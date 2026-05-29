import asyncio
import json
import os
import time
from pathlib import Path

from aiohttp import web


DEPLOYER_SECRET = (os.getenv("DEPLOYER_SECRET", "") or "").strip()
WORKDIR = Path(os.getenv("DEPLOY_WORKDIR", "/workspace")).resolve()
STATE_FILE = Path(os.getenv("DEPLOY_STATE_FILE", str(WORKDIR / "data" / "deployer_last_result.json"))).resolve()

_lock = asyncio.Lock()
_last_result = {"running": False, "ok": None, "log": ""}
EXPECTED_CONTAINERS = ["ksysha-bot", "ksysha-deployer", "ksysha-cloudflared"]


def _save_last_result() -> None:
    try:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = STATE_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(_last_result, ensure_ascii=False), encoding="utf-8")
        tmp.replace(STATE_FILE)
    except Exception:
        # Не падаем из-за проблем записи состояния.
        pass


def _consume_last_result() -> dict:
    if not STATE_FILE.exists():
        return {"running": False, "ok": None, "log": ""}
    try:
        payload = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        try:
            STATE_FILE.unlink(missing_ok=True)
        except Exception:
            pass
        if isinstance(payload, dict):
            return payload
    except Exception:
        pass
    return {"running": False, "ok": None, "log": ""}


async def _run_cmd(*args: str, cwd: Path | None = None) -> tuple[int, str]:
    proc = await asyncio.create_subprocess_exec(
        *args,
        cwd=str(cwd) if cwd else None,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    out, _ = await proc.communicate()
    return proc.returncode, (out or b"").decode("utf-8", errors="replace").strip()


async def _container_id(name: str) -> str:
    rc, out = await _run_cmd("docker", "inspect", "-f", "{{.Id}}", name)
    if rc != 0:
        return ""
    return out.strip()


async def _container_running(name: str) -> bool:
    rc, out = await _run_cmd("docker", "inspect", "-f", "{{.State.Running}}", name)
    return rc == 0 and out.strip().lower() == "true"


async def _wait_container_stable_running(name: str, *, checks: int = 3, interval_sec: float = 1.0) -> bool:
    ok_seq = 0
    for _ in range(max(1, checks * 2)):
        if await _container_running(name):
            ok_seq += 1
            if ok_seq >= checks:
                return True
        else:
            ok_seq = 0
        await asyncio.sleep(interval_sec)
    return False


async def _compose_recreate_with_retries(service: str, *, build: bool = False, retries: int = 2) -> tuple[bool, str]:
    logs: list[str] = []
    for attempt in range(1, retries + 1):
        args = ["docker", "compose", "up", "-d"]
        if build:
            args.append("--build")
        args += ["--force-recreate", "--no-deps", service]
        rc, out = await _run_cmd(*args, cwd=WORKDIR)
        logs.append(f"[{service}] attempt {attempt}: rc={rc}\n{out}")
        if rc == 0:
            return True, "\n".join(logs)

        if "is already in use" in out:
            target = f"ksysha-{service}" if service != "ksysha-bot" else "ksysha-bot"
            rm_rc, rm_out = await _run_cmd("docker", "rm", "-f", target)
            logs.append(f"[{service}] rm-conflict rc={rm_rc}\n{rm_out}")

        await asyncio.sleep(1.0)
    return False, "\n".join(logs)


async def _ensure_bot_recreated(old_id: str) -> tuple[bool, str]:
    logs: list[str] = []
    new_id = await _container_id("ksysha-bot")
    running = await _wait_container_stable_running("ksysha-bot", checks=2, interval_sec=0.8)
    if running and old_id and new_id and new_id != old_id:
        return True, "bot-check: recreated and running"

    logs.append(
        f"bot-check: not stable (running={running}, old_id={old_id[:12] if old_id else '-'}, new_id={new_id[:12] if new_id else '-'})"
    )
    rm_rc, rm_out = await _run_cmd("docker", "rm", "-f", "ksysha-bot")
    logs.append(f"bot-force-rm rc={rm_rc}\n{rm_out}")
    retry_ok, retry_log = await _compose_recreate_with_retries("ksysha-bot", build=False, retries=2)
    logs.append(retry_log)
    latest_id = await _container_id("ksysha-bot")
    latest_running = await _wait_container_stable_running("ksysha-bot", checks=2, interval_sec=0.8)
    recreated = bool(old_id and latest_id and latest_id != old_id)
    ok = bool(retry_ok and latest_running and recreated)
    logs.append(
        f"bot-check-after-retry: ok={ok}, running={latest_running}, recreated={recreated}, latest_id={latest_id[:12] if latest_id else '-'}"
    )
    return ok, "\n".join(logs)


async def _project_version() -> str:
    env_v = (os.getenv("PROJECT_VERSION", "") or "").strip()
    if env_v:
        return env_v
    try:
        rc, out = await _run_cmd("git", "rev-parse", "--short", "HEAD", cwd=WORKDIR)
        if rc == 0 and out:
            return out.strip()
    except Exception:
        pass
    return "unknown"


async def _container_networks(name: str) -> list[str]:
    rc, out = await _run_cmd(
        "docker",
        "inspect",
        "-f",
        "{{range $k, $_ := .NetworkSettings.Networks}}{{$k}} {{end}}",
        name,
    )
    if rc != 0 or not out:
        return []
    return [n.strip() for n in out.split() if n.strip()]


async def _containers_snapshot() -> dict:
    rc, out = await _run_cmd(
        "docker",
        "ps",
        "-a",
        "--format",
        "{{.Names}}|{{.Status}}|{{.Image}}|{{.State}}",
    )
    if rc != 0:
        return {
            "total": 0,
            "running": 0,
            "expected": EXPECTED_CONTAINERS,
            "missing_expected": EXPECTED_CONTAINERS[:],
            "details": [],
            "error": (out or "docker ps failed").strip(),
        }

    details = []
    by_name = {}
    for raw in out.splitlines():
        line = raw.strip()
        if not line:
            continue
        parts = line.split("|", 3)
        if len(parts) != 4:
            continue
        name, status, image, state = [p.strip() for p in parts]
        item = {
            "name": name,
            "status": status,
            "image": image,
            "state": state,
            "running": state.lower() == "running",
        }
        details.append(item)
        by_name[name] = item

    missing_expected = [name for name in EXPECTED_CONTAINERS if name not in by_name]
    return {
        "total": len(details),
        "running": sum(1 for i in details if i.get("running")),
        "expected": EXPECTED_CONTAINERS,
        "missing_expected": missing_expected,
        "details": [by_name[n] for n in EXPECTED_CONTAINERS if n in by_name],
    }


async def health(_: web.Request) -> web.Response:
    containers_live = await _containers_snapshot()
    payload = dict(_last_result)
    if "containers" not in payload:
        payload["containers"] = containers_live
    return web.json_response({
        "ok": True,
        "deploy": payload,
        "containers_live": containers_live,
        "expected_services": EXPECTED_CONTAINERS,
    })


async def _run_deploy() -> None:
    global _last_result
    started_ts = time.time()
    started_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(started_ts))
    old_id = ""
    version = "unknown"
    parts = []
    try:
        old_id = await _container_id("ksysha-bot")
        version = await _project_version()
        _last_result = {
            "running": True,
            "ok": None,
            "log": "",
            "started_at": started_iso,
            "version": version,
            "old_container_id": old_id,
            "containers": await _containers_snapshot(),
        }
        _save_last_result()

        # 1) Пересобираем и пересоздаем ksysha-bot (как и раньше).
        bot_up_ok, bot_up_log = await _compose_recreate_with_retries("ksysha-bot", build=True, retries=3)
        parts.append(bot_up_log)
        bot_recreate_ok, bot_recreate_log = await _ensure_bot_recreated(old_id)
        parts.append(bot_recreate_log)

        # 2) Пересоздаем cloudflared, чтобы туннель поднимался на свежем цикле.
        old_cf_id = await _container_id("ksysha-cloudflared")
        cf_ok, cf_log = await _compose_recreate_with_retries("cloudflared", build=False, retries=3)
        parts.append(cf_log)
        new_cf_id = await _container_id("ksysha-cloudflared")

        # В редких случаях compose "успешно" отрабатывает, но контейнер не меняется.
        # Тогда принудительно перезапускаем cloudflared.
        if old_cf_id and new_cf_id and old_cf_id == new_cf_id:
            cf_restart_proc = await asyncio.create_subprocess_exec(
                "docker",
                "restart",
                "ksysha-cloudflared",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            cf_restart_out, _ = await cf_restart_proc.communicate()
            parts.append((cf_restart_out or b"").decode("utf-8", errors="replace"))

        # Гарантируем, что bot видит deployer по DNS после recreate:
        # подключаем bot к тем же docker-сетям, где находится deployer.
        deployer_networks = await _container_networks("ksysha-deployer")
        for network_name in deployer_networks:
            net_proc = await asyncio.create_subprocess_exec(
                "docker",
                "network",
                "connect",
                network_name,
                "ksysha-bot",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            net_out, _ = await net_proc.communicate()
            txt = (net_out or b"").decode("utf-8", errors="replace").strip()
            if txt and "already exists" not in txt:
                parts.append(f"network-connect {network_name}: {txt}")

        # 3) Планируем отложенный self-restart deployer, чтобы не оборвать
        # текущее выполнение до записи финального статуса.
        self_restart_proc = await asyncio.create_subprocess_exec(
            "sh",
            "-lc",
            "sleep 2; docker restart ksysha-deployer >/tmp/ksysha_deployer_self_restart.log 2>&1",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        parts.append("self-restart deployer scheduled (delay 2s)")

        # self_restart_proc запускается в фоне и не имеет финального returncode
        # в рамках этого запроса, поэтому не учитываем его в основном результате.
        bot_running = await _wait_container_stable_running("ksysha-bot", checks=2, interval_sec=1.0)
        cf_running = await _wait_container_stable_running("ksysha-cloudflared", checks=2, interval_sec=1.0)
        if not bot_running:
            parts.append("post-check failed: ksysha-bot is not running")
        if not cf_running:
            parts.append("post-check failed: ksysha-cloudflared is not running")

        ok = bool(bot_up_ok and bot_recreate_ok and cf_ok and bot_running and cf_running)
    except Exception as e:
        parts.append(f"internal_error: {e}")
        ok = False
    finally:
        ended_ts = time.time()
        ended_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ended_ts))
        duration_ms = int((ended_ts - started_ts) * 1000)
        new_id = await _container_id("ksysha-bot")
        recreated = bool(ok and old_id and new_id and old_id != new_id)
        _last_result = {
            "running": False,
            "ok": ok,
            "log": "\n".join(parts)[-12000:],
            "started_at": started_iso,
            "finished_at": ended_iso,
            "duration_ms": duration_ms,
            "version": version,
            "old_container_id": old_id,
            "new_container_id": new_id,
            "recreated": recreated,
            "containers": await _containers_snapshot(),
        }
        _save_last_result()
        if _lock.locked():
            _lock.release()


async def deploy(request: web.Request) -> web.Response:
    provided = (request.headers.get("X-Deploy-Secret") or "").strip()
    if not DEPLOYER_SECRET or provided != DEPLOYER_SECRET:
        return web.json_response({"ok": False, "error": "forbidden"}, status=403)

    if _lock.locked():
        return web.json_response({"ok": False, "error": "deploy_in_progress"}, status=409)

    await _lock.acquire()
    asyncio.create_task(_run_deploy())
    return web.json_response({"ok": True, "started": True})


def create_app() -> web.Application:
    app = web.Application()
    app.router.add_get("/health", health)
    app.router.add_post("/deploy", deploy)
    return app


if __name__ == "__main__":
    _last_result = _consume_last_result()
    web.run_app(create_app(), host="0.0.0.0", port=25100)

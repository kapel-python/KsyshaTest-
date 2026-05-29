#!/usr/bin/env python3
"""
Unified prod smoke checks.
Combines contract checks and main-page smoke checks with selectable modes.
"""

import argparse
import asyncio
import os
from typing import List, Tuple

import aiohttp


BASE_URL = os.getenv("BASE_URL", "http://127.0.0.1:25086").rstrip("/")
API_KEY = (os.getenv("API_SECRET_KEY") or os.getenv("API_KEY") or "").strip()

Result = Tuple[bool, str, str]


def _ok(name: str, info: str = "") -> Result:
    return (True, name, info)


def _fail(name: str, info: str = "") -> Result:
    return (False, name, info)


async def _json(session: aiohttp.ClientSession, method: str, path: str, **kwargs):
    async with session.request(method, BASE_URL + path, timeout=aiohttp.ClientTimeout(total=20), **kwargs) as r:
        ct = r.headers.get("Content-Type", "")
        data = None
        if "application/json" in ct:
            try:
                data = await r.json()
            except Exception:
                data = None
        else:
            _ = await r.text()
        return r.status, r.headers, data


async def _fetch_text(session: aiohttp.ClientSession, path: str):
    async with session.get(BASE_URL + path, timeout=aiohttp.ClientTimeout(total=20)) as resp:
        return resp.status, resp.headers, await resp.text()


async def run_contract_checks(results: List[Result]) -> None:
    headers_key = {"X-Api-Key": API_KEY} if API_KEY else {}
    async with aiohttp.ClientSession() as s:
        st, h, _ = await _json(s, "GET", "/")
        results.append(_ok("GET / == 200", f"HTTP {st}") if st == 200 else _fail("GET / == 200", f"HTTP {st}"))
        csp = h.get("Content-Security-Policy", "")
        results.append(_ok("CSP header present") if csp else _fail("CSP header present", "missing"))
        xcto = h.get("X-Content-Type-Options", "").lower()
        results.append(_ok("X-Content-Type-Options nosniff") if xcto == "nosniff" else _fail("X-Content-Type-Options nosniff", xcto))

        st, _, body = await _json(s, "GET", "/api/info")
        results.append(_ok("/api/info without key forbidden", f"HTTP {st}") if st in (401, 403) else _fail("/api/info without key forbidden", f"HTTP {st}"))
        if isinstance(body, dict):
            leak = "API_SECRET_KEY" in body
            results.append(_ok("/api/info no secret leakage contract") if not leak else _fail("/api/info no secret leakage contract", "contains API_SECRET_KEY"))

        if API_KEY:
            st, _, body = await _json(s, "GET", "/api/info", headers=headers_key)
            ok_info = st == 200 and isinstance(body, dict)
            results.append(_ok("/api/info with key works", f"HTTP {st}") if ok_info else _fail("/api/info with key works", f"HTTP {st}"))
            st2, _, body2 = await _json(s, "GET", "/api/all?v=creator", headers=headers_key)
            all_ok = st2 in (200, 403) and isinstance(body2, dict)
            results.append(_ok("/api/all trusted contract stable", f"HTTP {st2}") if all_ok else _fail("/api/all trusted contract stable", f"HTTP {st2}"))

        st, _, body = await _json(s, "POST", "/api/token_auth", json={"token": ""}, headers=headers_key)
        token_auth_ok = (st < 500) and isinstance(body, dict) and ("ok" in body or "error" in body)
        results.append(_ok("token_auth safe contract (non-5xx + json)", f"HTTP {st}") if token_auth_ok else _fail("token_auth safe contract (non-5xx + json)", f"HTTP {st}"))

        st, _, _ = await _json(s, "GET", "/api/admin/health_metrics")
        results.append(_ok("admin health metrics without key forbidden", f"HTTP {st}") if st in (401, 403) else _fail("admin health metrics without key forbidden", f"HTTP {st}"))

        async with s.options(BASE_URL + "/api/token_auth", timeout=aiohttp.ClientTimeout(total=20)) as r:
            st = r.status
            acao = r.headers.get("Access-Control-Allow-Origin", "")
        results.append(_ok("OPTIONS /api/token_auth == 204", f"HTTP {st}") if st == 204 else _fail("OPTIONS /api/token_auth == 204", f"HTTP {st}"))
        results.append(_ok("ACAO header exists", acao) if acao else _fail("ACAO header exists", "missing"))

        async with s.get(BASE_URL + "/ws/site?v=creator", timeout=aiohttp.ClientTimeout(total=20)) as r:
            st = r.status
        results.append(_ok("GET /ws/site plain request protected", f"HTTP {st}") if st in (400, 426) else _fail("GET /ws/site plain request protected", f"HTTP {st}"))


async def run_main_page_checks(results: List[Result]) -> None:
    async with aiohttp.ClientSession() as s:
        status, headers, html = await _fetch_text(s, "/")
        results.append(_ok("GET / main page available", f"HTTP {status}") if status == 200 else _fail("GET / main page available", f"HTTP {status}"))

        leak = "{{API_SECRET_KEY}}" in html
        results.append(_ok("No raw API secret placeholder in HTML") if not leak else _fail("No raw API secret placeholder in HTML", "placeholder found"))

        has_partner_marker = "role === 'partner'" in html or "role === \"partner\"" in html
        results.append(_ok("Partner role marker exists in frontend script") if has_partner_marker else _fail("Partner role marker exists in frontend script", "marker not found"))

        csp = headers.get("Content-Security-Policy", "")
        xcto = headers.get("X-Content-Type-Options", "")
        results.append(_ok("Main page CSP header present") if csp else _fail("Main page CSP header present", "missing"))
        results.append(_ok("Main page X-Content-Type-Options nosniff") if xcto.lower() == "nosniff" else _fail("Main page X-Content-Type-Options nosniff", xcto or "missing"))


async def run_suite(mode: str) -> int:
    results: List[Result] = []
    if mode in ("quick", "all"):
        await run_main_page_checks(results)
    if mode in ("contract", "all"):
        await run_contract_checks(results)

    passed = sum(1 for ok, _, _ in results if ok)
    total = len(results)
    print(f"PROD SMOKE [{mode}]")
    for ok, name, info in results:
        mark = "PASS" if ok else "FAIL"
        print(f"[{mark}] {name}" + (f" -> {info}" if info else ""))
    print(f"RESULT: {passed}/{total}")
    return 0 if passed == total else 1


def parse_args():
    parser = argparse.ArgumentParser(description="Unified production smoke checks")
    parser.add_argument("--mode", choices=("quick", "contract", "all"), default="all", help="quick=main page checks, contract=API contracts, all=both")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    return asyncio.run(run_suite(args.mode))


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130)

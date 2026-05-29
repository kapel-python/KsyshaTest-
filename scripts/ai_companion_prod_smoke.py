#!/usr/bin/env python3
import argparse
import json
import statistics
import sys
import time
from typing import Any, Dict, List

import requests


def _percentile_ms(values: List[float], p: float) -> float:
    if not values:
        return 0.0
    if len(values) == 1:
        return values[0]
    k = (len(values) - 1) * p
    f = int(k)
    c = min(f + 1, len(values) - 1)
    if f == c:
        return values[f]
    return values[f] + (values[c] - values[f]) * (k - f)


def main() -> int:
    parser = argparse.ArgumentParser(description="Low-noise production smoke test for /api/ai_companion")
    parser.add_argument("--base-url", default="http://127.0.0.1:25086", help="API base URL")
    parser.add_argument("--path", default="/api/ai_companion", help="Companion endpoint path")
    parser.add_argument("--api-key", default="", help="X-API-Key value (optional)")
    parser.add_argument("--cookie", default="", help="Cookie header value (optional)")
    parser.add_argument("--requests", type=int, default=6, help="Number of requests (recommended: 5-8)")
    parser.add_argument("--pause-sec", type=float, default=1.8, help="Pause between requests")
    parser.add_argument("--timeout-sec", type=float, default=18.0, help="HTTP timeout per request")
    parser.add_argument("--max-timeout-ratio", type=float, default=0.25, help="Fail if timeout ratio exceeds this")
    parser.add_argument("--max-5xx-ratio", type=float, default=0.10, help="Fail if 5xx ratio exceeds this")
    args = parser.parse_args()

    if args.requests <= 0:
        print("requests must be > 0", file=sys.stderr)
        return 2

    url = args.base_url.rstrip("/") + args.path
    headers = {"Content-Type": "application/json"}
    if args.api_key:
        headers["X-API-Key"] = args.api_key
    if args.cookie:
        headers["Cookie"] = args.cookie

    payload = {
        "message": "Проверь кратко наш прогресс за неделю и предложи 2 мягких шага.",
        "history": [],
    }

    latencies_ms: List[float] = []
    statuses: List[int] = []
    timeouts = 0
    err_5xx = 0
    other_errors = 0

    print(f"Smoke target: {url}")
    print(f"Total requests: {args.requests}, pause={args.pause_sec}s, timeout={args.timeout_sec}s")

    for i in range(1, args.requests + 1):
        started = time.monotonic()
        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=args.timeout_sec)
            elapsed_ms = (time.monotonic() - started) * 1000.0
            latencies_ms.append(elapsed_ms)
            statuses.append(resp.status_code)
            if 500 <= resp.status_code <= 599:
                err_5xx += 1
            print(f"#{i}: status={resp.status_code} latency_ms={elapsed_ms:.1f}")
        except requests.exceptions.ReadTimeout:
            elapsed_ms = (time.monotonic() - started) * 1000.0
            timeouts += 1
            print(f"#{i}: timeout latency_ms={elapsed_ms:.1f}")
        except Exception as exc:
            other_errors += 1
            print(f"#{i}: error={type(exc).__name__} details={exc}")

        if i < args.requests:
            time.sleep(max(0.0, args.pause_sec))

    ok_count = len(latencies_ms)
    timeout_ratio = timeouts / float(args.requests)
    err_5xx_ratio = err_5xx / float(args.requests)

    p50 = _percentile_ms(sorted(latencies_ms), 0.50) if latencies_ms else 0.0
    p95 = _percentile_ms(sorted(latencies_ms), 0.95) if latencies_ms else 0.0
    avg = statistics.mean(latencies_ms) if latencies_ms else 0.0

    summary: Dict[str, Any] = {
        "url": url,
        "requests": args.requests,
        "ok_count": ok_count,
        "timeouts": timeouts,
        "errors_5xx": err_5xx,
        "other_errors": other_errors,
        "timeout_ratio": round(timeout_ratio, 4),
        "errors_5xx_ratio": round(err_5xx_ratio, 4),
        "latency_ms": {
            "avg": round(avg, 2),
            "p50": round(p50, 2),
            "p95": round(p95, 2),
        },
        "status_codes": statuses,
    }

    print("SUMMARY:")
    print(json.dumps(summary, ensure_ascii=False))

    if timeout_ratio > args.max_timeout_ratio:
        print("FAIL: timeout ratio exceeded threshold", file=sys.stderr)
        return 1
    if err_5xx_ratio > args.max_5xx_ratio:
        print("FAIL: 5xx ratio exceeded threshold", file=sys.stderr)
        return 1
    if ok_count == 0:
        print("FAIL: no successful responses", file=sys.stderr)
        return 1

    print("PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Load test of SCICP through the load balancer (report sections 5.2, 5.3 and 9).

Signs in once as a fleet manager, then N virtual users send requests back to back for D seconds,
cycling through five read endpoints that touch every service and both databases:

    /api/v1/dashboard/overview   C1 (cached 30 s per tenant; calls C2 and C3 when the cache is cold)
    /api/v1/dashboard/map        C1 (MongoDB aggregation, cached 10 s)
    /api/v1/billing/invoice      C1 (MySQL counts + MongoDB count, not cached)
    /c2/api/v1/vehicles          C2 (MySQL)
    /c3/api/v1/alerts            C3 (MySQL view, newest 20 open alerts)

For each concurrency level it prints the throughput, the latency percentiles, the errors and how the
requests were spread over the replicas (nginx's X-Upstream header). Run it where it can reach the
web container, for example on the EC2 instance from a one-off container on the compose network:

    docker run --rm --network scicp_default -v "$PWD":/repo:ro scicp/c1-api:local \
        python /repo/tools/loadtest.py --base http://web:8080 --duration 30 --concurrency 1,8,32

Only reads: nothing is written except the one sign-in row of the audit log.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import time
from collections import Counter, defaultdict
from typing import Dict, List

import httpx

ENDPOINTS = [
    ("C1 overview", "/api/v1/dashboard/overview"),
    ("C1 map", "/api/v1/dashboard/map"),
    ("C1 billing", "/api/v1/billing/invoice"),
    ("C2 vehicles", "/c2/api/v1/vehicles?size=100"),
    ("C3 alerts", "/c3/api/v1/alerts?status=OPEN&size=20"),
]


def percentile(values: List[float], q: float) -> float:
    if not values:
        return float("nan")
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round(q / 100 * (len(ordered) - 1)))))
    return ordered[index]


async def run_level(base: str, token: str, concurrency: int, duration: float) -> Dict:
    latencies: Dict[str, List[float]] = defaultdict(list)
    errors: Counter = Counter()
    upstreams: Dict[str, Counter] = defaultdict(Counter)
    stop_at = time.perf_counter() + duration
    headers = {"Authorization": f"Bearer {token}"}
    limits = httpx.Limits(max_connections=concurrency, max_keepalive_connections=concurrency)

    async with httpx.AsyncClient(base_url=base, headers=headers, timeout=30.0, limits=limits) as client:
        async def user(offset: int) -> None:
            i = offset
            while time.perf_counter() < stop_at:
                name, path = ENDPOINTS[i % len(ENDPOINTS)]
                i += 1
                started = time.perf_counter()
                try:
                    response = await client.get(path)
                    elapsed = (time.perf_counter() - started) * 1000
                    if response.status_code >= 400:
                        errors[f"{name} {response.status_code}"] += 1
                        continue
                    latencies[name].append(elapsed)
                    upstreams[name.split()[0]][response.headers.get("x-upstream", "?")] += 1
                except httpx.HTTPError as exc:
                    errors[f"{name} {exc.__class__.__name__}"] += 1

        started = time.perf_counter()
        await asyncio.gather(*(user(n) for n in range(concurrency)))
        wall = time.perf_counter() - started

    every = [v for values in latencies.values() for v in values]
    return {
        "concurrency": concurrency, "seconds": round(wall, 1), "requests": len(every),
        "errors": sum(errors.values()), "errorKinds": dict(errors),
        "rps": round(len(every) / wall, 1),
        "p50": round(percentile(every, 50), 1), "p95": round(percentile(every, 95), 1),
        "p99": round(percentile(every, 99), 1),
        "perEndpoint": {name: {"n": len(v), "p50": round(percentile(v, 50), 1), "p95": round(percentile(v, 95), 1),
                               "mean": round(statistics.fmean(v), 1) if v else None}
                        for name, v in latencies.items()},
        "replicas": {service: dict(counter) for service, counter in upstreams.items()},
    }


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--base", default="http://localhost:8080")
    parser.add_argument("--email", default="maria@bayshuttle.example")
    parser.add_argument("--password", default="Passw0rd!demo")
    parser.add_argument("--duration", type=float, default=30.0)
    parser.add_argument("--concurrency", default="1,8,32")
    parser.add_argument("--json", help="also write the results to this file")
    args = parser.parse_args()

    async with httpx.AsyncClient(base_url=args.base, timeout=30.0) as client:
        login = await client.post("/api/v1/auth/login", json={"email": args.email, "password": args.password})
        login.raise_for_status()
        token = login.json()["accessToken"]

    results = []
    for level in [int(x) for x in args.concurrency.split(",") if x.strip()]:
        result = await run_level(args.base, token, level, args.duration)
        results.append(result)
        print(f"\n{level} virtual users, {result['seconds']} s: {result['requests']} requests, "
              f"{result['rps']} req/s, p50 {result['p50']} ms, p95 {result['p95']} ms, p99 {result['p99']} ms, "
              f"{result['errors']} errors {result['errorKinds'] or ''}")
        for name, stats in result["perEndpoint"].items():
            print(f"    {name:12s} n={stats['n']:6d}  p50 {stats['p50']:7.1f} ms  p95 {stats['p95']:7.1f} ms")
        for service, spread in result["replicas"].items():
            print(f"    {service} replicas: " + ", ".join(f"{k} x{v}" for k, v in sorted(spread.items())))
    if args.json:
        with open(args.json, "w") as handle:
            json.dump(results, handle, indent=2)


if __name__ == "__main__":
    asyncio.run(main())

"""Demo tool: watch C1's WebSocket as a user, to see C3's alert.created / alert.updated arrive.

    c3/.venv/bin/python c3/tools/watch_ws.py                      # Maria (tenant B), 120 s
    c3/.venv/bin/python c3/tools/watch_ws.py --email anh@example.com --seconds 300

It logs in through nginx (POST /api/v1/auth/login), opens ws://<host>/ws, sends the auth
message C1 expects ({"type": "auth", "token": ...}), answers C1's pings and prints every
other message with the time it arrived.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime

import httpx
import websockets


async def main() -> int:
    parser = argparse.ArgumentParser(description="Print the live messages C1 pushes to a user.")
    parser.add_argument("--base", default="http://localhost:8080", help="the web app (nginx)")
    parser.add_argument("--email", default="maria@bayshuttle.example")
    parser.add_argument("--password", default="Passw0rd!demo")
    parser.add_argument("--seconds", type=float, default=120)
    args = parser.parse_args()
    async with httpx.AsyncClient(base_url=args.base, timeout=10) as client:
        login = await client.post("/api/v1/auth/login", json={"email": args.email, "password": args.password})
    if login.status_code != 200:
        print(f"login failed: {login.status_code} {login.text}", file=sys.stderr)
        return 1
    token = login.json()["accessToken"]
    url = args.base.replace("http://", "ws://").replace("https://", "wss://").rstrip("/") + "/ws"
    print(f"{datetime.now():%H:%M:%S} listening on {url} as {args.email} for {args.seconds:.0f} s", flush=True)
    async with websockets.connect(url) as ws:
        await ws.send(json.dumps({"type": "auth", "token": token}))
        loop = asyncio.get_running_loop()
        end = loop.time() + args.seconds
        while (remaining := end - loop.time()) > 0:
            try:
                message = json.loads(await asyncio.wait_for(ws.recv(), timeout=remaining))
            except asyncio.TimeoutError:
                break
            if message.get("type") == "ping":
                await ws.send(json.dumps({"type": "pong"}))
                continue
            print(f"{datetime.now():%H:%M:%S} {json.dumps(message)}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

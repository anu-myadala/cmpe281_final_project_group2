"""Demo tool: call GET /internal/clips/{clipId} the way the C4 worker does.

    docker compose -f docker-compose.yml -f docker-compose.c2.yml exec c2 python tools/fetch_clip.py          # newest clip
    docker compose -f docker-compose.yml -f docker-compose.c2.yml exec c2 python tools/fetch_clip.py c41e8d2b
    ... python tools/fetch_clip.py --no-token      # shows the 401 a caller without the service token gets

It prints the status, the headers C2 adds and the WAV format of the answer.
"""
from __future__ import annotations

import argparse
import asyncio
import io
import os
import sys
import time
import uuid
import wave
from pathlib import Path

import httpx
import jwt

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


async def newest_clip() -> str:
    from sqlalchemy import select
    from c2_service.config import Settings
    from c2_service.db import make_engine, make_session_factory
    from c2_service.models import Clip
    engine = make_engine(Settings().database_url)
    async with make_session_factory(engine)() as session:
        clip_id = (await session.execute(select(Clip.clip_id).where(Clip.state == "STORED")
                                         .order_by(Clip.uploaded_at.desc()).limit(1))).scalar_one_or_none()
    await engine.dispose()
    if clip_id is None:
        raise SystemExit("no clip stored yet")
    return clip_id


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("clip_id", nargs="?")
    parser.add_argument("--no-token", action="store_true")
    parser.add_argument("--url", default=os.environ.get("C2_URL", "http://127.0.0.1:8002/api/v1"))
    args = parser.parse_args()
    clip_id = args.clip_id or asyncio.run(newest_clip())
    headers = {}
    if not args.no_token:
        now = int(time.time())
        token = jwt.encode({"sub": "c4-demo", "role": "SERVICE", "iat": now, "exp": now + 600, "jti": uuid.uuid4().hex},
                           os.environ["SERVICE_TOKEN_SECRET"], algorithm="HS256")
        headers["Authorization"] = f"Bearer {token}"
    r = httpx.get(f"{args.url}/internal/clips/{clip_id}", headers=headers, timeout=10)
    print(f"GET /internal/clips/{clip_id} -> HTTP {r.status_code} {r.headers.get('content-type')}")
    for h in ("x-tenant-id", "x-vehicle-id", "x-captured-at"):
        if h in r.headers:
            print(f"  {h}: {r.headers[h]}")
    if r.status_code == 200:
        with wave.open(io.BytesIO(r.content)) as w:
            print(f"  WAV: {w.getnchannels()} channel, {w.getframerate()} Hz, {w.getsampwidth() * 8}-bit, "
                  f"{w.getnframes() / w.getframerate():.1f} s, {len(r.content)} bytes")
    else:
        print(f"  {r.text[:200]}")


if __name__ == "__main__":
    main()

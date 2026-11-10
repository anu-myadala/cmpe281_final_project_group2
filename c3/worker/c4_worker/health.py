"""GET /healthz on :8010 -> {status, modelVersion, queueDepth, processed, dropped, errors} (stdlib asyncio)."""
from __future__ import annotations

import asyncio
import json
from typing import Any, Callable, Dict


async def serve_health(snapshot: Callable[[], Dict[str, Any]], host: str = "0.0.0.0", port: int = 8010):
    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            request = await asyncio.wait_for(reader.readline(), timeout=5)
            while (await asyncio.wait_for(reader.readline(), timeout=5)) not in (b"\r\n", b"\n", b""):
                pass
            parts = request.decode(errors="replace").split()
            if len(parts) >= 2 and parts[0] in ("GET", "HEAD") and parts[1].split("?")[0] == "/healthz":
                status, body = "200 OK", json.dumps(snapshot()).encode()
            else:
                status, body = "404 Not Found", b'{"error":{"code":"NOT_FOUND","message":"GET /healthz"}}'
            head = (f"HTTP/1.1 {status}\r\nContent-Type: application/json\r\nContent-Length: {len(body)}\r\n"
                    "Connection: close\r\n\r\n").encode()
            writer.write(head + (b"" if parts and parts[0] == "HEAD" else body))
            await writer.drain()
        except (asyncio.TimeoutError, ConnectionError):
            pass
        finally:
            writer.close()

    return await asyncio.start_server(handle, host, port)

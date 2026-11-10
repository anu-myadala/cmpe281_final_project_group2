"""Service token for C1, C2 and C3: HS256 with SERVICE_TOKEN_SECRET, role SERVICE (as stubs/worker.py)."""
from __future__ import annotations

import time
import uuid
from typing import Callable, Dict, Optional

import jwt


class ServiceToken:
    def __init__(self, secret: str, sub: str = "c4-worker", ttl_s: int = 24 * 3600,
                 clock: Callable[[], float] = time.time) -> None:
        self.secret, self.sub, self.ttl_s, self.clock = secret, sub, ttl_s, clock
        self._token: Optional[str] = None
        self._exp = 0

    def get(self) -> str:
        now = int(self.clock())
        if self._token is None or now > self._exp - 300:  # renewed 5 min before it expires
            self._exp = now + self.ttl_s
            self._token = jwt.encode({"sub": self.sub, "role": "SERVICE", "iat": now, "exp": self._exp,
                                      "jti": uuid.uuid4().hex}, self.secret, algorithm="HS256")
        return self._token

    def headers(self) -> Dict[str, str]:
        return {"Authorization": f"Bearer {self.get()}"}

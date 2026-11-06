"""C1Client: the calls C3 makes to C1's REST API (httpx).

For now that is one call: the feedback label for retraining (Part III, Table 82,
"C1 feedback API"; FR-C4-04). When an alert is resolved C3 posts CONFIRMED, and when
it is marked a false alarm, FALSE_ALARM, to POST {C1_BASE_URL}/events/{eventRef}/feedback,
with the acting user's own token, so C1 records who labeled it. As in the stub it is
best effort: any failure is logged and the alert action still succeeds. `transport`
lets the tests swap in httpx.MockTransport.
"""
from __future__ import annotations

import logging
from typing import Optional

import httpx

log = logging.getLogger("scicp.c3.c1client")


class C1Client:
    def __init__(self, base_url: str, timeout: float = 5.0, transport: Optional[httpx.AsyncBaseTransport] = None) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.transport = transport

    async def post_feedback(self, event_ref: str, label: str, user_token: str) -> bool:
        """True when C1 accepted the label; False (logged) on any error or refusal, e.g. a duplicate (BR-11)."""
        try:
            async with httpx.AsyncClient(timeout=self.timeout, transport=self.transport) as client:
                response = await client.post(f"{self.base_url}/events/{event_ref}/feedback", json={"label": label},
                                             headers={"Authorization": f"Bearer {user_token}"})
        except Exception as exc:  # noqa: BLE001 - best effort
            log.info("feedback %s on %s not sent: %s", label, event_ref, exc)
            return False
        if response.status_code >= 400:
            log.info("feedback %s on %s not recorded: %s", label, event_ref, response.status_code)
            return False
        return True

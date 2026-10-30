"""VehicleClient (C2) and AlertClient (C3): internal REST clients that carry
a service token and give up after 5 s. Any failure raises ClientError so the
services can fall back to their caches."""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import httpx

from ..config import Settings
from ..core.security import TokenService

log = logging.getLogger("scicp.clients")


class ClientError(Exception):
    """The neighbor did not answer in time or answered with an error."""


class _ServiceClient:
    def __init__(self, base_url: str, settings: Settings, tokens: TokenService, name: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.settings = settings
        self.tokens = tokens
        self.name = name
        self._token: Optional[str] = None

    def _headers(self) -> Dict[str, str]:
        if self._token is None:
            self._token = self.tokens.issue_service_token()
        return {"Authorization": f"Bearer {self._token}"}

    async def get(self, path: str, params: Optional[Dict[str, Any]] = None) -> Any:
        url = f"{self.base_url}{path}"
        try:
            async with httpx.AsyncClient(timeout=self.settings.client_timeout_seconds) as client:
                response = await client.get(url, params=params, headers=self._headers())
                if response.status_code == 401:
                    # The service token may have expired: mint a new one once.
                    self._token = self.tokens.issue_service_token()
                    response = await client.get(url, params=params, headers=self._headers())
        except httpx.HTTPError as exc:
            log.warning("%s unreachable: %s %s", self.name, url, exc)
            raise ClientError(f"{self.name} unreachable") from exc
        if response.status_code >= 400:
            log.warning("%s answered %s for %s", self.name, response.status_code, url)
            raise ClientError(f"{self.name} answered {response.status_code}")
        try:
            return response.json()
        except ValueError as exc:
            raise ClientError(f"{self.name} answered a non-JSON body") from exc


class VehicleClient(_ServiceClient):
    """GET /vehicles?tenantId and GET /vehicles/{vehicleId}/devices on C2."""

    def __init__(self, settings: Settings, tokens: TokenService) -> None:
        super().__init__(settings.c2_base_url, settings, tokens, "C2")

    async def list(self, tenant_id: int) -> List[Dict[str, Any]]:
        body = await self.get("/vehicles", {"tenantId": tenant_id, "size": 100})
        items = body.get("items", body) if isinstance(body, dict) else body
        return list(items or [])

    async def device_summary(self, vehicle_id: str, tenant_id: Optional[int] = None) -> List[Dict[str, Any]]:
        params = {"tenantId": tenant_id} if tenant_id is not None else None
        body = await self.get(f"/vehicles/{vehicle_id}/devices", params)
        return list(body.get("items", body) if isinstance(body, dict) else body)


class AlertClient(_ServiceClient):
    """GET /alerts?status=OPEN&tenantId&limit, GET /alerts/{id}, GET /alerts/map on C3."""

    def __init__(self, settings: Settings, tokens: TokenService) -> None:
        super().__init__(settings.c3_base_url, settings, tokens, "C3")

    async def open_alerts(self, tenant_id: int, limit: int = 10) -> tuple:
        body = await self.get("/alerts", {"status": "OPEN", "tenantId": tenant_id, "limit": limit,
                                          "size": limit, "sort": "createdAt"})
        items = list(body.get("items", []))
        total = int(body.get("total", len(items)))
        return items, total

    async def get_alert(self, alert_id: int, tenant_id: Optional[int] = None) -> Dict[str, Any]:
        params = {"tenantId": tenant_id} if tenant_id is not None else None
        return await self.get(f"/alerts/{alert_id}", params)

    async def map_points(self, tenant_id: int) -> List[Dict[str, Any]]:
        body = await self.get("/alerts/map", {"status": "OPEN", "tenantId": tenant_id})
        return list(body.get("items", body) if isinstance(body, dict) else body)

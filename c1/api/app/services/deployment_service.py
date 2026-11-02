"""DeploymentService: closes the loop between the model registry and the C4
classifier worker.

  1. ModelRegistryService.activate publishes model.activated (EventBus + MQTT).
  2. The worker asks GET /internal/models/active for the version, its SHA-256 and a
     presigned download URL, fetches the artifact and verifies the digest.
  3. It reports POST /internal/worker/status {workerId, modelVersion, sha256, state}.
  4. C1 keeps the newest report per worker (memory, mirrored in the MongoDB
     collection worker_status so a restart of the API does not lose it), publishes
     model.deployed to the staff room, and GET /models/{id}/deployment shows it.

sha256Verified is decided here, not by the worker: the reported digest must equal
the one recorded in model_version when the upload was completed.
"""
from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from ..container import AppContainer
from ..core.timeutil import iso, parse_iso, utcnow
from ..errors import NotFound, Validation
from ..models import AiModel
from ..repos import ModelRepo, ModelVersionRepo
from ..schemas import WorkerStatusBody

log = logging.getLogger("scicp.deployment")

WORKER_COLLECTION = "worker_status"
DEFAULT_MODEL_NAME = "sound-classifier"
# A worker that has not reported for this long is shown as stale (it polls every 30 s).
STALE_AFTER = timedelta(seconds=90)


class DeploymentService:
    def __init__(self, container: AppContainer, session: AsyncSession) -> None:
        self.c = container
        self.session = session

    async def _model(self, model_id: Optional[int], name: Optional[str] = None) -> AiModel:
        model: Optional[AiModel] = None
        if model_id is not None:
            model = await ModelRepo.get(self.session, model_id)
        elif name:
            model = await ModelRepo.by_name(self.session, name)
        else:
            model = await ModelRepo.by_name(self.session, DEFAULT_MODEL_NAME)
            if model is None:
                models = await ModelRepo.list(self.session)
                model = models[0] if models else None
        if model is None:
            raise NotFound("Model not found")
        return model

    # -- for the worker (service token) ----------------------------------------------------
    async def active_artifact(self, model_id: Optional[int], name: Optional[str]) -> Dict[str, Any]:
        model = await self._model(model_id, name)
        active = await ModelVersionRepo.active(self.session, model.model_id)
        if active is None:
            raise NotFound("The model has no ACTIVE version")
        url = await self.c.storage.presign_get(active.artifact_key)
        return {"modelId": model.model_id, "name": model.name, "version": active.version,
                "tag": f"{model.name}-{active.version}", "sha256": active.sha256, "sizeBytes": active.size_bytes,
                "downloadUrl": url, "expiresInSeconds": self.c.settings.presign_seconds,
                "activatedAt": iso(active.activated_at)}

    async def report(self, body: WorkerStatusBody) -> Dict[str, Any]:
        model = await self._model(body.modelId)
        verified = False
        if body.modelVersion:
            row = await ModelVersionRepo.find(self.session, model.model_id, body.modelVersion)
            verified = (body.state == "READY" and row is not None and bool(row.sha256) and bool(body.sha256)
                        and row.sha256.lower() == (body.sha256 or "").lower())
        loaded_at = iso(parse_iso(body.loadedAt, "loadedAt")) if body.loadedAt else None
        entry = {"workerId": body.workerId, "modelId": model.model_id, "modelName": model.name,
                 "modelVersion": body.modelVersion, "state": body.state, "loadedAt": loaded_at,
                 "sha256": body.sha256.lower() if body.sha256 else None, "sha256Verified": verified,
                 "detail": body.detail, "lastHeartbeatAt": iso(utcnow())}
        previous = self.c.workers.get(body.workerId)
        self.c.workers[body.workerId] = entry
        try:
            await self.c.mongo[WORKER_COLLECTION].replace_one({"_id": body.workerId}, {**entry, "_id": body.workerId},
                                                              upsert=True)
        except Exception as exc:  # noqa: BLE001 - the in-memory copy is the source for the page
            log.warning("worker_status not saved in MongoDB: %s", exc)
        if previous is None or (previous.get("state"), previous.get("modelVersion")) != (entry["state"],
                                                                                      entry["modelVersion"]):
            log.info("worker %s: %s %s (sha256 verified: %s)", body.workerId, entry["state"], entry["modelVersion"],
                     verified)
        # Every report goes to the staff room; the page also refetches every 10 s.
        await self.c.bus.publish("model.deployed", dict(entry))
        return entry

    # -- for the web app ------------------------------------------------------------------------
    async def _load_saved(self) -> None:
        """After an API restart, start from the reports saved in MongoDB."""
        if self.c.workers:
            return
        try:
            docs = await self.c.mongo[WORKER_COLLECTION].find({}).to_list(100)
        except Exception as exc:  # noqa: BLE001
            log.warning("worker_status not readable: %s", exc)
            return
        for doc in docs:
            doc.pop("_id", None)
            if doc.get("workerId"):
                self.c.workers[doc["workerId"]] = doc

    async def deployment(self, model_id: int) -> Dict[str, Any]:
        model = await self._model(model_id)
        await self._load_saved()
        active = await ModelVersionRepo.active(self.session, model.model_id)
        now = utcnow()
        workers: List[Dict[str, Any]] = []
        for entry in sorted(self.c.workers.values(), key=lambda e: e["workerId"]):
            if entry.get("modelId") != model.model_id:
                continue
            item = {k: entry.get(k) for k in ("workerId", "modelVersion", "state", "loadedAt", "sha256",
                                              "sha256Verified", "lastHeartbeatAt", "detail")}
            try:
                beat = parse_iso(entry["lastHeartbeatAt"]) if entry.get("lastHeartbeatAt") else None
            except Validation:
                beat = None
            item["stale"] = beat is None or now - beat > STALE_AFTER
            item["runsActiveVersion"] = active is not None and entry.get("modelVersion") == active.version
            workers.append(item)
        return {"modelId": model.model_id, "activeVersion": active.version if active else None,
                "workers": workers, "asOf": iso(now)}

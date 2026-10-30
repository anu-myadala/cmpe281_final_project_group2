"""ModelRegistryService (C4 pages): models, the two-step version upload
(presigned S3 PUT, then complete with checksum and metrics), activation with
the BR-10 gate in one transaction, rollback, feedback labels (BR-11) and the
label export."""
from __future__ import annotations

import csv
import io
import logging
from typing import Any, Dict, List, Optional

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..clients import MODEL_ACTIVATED_TOPIC
from ..container import AppContainer
from ..core.audit import AuditLogger, snapshot
from ..core.rbac import Scope
from ..core.timeutil import iso, utcnow
from ..errors import BusinessRule, Conflict, NotFound, Validation
from ..models import SOUND_CLASSES, AiModel, FeedbackLabel, ModelVersion, Tenant
from ..repos import FeedbackRepo, ModelRepo, ModelVersionRepo, TenantRepo, UserRepo
from ..schemas import FeedbackBody, MetricsBody

log = logging.getLogger("scicp.registry")

MACRO_F1_MARGIN = 0.05


def version_dto(v: ModelVersion, label_count: int = 0) -> Dict[str, Any]:
    return {"versionId": v.version_id, "modelId": v.model_id, "version": v.version, "status": v.status,
            "metrics": v.metrics_json, "artifactUri": v.artifact_uri, "sha256": v.sha256, "sizeBytes": v.size_bytes,
            "createdBy": v.created_by, "createdAt": iso(v.created_at), "activatedAt": iso(v.activated_at),
            "deactivatedAt": iso(v.deactivated_at), "notes": v.notes, "labelCount": label_count}


class ModelRegistryService:
    def __init__(self, container: AppContainer, session: AsyncSession) -> None:
        self.c = container
        self.session = session

    # -- models -----------------------------------------------------------------------
    async def list_models(self) -> List[Dict[str, Any]]:
        models = await ModelRepo.list(self.session)
        active = await ModelVersionRepo.active_versions(self.session)
        counts = await ModelVersionRepo.version_counts(self.session)
        return [{"modelId": m.model_id, "name": m.name, "task": m.task, "activeVersion": active.get(m.model_id),
                 "versions": counts.get(m.model_id, 0), "createdAt": iso(m.created_at)} for m in models]

    async def create_model(self, name: str, task: str, actor: int, ip: Optional[str]) -> Dict[str, Any]:
        if await ModelRepo.by_name(self.session, name):
            raise Conflict("A model with that name exists", {"field": "name"})
        model = AiModel(name=name, task=task, created_by=actor)
        self.session.add(model)
        await self.session.flush()
        AuditLogger.write(self.session, actor, None, "MODEL_CREATE", "ai_model", model.model_id, None, snapshot(model), ip)
        await self.session.commit()
        return {"modelId": model.model_id, "name": model.name, "task": model.task, "activeVersion": None,
                "versions": 0, "createdAt": iso(model.created_at)}

    async def _model(self, model_id: int) -> AiModel:
        model = await ModelRepo.get(self.session, model_id)
        if model is None:
            raise NotFound("Model not found")
        return model

    # -- versions ---------------------------------------------------------------------
    async def list_versions(self, model_id: int) -> List[Dict[str, Any]]:
        await self._model(model_id)
        versions = await ModelVersionRepo.list(self.session, model_id)
        counts = await FeedbackRepo.label_counts(self.session, [v.version_id for v in versions])
        return [version_dto(v, counts.get(v.version_id, 0)) for v in versions]

    async def begin_upload(self, model_id: int, version: str, notes: Optional[str], actor: int,
                           ip: Optional[str]) -> Dict[str, Any]:
        await self._model(model_id)
        if await ModelVersionRepo.find(self.session, model_id, version):
            raise Conflict(f"Version {version} exists for this model", {"field": "version"})
        key = self.c.storage.artifact_key(model_id, version)
        row = ModelVersion(model_id=model_id, version=version, status="UPLOADING",
                           artifact_uri=self.c.storage.artifact_uri(model_id, version), notes=notes, created_by=actor)
        self.session.add(row)
        await self.session.flush()
        AuditLogger.write(self.session, actor, None, "VERSION_UPLOAD", "model_version", row.version_id, None,
                          snapshot(row), ip)
        await self.session.commit()
        url = await self.c.storage.presign_put(key)
        return {"versionId": row.version_id, "version": row.version, "status": row.status,
                "upload": {"url": url, "key": key, "expiresInSeconds": self.c.settings.presign_seconds}}

    async def complete_upload(self, model_id: int, version: str, sha256: str, size_bytes: int, metrics: MetricsBody,
                              actor: int, ip: Optional[str]) -> Dict[str, Any]:
        await self._model(model_id)
        row = await ModelVersionRepo.find(self.session, model_id, version)
        if row is None:
            raise NotFound("Version not found")
        if row.status != "UPLOADING":
            raise Conflict(f"Version is {row.status}, not UPLOADING")
        before = snapshot(row)
        failure = None
        head = await self.c.storage.head(row.artifact_key)
        if head is None:
            failure = "artifact missing"
        elif head.size != size_bytes:
            failure = f"size mismatch: object has {head.size} bytes"
        else:
            actual = head.sha256 or await self.c.storage.sha256_of(row.artifact_key)
            if actual.lower() != sha256.lower():
                failure = "sha256 mismatch"
        if failure:
            row.status = "FAILED"
            row.notes = f"{failure}" + (f"; {row.notes}" if row.notes else "")
            AuditLogger.write(self.session, actor, None, "VERSION_COMPLETE", "model_version", row.version_id, before,
                              snapshot(row), ip)
            await self.session.commit()
            raise BusinessRule("BR-10", f"Upload verification failed: {failure}", reason=failure)
        row.sha256 = sha256.lower()
        row.size_bytes = size_bytes
        row.metrics_json = metrics.model_dump()
        row.status = "REGISTERED"
        AuditLogger.write(self.session, actor, None, "VERSION_COMPLETE", "model_version", row.version_id, before,
                          snapshot(row), ip)
        await self.session.commit()
        return version_dto(row)

    async def activate(self, model_id: int, version: str, force: bool, note: Optional[str], actor: int,
                       ip: Optional[str], audit_action: str = "MODEL_ACTIVATE") -> Dict[str, Any]:
        """BR-10 (Table 31) in one transaction; the events go out after the commit."""
        await self._model(model_id)
        current = await ModelVersionRepo.active_for_update(self.session, model_id)  # SELECT ... FOR UPDATE
        target = await ModelVersionRepo.find(self.session, model_id, version)
        if target is None:
            raise NotFound("Version not found")
        if target.status not in ("REGISTERED", "INACTIVE"):  # R1
            raise Conflict(f"Version is {target.status}; only REGISTERED or INACTIVE versions can be activated")
        if not await self.c.storage.verify(target.artifact_key, target.size_bytes, target.sha256):  # R2
            raise BusinessRule("BR-10", "Artifact missing or changed in storage", reason="artifact")
        if not target.metrics_json or target.macro_f1 is None:  # R3
            raise BusinessRule("BR-10", "Metrics missing", reason="metrics")
        if current is not None and current.macro_f1 is not None:
            if target.macro_f1 < current.macro_f1 - MACRO_F1_MARGIN and not (force and note):  # R4 / R5
                raise BusinessRule("BR-10", "Macro-F1 is more than 0.05 below the active version",
                                   activeMacroF1=current.macro_f1, targetMacroF1=target.macro_f1)
        now = utcnow()
        previous = current.version if current else None
        if current is not None:
            current.status = "INACTIVE"
            current.deactivated_at = now
        target.status = "ACTIVE"
        target.activated_at = now
        target.deactivated_at = None
        AuditLogger.write(self.session, actor, None, audit_action, "model_version", target.version_id,
                          {"active": previous}, {"active": version, "force": force, "note": note}, ip)
        await self.session.commit()
        payload = {"modelId": model_id, "version": version}
        await self.c.bus.publish("model.activated", payload)  # staff room
        await self.c.mqtt.publish(MODEL_ACTIVATED_TOPIC, payload, qos=1, retain=True)  # C4 worker
        return {"active": version, "previous": previous, "modelId": model_id}

    async def rollback(self, model_id: int, note: Optional[str], actor: int, ip: Optional[str]) -> Dict[str, Any]:
        await self._model(model_id)
        prev = await ModelVersionRepo.last_previously_active(self.session, model_id)
        if prev is None:
            raise Conflict("Nothing to roll back to")
        result = await self.activate(model_id, prev.version, force=True, note="rollback: " + (note or ""), actor=actor,
                                     ip=ip)
        AuditLogger.write(self.session, actor, None, "MODEL_ROLLBACK", "model_version", prev.version_id,
                          {"active": result["previous"]}, {"active": prev.version, "note": note}, ip)
        await self.session.commit()
        return result

    async def download_url(self, model_id: int, version: str) -> str:
        await self._model(model_id)
        row = await ModelVersionRepo.find(self.session, model_id, version)
        if row is None:
            raise NotFound("Version not found")
        if row.status in ("UPLOADING", "FAILED"):
            raise Conflict(f"Version is {row.status}; no artifact to download")
        return await self.c.storage.presign_get(row.artifact_key)

    # -- feedback (BR-11) -------------------------------------------------------------------
    async def record_feedback(self, event_ref: str, body: FeedbackBody, actor: int, role: str,
                              tenant_id: Optional[int], ip: Optional[str]) -> Dict[str, Any]:
        if body.label == "WRONG_CLASS" and not body.correctedClass:
            raise Validation("correctedClass is required for WRONG_CLASS", {"field": "correctedClass"})
        if body.correctedClass and body.correctedClass not in SOUND_CLASSES:
            raise Validation("Unknown sound class", {"field": "correctedClass", "allowed": list(SOUND_CLASSES)})
        event = await self.c.events.find_event(event_ref)
        if event is None:
            raise NotFound("Event not found")
        if tenant_id is not None:  # tenant roles may only label their own events
            tenant: Optional[Tenant] = await TenantRepo.get(self.session, tenant_id)
            if tenant is None or event.get("tenantId") != tenant.code:
                raise NotFound("Event not found")
        version_row = await self._version_of_event(event)
        if version_row is None:
            raise NotFound("Model version of the event not found")
        if await FeedbackRepo.find(self.session, event_ref, actor):
            raise Conflict("A label for this event by this user exists", {"rule": "BR-11"})
        label = FeedbackLabel(event_ref=event_ref, version_id=version_row.version_id, user_id=actor,
                              label=body.label, corrected_class=body.correctedClass)
        self.session.add(label)
        try:
            await self.session.flush()
        except IntegrityError:
            await self.session.rollback()
            raise Conflict("A label for this event by this user exists", {"rule": "BR-11"})
        AuditLogger.write(self.session, actor, tenant_id, "FEEDBACK_LABEL", "feedback_label", label.label_id, None,
                          snapshot(label), ip)
        await self.session.commit()
        return {"labelId": label.label_id, "eventRef": event_ref, "versionId": label.version_id,
                "modelVersion": f"{(await self._model(version_row.model_id)).name}-{version_row.version}",
                "label": label.label, "correctedClass": label.corrected_class, "userId": actor, "ts": iso(label.ts)}

    async def _version_of_event(self, event: Dict[str, Any]) -> Optional[ModelVersion]:
        """inference.modelVersion is "{model name}-{version}" (sound-classifier-1.1.0)."""
        tag = (event.get("inference") or {}).get("modelVersion") or ""
        if "-" not in tag:
            return None
        name, version = tag.rsplit("-", 1)
        model = await ModelRepo.by_name(self.session, name)
        if model is None:
            return None
        return await ModelVersionRepo.find(self.session, model.model_id, version)

    async def export_feedback(self, model_id: int, version_id: Optional[int]) -> str:
        await self._model(model_id)
        labels = await FeedbackRepo.for_export(self.session, model_id, version_id)
        if not labels:
            raise NotFound("No labels for this version", {"rule": "BR-11"})
        events = await self.c.events.events_by_refs([lab.event_ref for lab in labels])
        buffer = io.StringIO()
        writer = csv.writer(buffer, lineterminator="\r\n")
        writer.writerow(["eventRef", "versionId", "predictedClass", "confidence", "label", "correctedClass", "userId", "ts"])
        for lab in labels:
            top = ((events.get(lab.event_ref) or {}).get("inference") or {}).get("topK") or [{}]
            writer.writerow([lab.event_ref, lab.version_id, top[0].get("class", ""), top[0].get("conf", ""),
                             lab.label, lab.corrected_class or "", lab.user_id, iso(lab.ts)])
        return buffer.getvalue()

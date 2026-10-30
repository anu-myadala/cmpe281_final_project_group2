"""SQLAlchemy repositories, one per table of Part I Section 3.3. They hold
all the SQL; services pass the session of the current request."""
from __future__ import annotations

from datetime import datetime
from typing import Dict, List, Optional, Sequence, Tuple

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.pagination import Page
from ..models import (AiModel, AlertType, AppUser, FeedbackLabel, IntelligenceService, ModelVersion, RefreshToken,
                      Subscription, Tenant, Vehicle, utcnow)


class TenantRepo:
    @staticmethod
    async def get(session: AsyncSession, tenant_id: int) -> Optional[Tenant]:
        return await session.get(Tenant, tenant_id)

    @staticmethod
    async def by_name(session: AsyncSession, name: str) -> Optional[Tenant]:
        return (await session.execute(select(Tenant).where(func.lower(Tenant.name) == name.lower()))).scalar_one_or_none()

    @staticmethod
    async def by_code(session: AsyncSession, code: str) -> Optional[Tenant]:
        return (await session.execute(select(Tenant).where(Tenant.code == code))).scalar_one_or_none()

    @staticmethod
    async def next_code(session: AsyncSession) -> str:
        """Tenant codes are tA, tB, ... then t{id} once the alphabet is used."""
        codes = set((await session.execute(select(Tenant.code))).scalars())
        for letter in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
            if f"t{letter}" not in codes:
                return f"t{letter}"
        return f"t{len(codes) + 1}"

    @staticmethod
    async def page(session: AsyncSession, page: Page, status: Optional[str] = None,
                   q: Optional[str] = None) -> Tuple[List[Tenant], int]:
        stmt = select(Tenant)
        if status:
            stmt = stmt.where(Tenant.status == status)
        if q:
            stmt = stmt.where(func.lower(Tenant.name).like(f"%{q.lower()}%"))
        total = (await session.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
        rows = (await session.execute(stmt.order_by(Tenant.tenant_id).offset(page.offset).limit(page.size))).scalars()
        return list(rows), int(total)

    @staticmethod
    async def counts(session: AsyncSession, tenant_ids: Sequence[int]) -> Tuple[Dict[int, int], Dict[int, int]]:
        """Vehicles (not retired) and users per tenant."""
        if not tenant_ids:
            return {}, {}
        v = await session.execute(select(Vehicle.tenant_id, func.count()).where(
            Vehicle.tenant_id.in_(tenant_ids), Vehicle.status != "RETIRED").group_by(Vehicle.tenant_id))
        u = await session.execute(select(AppUser.tenant_id, func.count()).where(
            AppUser.tenant_id.in_(tenant_ids)).group_by(AppUser.tenant_id))
        return {t: int(c) for t, c in v}, {t: int(c) for t, c in u}

    @staticmethod
    async def vehicle_count(session: AsyncSession, tenant_id: int) -> int:
        return int((await session.execute(select(func.count()).select_from(Vehicle).where(
            Vehicle.tenant_id == tenant_id, Vehicle.status != "RETIRED"))).scalar_one())


class UserRepo:
    @staticmethod
    async def get(session: AsyncSession, user_id: int) -> Optional[AppUser]:
        return await session.get(AppUser, user_id)

    @staticmethod
    async def find_by_email(session: AsyncSession, email: str) -> Optional[AppUser]:
        return (await session.execute(select(AppUser).where(AppUser.email == email.strip().lower()))).scalar_one_or_none()

    @staticmethod
    async def count_active_admins(session: AsyncSession) -> int:
        return int((await session.execute(select(func.count()).select_from(AppUser).where(
            AppUser.role == "SYS_ADMIN", AppUser.status == "ACTIVE"))).scalar_one())

    @staticmethod
    async def page(session: AsyncSession, page: Page, tenant_id: Optional[int] = None, role: Optional[str] = None,
                   status: Optional[str] = None, q: Optional[str] = None) -> Tuple[List[AppUser], int]:
        stmt = select(AppUser)
        if tenant_id is not None:
            stmt = stmt.where(AppUser.tenant_id == tenant_id)
        if role:
            stmt = stmt.where(AppUser.role == role)
        if status:
            stmt = stmt.where(AppUser.status == status)
        if q:
            like = f"%{q.lower()}%"
            stmt = stmt.where(or_(func.lower(AppUser.email).like(like), func.lower(AppUser.name).like(like)))
        total = (await session.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
        rows = (await session.execute(stmt.order_by(AppUser.user_id).offset(page.offset).limit(page.size))).scalars()
        return list(rows), int(total)

    @staticmethod
    async def names(session: AsyncSession, user_ids: Sequence[int]) -> Dict[int, str]:
        if not user_ids:
            return {}
        rows = await session.execute(select(AppUser.user_id, AppUser.name).where(AppUser.user_id.in_(set(user_ids))))
        return {uid: name for uid, name in rows}


class RefreshTokenRepo:
    @staticmethod
    async def find_valid(session: AsyncSession, token_hash: str) -> Optional[RefreshToken]:
        row = (await session.execute(select(RefreshToken).where(RefreshToken.token_hash == token_hash))).scalar_one_or_none()
        if row is None or row.revoked_at is not None or row.expires_at <= utcnow():
            return None
        return row

    @staticmethod
    async def revoke(session: AsyncSession, token_hash: str) -> None:
        await session.execute(update(RefreshToken).where(RefreshToken.token_hash == token_hash,
                                                         RefreshToken.revoked_at.is_(None)).values(revoked_at=utcnow()))

    @staticmethod
    async def revoke_all(session: AsyncSession, user_id: int) -> None:
        await session.execute(update(RefreshToken).where(RefreshToken.user_id == user_id,
                                                         RefreshToken.revoked_at.is_(None)).values(revoked_at=utcnow()))


class IntelligenceServiceRepo:
    @staticmethod
    async def list(session: AsyncSession) -> List[IntelligenceService]:
        return list((await session.execute(select(IntelligenceService).order_by(IntelligenceService.service_code))).scalars())


class SubscriptionRepo:
    @staticmethod
    async def by_vehicle(session: AsyncSession, vehicle_id: int) -> List[Subscription]:
        return list((await session.execute(select(Subscription).where(
            Subscription.vehicle_id == vehicle_id).order_by(Subscription.service_code))).scalars())

    @staticmethod
    async def by_vehicles(session: AsyncSession, vehicle_ids: Sequence[int]) -> Dict[int, List[Subscription]]:
        out: Dict[int, List[Subscription]] = {vid: [] for vid in vehicle_ids}
        if not vehicle_ids:
            return out
        rows = (await session.execute(select(Subscription).where(
            Subscription.vehicle_id.in_(vehicle_ids)).order_by(Subscription.service_code))).scalars()
        for row in rows:
            out.setdefault(row.vehicle_id, []).append(row)
        return out

    @staticmethod
    async def by_tenant(session: AsyncSession, tenant_id: int) -> List[Subscription]:
        return list((await session.execute(select(Subscription).where(
            Subscription.tenant_id == tenant_id).order_by(Subscription.vehicle_id, Subscription.service_code))).scalars())


class AlertTypeRepo:
    @staticmethod
    async def list(session: AsyncSession) -> List[AlertType]:
        return list((await session.execute(select(AlertType).order_by(AlertType.code))).scalars())

    @staticmethod
    async def get(session: AsyncSession, code: str) -> Optional[AlertType]:
        return await session.get(AlertType, code)


class ModelRepo:
    @staticmethod
    async def list(session: AsyncSession) -> List[AiModel]:
        return list((await session.execute(select(AiModel).order_by(AiModel.model_id))).scalars())

    @staticmethod
    async def get(session: AsyncSession, model_id: int) -> Optional[AiModel]:
        return await session.get(AiModel, model_id)

    @staticmethod
    async def by_name(session: AsyncSession, name: str) -> Optional[AiModel]:
        return (await session.execute(select(AiModel).where(AiModel.name == name))).scalar_one_or_none()


class ModelVersionRepo:
    @staticmethod
    async def list(session: AsyncSession, model_id: int) -> List[ModelVersion]:
        return list((await session.execute(select(ModelVersion).where(
            ModelVersion.model_id == model_id).order_by(ModelVersion.created_at.desc(), ModelVersion.version_id.desc()))).scalars())

    @staticmethod
    async def find(session: AsyncSession, model_id: int, version: str) -> Optional[ModelVersion]:
        return (await session.execute(select(ModelVersion).where(
            ModelVersion.model_id == model_id, ModelVersion.version == version))).scalar_one_or_none()

    @staticmethod
    async def get(session: AsyncSession, version_id: int) -> Optional[ModelVersion]:
        return await session.get(ModelVersion, version_id)

    @staticmethod
    async def active(session: AsyncSession, model_id: int) -> Optional[ModelVersion]:
        return (await session.execute(select(ModelVersion).where(
            ModelVersion.model_id == model_id, ModelVersion.status == "ACTIVE"))).scalars().first()

    @staticmethod
    async def active_for_update(session: AsyncSession, model_id: int) -> Optional[ModelVersion]:
        """SELECT ... FOR UPDATE on the current ACTIVE row (a no-op lock on SQLite)."""
        stmt = select(ModelVersion).where(ModelVersion.model_id == model_id, ModelVersion.status == "ACTIVE")
        if session.bind is not None and session.bind.dialect.name != "sqlite":
            stmt = stmt.with_for_update()
        return (await session.execute(stmt)).scalars().first()

    @staticmethod
    async def last_previously_active(session: AsyncSession, model_id: int) -> Optional[ModelVersion]:
        """The INACTIVE version with the newest deactivated_at."""
        return (await session.execute(select(ModelVersion).where(
            ModelVersion.model_id == model_id, ModelVersion.status == "INACTIVE",
            ModelVersion.deactivated_at.isnot(None)).order_by(ModelVersion.deactivated_at.desc()))).scalars().first()

    @staticmethod
    async def active_versions(session: AsyncSession) -> Dict[int, str]:
        rows = await session.execute(select(ModelVersion.model_id, ModelVersion.version).where(ModelVersion.status == "ACTIVE"))
        return {m: v for m, v in rows}

    @staticmethod
    async def version_counts(session: AsyncSession) -> Dict[int, int]:
        rows = await session.execute(select(ModelVersion.model_id, func.count()).group_by(ModelVersion.model_id))
        return {m: int(c) for m, c in rows}


class FeedbackRepo:
    @staticmethod
    async def label_counts(session: AsyncSession, version_ids: Sequence[int]) -> Dict[int, int]:
        if not version_ids:
            return {}
        rows = await session.execute(select(FeedbackLabel.version_id, func.count()).where(
            FeedbackLabel.version_id.in_(version_ids)).group_by(FeedbackLabel.version_id))
        return {v: int(c) for v, c in rows}

    @staticmethod
    async def find(session: AsyncSession, event_ref: str, user_id: int) -> Optional[FeedbackLabel]:
        return (await session.execute(select(FeedbackLabel).where(
            FeedbackLabel.event_ref == event_ref, FeedbackLabel.user_id == user_id))).scalar_one_or_none()

    @staticmethod
    async def for_export(session: AsyncSession, model_id: int, version_id: Optional[int]) -> List[FeedbackLabel]:
        stmt = select(FeedbackLabel).join(ModelVersion, ModelVersion.version_id == FeedbackLabel.version_id).where(
            ModelVersion.model_id == model_id)
        if version_id is not None:
            stmt = stmt.where(FeedbackLabel.version_id == version_id)
        return list((await session.execute(stmt.order_by(FeedbackLabel.ts, FeedbackLabel.label_id))).scalars())

"""AuthService and LoginThrottle (BR-02, decision table of Part I Table 25).

The lock check comes before the password check so that a locked account
does not leak whether the password was right; the counters live on the
app_user row so that a lock survives a restart.
"""
from __future__ import annotations

from datetime import timedelta
from typing import Dict, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from ..container import AppContainer
from ..core.audit import AuditLogger
from ..core.rbac import RbacGuard
from ..core.security import TokenPair
from ..core.timeutil import iso, utcnow
from ..errors import Forbidden, Locked, RateLimit, Unauthorized, Validation
from ..models import AppUser, Tenant
from ..repos import RefreshTokenRepo, TenantRepo, UserRepo

FAILURE_WINDOW = timedelta(minutes=15)
LOCK_DURATION = timedelta(minutes=15)
MAX_FAILURES = 5
MIN_PASSWORD_LENGTH = 10


class LoginThrottle:
    """Owns failed_logins, last_failed_at and locked_until on app_user."""

    @staticmethod
    def is_locked(user: AppUser) -> bool:
        return user.locked_until is not None and user.locked_until > utcnow()

    @staticmethod
    def record_failure(user: AppUser) -> bool:
        """Count one failure; True when this failure locks the account."""
        now = utcnow()
        if user.last_failed_at is None or now - user.last_failed_at > FAILURE_WINDOW:
            user.failed_logins = 0  # the window closed: the counter starts again
        user.failed_logins += 1
        user.last_failed_at = now
        if user.failed_logins >= MAX_FAILURES:
            user.status = "LOCKED"
            user.locked_until = now + LOCK_DURATION
            return True
        return False

    @staticmethod
    def reset(user: AppUser) -> None:
        user.failed_logins = 0
        user.last_failed_at = None
        user.locked_until = None
        if user.status == "LOCKED":
            user.status = "ACTIVE"


def user_dto(user: AppUser, tenant: Optional[Tenant]) -> Dict:
    return {"userId": user.user_id, "email": user.email, "name": user.name, "role": user.role,
            "tenantId": user.tenant_id, "tenantName": tenant.name if tenant else None,
            "plan": tenant.plan if tenant else None, "mustChangePassword": bool(user.must_change_password)}


class AuthService:
    def __init__(self, container: AppContainer, session: AsyncSession) -> None:
        self.c = container
        self.session = session

    async def login(self, email: str, password: str, ip: Optional[str]) -> tuple:
        """-> (TokenPair, user, tenant). Raises 401 / 403 / 423 / 429."""
        if not self.c.login_rate.hit(ip or "unknown"):
            raise RateLimit("Too many login attempts, try again in a minute")
        user = await UserRepo.find_by_email(self.session, email)
        if user is None or user.status == "DISABLED":
            raise Unauthorized()  # R1: generic answer, no audit row (the email may belong to nobody)
        tenant = await TenantRepo.get(self.session, user.tenant_id) if user.tenant_id is not None else None
        if tenant is not None and tenant.status == "SUSPENDED":
            raise Forbidden("TENANT_SUSPENDED")  # R2
        if LoginThrottle.is_locked(user):
            raise Locked("Account locked. Try again later.", {"lockedUntil": iso(user.locked_until)})  # R3
        if not self.c.hasher.verify(password, user.password_hash):
            locked = LoginThrottle.record_failure(user)  # R4 / R5
            AuditLogger.write(self.session, user.user_id, user.tenant_id, "LOGIN_LOCK" if locked else "LOGIN_FAIL",
                              "app_user", user.user_id, None, {"failed": user.failed_logins, "ip": ip}, ip)
            await self.session.commit()
            if locked:
                raise Locked("Account locked. Try again later.", {"lockedUntil": iso(user.locked_until)})
            raise Unauthorized()
        LoginThrottle.reset(user)  # R6
        user.last_login_at = utcnow()
        pair, refresh_row = self.c.tokens.issue(user, tenant)
        self.session.add(refresh_row)
        AuditLogger.write(self.session, user.user_id, user.tenant_id, "LOGIN", "app_user", user.user_id, None,
                          {"ip": ip}, ip)
        await self.session.commit()
        return pair, user, tenant

    async def refresh(self, refresh_token: Optional[str]) -> str:
        if not refresh_token:
            raise Unauthorized("Missing refresh token")
        row = await RefreshTokenRepo.find_valid(self.session, self.c.tokens.hash_refresh(refresh_token))
        if row is None:
            raise Unauthorized("Refresh token invalid or expired")
        user = await UserRepo.get(self.session, row.user_id)
        if user is None or user.status == "DISABLED":
            raise Unauthorized()
        tenant = await TenantRepo.get(self.session, user.tenant_id) if user.tenant_id is not None else None
        if tenant is not None and tenant.status == "SUSPENDED":
            raise Forbidden("TENANT_SUSPENDED")
        return self.c.tokens.issue_access(user, tenant)

    async def logout(self, user_id: int, tenant_id: Optional[int], jti: str, exp: int,
                     refresh_token: Optional[str], ip: Optional[str]) -> None:
        self.c.tokens.revoke(jti, exp)
        if refresh_token:
            await RefreshTokenRepo.revoke(self.session, self.c.tokens.hash_refresh(refresh_token))
        AuditLogger.write(self.session, user_id, tenant_id, "LOGOUT", "app_user", user_id, None, {"jti": jti}, ip)
        await self.session.commit()

    async def me(self, user_id: int) -> Dict:
        user = await UserRepo.get(self.session, user_id)
        if user is None or user.status == "DISABLED":
            raise Unauthorized()
        tenant = await TenantRepo.get(self.session, user.tenant_id) if user.tenant_id is not None else None
        return {"user": user_dto(user, tenant), "permissions": RbacGuard.page_permissions(user.role)}

    async def change_password(self, user_id: int, current: str, new: str, ip: Optional[str]) -> None:
        user = await UserRepo.get(self.session, user_id)
        if user is None:
            raise Unauthorized()
        if len(new) < MIN_PASSWORD_LENGTH or new.lower() == user.email.lower():
            raise Validation("Password must have at least 10 characters and differ from the email",
                             {"field": "newPassword"})
        if not self.c.hasher.verify(current, user.password_hash):
            raise Unauthorized("Current password is wrong")
        before = {"mustChangePassword": bool(user.must_change_password)}
        user.password_hash = self.c.hasher.hash(new)
        user.must_change_password = False
        AuditLogger.write(self.session, user.user_id, user.tenant_id, "PASSWORD_CHANGE", "app_user", user.user_id,
                          before, {"mustChangePassword": False}, ip)
        await self.session.commit()

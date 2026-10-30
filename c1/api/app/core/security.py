"""PasswordHasher (argon2id), TokenService (access JWT + opaque refresh token)
and the service token used between the components."""
from __future__ import annotations

import hashlib
import secrets
import string
import time
import uuid
from dataclasses import dataclass
from datetime import timedelta
from typing import Dict, Optional

import jwt
from argon2 import PasswordHasher as _Argon2
from argon2.exceptions import VerifyMismatchError, VerificationError, InvalidHashError

from ..config import Settings
from ..errors import Unauthorized
from ..models import AppUser, RefreshToken, Tenant, utcnow


class PasswordHasher:
    """Argon2id hashes and random temporary passwords."""

    def __init__(self) -> None:
        self._argon = _Argon2()  # argon2id with the library defaults

    def hash(self, password: str) -> str:
        return self._argon.hash(password)

    def verify(self, password: str, password_hash: str) -> bool:
        try:
            return self._argon.verify(password_hash, password)
        except (VerifyMismatchError, VerificationError, InvalidHashError):
            return False

    @staticmethod
    def random_password(length: int = 12) -> str:
        alphabet = string.ascii_letters + string.digits
        body = "".join(secrets.choice(alphabet) for _ in range(length - 2))
        return body + secrets.choice(string.digits) + secrets.choice("!#$%")


@dataclass
class Claims:
    """Verified content of an access token."""
    user_id: int
    tenant_id: Optional[int]
    role: str
    name: str
    plan: Optional[str]
    jti: str
    exp: int

    @property
    def is_staff(self) -> bool:
        return self.tenant_id is None and self.role in ("IOT_ENGINEER", "SERVICE_STAFF", "SYS_ADMIN")


@dataclass
class TokenPair:
    access_token: str
    refresh_token: str
    expires_in: int


class TokenService:
    """Issues and verifies the access JWT (HS256, 15 min) and the opaque
    refresh token (random, stored as SHA-256 in refresh_token, 7 days)."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._revoked: Dict[str, float] = {}  # jti -> expiry epoch (process memory in our build)

    # -- access token -----------------------------------------------------
    def issue_access(self, user: AppUser, tenant: Optional[Tenant]) -> str:
        now = int(time.time())
        exp = now + self.settings.access_token_minutes * 60
        payload = {
            "sub": str(user.user_id),
            "tenantId": user.tenant_id,
            "role": user.role,
            "name": user.name,
            "plan": tenant.plan if tenant else None,
            "jti": uuid.uuid4().hex,
            "iat": now,
            "exp": exp,
        }
        return jwt.encode(payload, self.settings.jwt_secret, algorithm="HS256")

    def verify(self, token: str) -> Claims:
        try:
            payload = jwt.decode(token, self.settings.jwt_secret, algorithms=["HS256"])
        except jwt.ExpiredSignatureError:
            raise Unauthorized("Token expired")
        except jwt.PyJWTError:
            raise Unauthorized("Invalid token")
        if payload.get("role") == "SERVICE":
            raise Unauthorized("Service tokens cannot call user endpoints")
        jti = payload.get("jti", "")
        self._purge_revoked()
        if jti in self._revoked:
            raise Unauthorized("Token revoked")
        try:
            return Claims(user_id=int(payload["sub"]), tenant_id=payload.get("tenantId"), role=payload["role"],
                          name=payload.get("name", ""), plan=payload.get("plan"), jti=jti, exp=int(payload["exp"]))
        except (KeyError, ValueError):
            raise Unauthorized("Invalid token")

    def revoke(self, jti: str, exp: Optional[int] = None) -> None:
        self._revoked[jti] = float(exp or time.time() + self.settings.access_token_minutes * 60)

    def _purge_revoked(self) -> None:
        now = time.time()
        for key in [k for k, v in self._revoked.items() if v < now]:
            self._revoked.pop(key, None)

    # -- refresh token ----------------------------------------------------
    @staticmethod
    def hash_refresh(token: str) -> str:
        return hashlib.sha256(token.encode()).hexdigest()

    def new_refresh(self, user: AppUser) -> tuple:
        raw = secrets.token_urlsafe(48)
        row = RefreshToken(user_id=user.user_id, token_hash=self.hash_refresh(raw),
                           expires_at=utcnow() + timedelta(days=self.settings.refresh_token_days))
        return raw, row

    def issue(self, user: AppUser, tenant: Optional[Tenant]) -> tuple:
        """Access JWT plus a new refresh token row (the caller persists the row)."""
        raw, row = self.new_refresh(user)
        pair = TokenPair(access_token=self.issue_access(user, tenant), refresh_token=raw,
                         expires_in=self.settings.access_token_minutes * 60)
        return pair, row

    # -- service token ----------------------------------------------------
    def issue_service_token(self, subject: str = "c1-api") -> str:
        now = int(time.time())
        payload = {"sub": subject, "role": "SERVICE", "iat": now,
                   "exp": now + self.settings.service_token_hours * 3600, "jti": uuid.uuid4().hex}
        return jwt.encode(payload, self.settings.service_token_secret, algorithm="HS256")

    def verify_service_token(self, token: str) -> dict:
        try:
            payload = jwt.decode(token, self.settings.service_token_secret, algorithms=["HS256"])
        except jwt.PyJWTError:
            raise Unauthorized("Invalid service token")
        if payload.get("role") != "SERVICE":
            raise Unauthorized("Not a service token")
        return payload

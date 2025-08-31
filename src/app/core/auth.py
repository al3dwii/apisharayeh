# app/core/auth.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Dict

from fastapi import Header, HTTPException, Query, status
import jwt

from app.core.config import settings


@dataclass
class Tenant:
    id: str
    user_id: Optional[str] = None
    role: Optional[str] = None


def _parse_jwt_token(token: str) -> Dict:
    """
    Parse a JWT. In prod (JWT_PUBLIC_KEY set), verify RS256 signatures.
    If no public key is configured, parse without verification so local dev
    can still send dummy tokens if desired.
    """
    try:
        if settings.JWT_PUBLIC_KEY:
            return jwt.decode(
                token,
                settings.JWT_PUBLIC_KEY,
                algorithms=["RS256"],
                options={"verify_aud": False},
            )
        # dev: no key configured → don't verify signature
        return jwt.decode(token, options={"verify_signature": False, "verify_aud": False})
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=f"Invalid token: {e}") from e


async def get_tenant(
    authorization: Optional[str] = Header(default=None, alias="Authorization"),
    x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
    x_user_id: Optional[str] = Header(default=None, alias="X-User-Id"),
    tenant_query: Optional[str] = Query(default=None, alias="tenant"),
) -> Tenant:
    """
    Tenant resolver used by FastAPI dependencies.

    DEV behavior:
      - If DISABLE_TENANT is truthy (e.g., DISABLE_TENANT=1), we bypass auth/claims entirely.
      - We accept optional X-Tenant-Id / X-User-Id headers or ?tenant=... to override;
        otherwise we fall back to 'demo-tenant' / 'demo-user'.

      This lets you run curl / local tools without seeding DB flags or sending JWTs.

    PROD behavior:
      - If JWT_PUBLIC_KEY is present, require a Bearer token, parse/verify it,
        and extract tenant/user info from common claim names. X-Tenant-Id may
        still override if present (useful for non-standard identity providers).
    """
    dev_bypass = bool(getattr(settings, "DISABLE_TENANT", False))

    # ---------- DEV BYPASS ----------
    if dev_bypass or not settings.JWT_PUBLIC_KEY:
        tenant_id = x_tenant_id or tenant_query or "demo-tenant"
        user_id = x_user_id or "demo-user"
        return Tenant(id=tenant_id, user_id=user_id, role="admin")

    # ---------- PROD / STRICT MODE ----------
    # Require a Bearer token
    if not authorization:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing Authorization header")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid auth scheme (expected Bearer)")

    claims = _parse_jwt_token(token)

    # Allow header/query override if provided; otherwise pull from claims.
    tenant_id = x_tenant_id or tenant_query \
        or claims.get("tenant_id") or claims.get("org_id") or claims.get("org") or claims.get("tid")
    user_id = x_user_id or claims.get("sub") or claims.get("user_id")
    role = claims.get("role") or "user"

    if not tenant_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="No tenant in token")

    return Tenant(id=tenant_id, user_id=user_id, role=role)

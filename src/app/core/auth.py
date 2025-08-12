from fastapi import Header, HTTPException
from dataclasses import dataclass
import jwt
from app.core.config import settings

@dataclass
class Tenant:
    id: str
    user_id: str | None = None
    role: str | None = None

def _parse_jwt_token(token: str) -> dict:
    try:
        return jwt.decode(
            token,
            settings.JWT_PUBLIC_KEY,
            algorithms=["RS256"],
            options={"verify_aud": False},
        )
    except Exception as e:
        raise HTTPException(401, f"Invalid token: {e}")

async def get_tenant(authorization: str | None = Header(default=None)) -> Tenant:
    # DEV fallback if no auth configured
    if not authorization or not authorization.startswith("Bearer ") or not settings.JWT_PUBLIC_KEY:
        return Tenant(id="demo-tenant", user_id="demo-user", role="admin")
    token = authorization.split(" ", 1)[1]
    claims = _parse_jwt_token(token)
    # Common claim fallbacks across providers
    tenant_id = claims.get("tenant_id") or claims.get("org_id") or claims.get("org") or claims.get("tid")
    user_id = claims.get("sub") or claims.get("user_id")
    role = claims.get("role") or "user"
    if not tenant_id:
        raise HTTPException(403, "No tenant in token")
    return Tenant(id=tenant_id, user_id=user_id, role=role)

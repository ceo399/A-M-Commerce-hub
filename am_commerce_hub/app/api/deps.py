"""FastAPI ????: ?????????????????"""
from __future__ import annotations

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app import security
from app.db.models import Role, User
from app.db.session import session_scope

_bearer = HTTPBearer(auto_error=True)


def get_current_user(creds: HTTPAuthorizationCredentials = Depends(_bearer)) -> dict:
    """JWT?????????????????????DB??????"""
    try:
        payload = security.decode_access_token(creds.credentials)
    except Exception:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "???????????")
    return {"id": int(payload["sub"]), "email": payload["email"],
            "roles": payload.get("roles", [])}


def require_roles(*allowed: Role):
    """?????????ADMIN?????????"""
    allowed_values = {r.value for r in allowed} | {Role.ADMIN.value}

    def _checker(current=Depends(get_current_user)) -> dict:
        if not (set(current["roles"]) & allowed_values):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "?????????????")
        return current

    return _checker

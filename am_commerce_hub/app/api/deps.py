"""FastAPI 依存関数: 認証済みユーザー取得とロール検査。"""
from __future__ import annotations

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app import security
from app.db.models import Role, User
from app.db.session import session_scope

_bearer = HTTPBearer(auto_error=True)


def get_current_user(creds: HTTPAuthorizationCredentials = Depends(_bearer)) -> dict:
    """JWTを検証し、ユーザー情報の辞書を返す（軽量・DB照会なし）。"""
    try:
        payload = security.decode_access_token(creds.credentials)
    except Exception:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "認証トークンが無効です")
    return {"id": int(payload["sub"]), "email": payload["email"],
            "roles": payload.get("roles", [])}


def require_roles(*allowed: Role):
    """指定ロール（またはADMIN）を要求する依存。"""
    allowed_values = {r.value for r in allowed} | {Role.ADMIN.value}

    def _checker(current=Depends(get_current_user)) -> dict:
        if not (set(current["roles"]) & allowed_values):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "この操作の権限がありません")
        return current

    return _checker

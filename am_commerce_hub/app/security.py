"""パスワードハッシュ・JWT・OTPコード生成。"""
from __future__ import annotations

import base64
import hashlib
import secrets
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt

from app.config import settings


# ---- パスワード（bcrypt。72バイト制限回避のため事前にSHA-256で正規化） ----
def _prepare(password: str) -> bytes:
    digest = hashlib.sha256(password.encode("utf-8")).digest()
    return base64.b64encode(digest)


def hash_password(password: str) -> str:
    return bcrypt.hashpw(_prepare(password), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(_prepare(password), password_hash.encode("utf-8"))
    except ValueError:
        return False


# ---- JWT アクセストークン ----
def create_access_token(user_id: int, email: str, roles: list[str]) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id),
        "email": email,
        "roles": roles,
        "iat": now,
        "exp": now + timedelta(minutes=settings.access_token_minutes),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str) -> dict:
    return jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])


# ---- OTP（二段階認証コード） ----
def generate_otp(num_digits: int = 6) -> str:
    return "".join(secrets.choice("0123456789") for _ in range(num_digits))


def hash_otp(code: str) -> str:
    return hashlib.sha256(code.encode("utf-8")).hexdigest()


def verify_otp(code: str, code_hash: str) -> bool:
    return secrets.compare_digest(hash_otp(code), code_hash)

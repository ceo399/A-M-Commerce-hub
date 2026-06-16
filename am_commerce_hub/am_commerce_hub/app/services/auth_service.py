"""???????

- ?????????????
- ?????????????? ? ???????????
- ?????????? ? ??????????
"""
from __future__ import annotations

import secrets
from datetime import timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import security
from app.config import settings
from app.db.base import utcnow
from app.db.models import LoginChallenge, Role, TwoFactorMethod, User
from app.integrations.registry import Integrations, get_integrations
from app.logging_config import get_logger

log = get_logger("amhub.auth")


class AuthError(Exception):
    """?????????????????401/400?????"""


# ---- ????????????? ----
def create_user(session: Session, *, email: str, password: str, full_name: str | None = None,
                roles: list[Role], phone: str | None = None,
                two_factor_method: TwoFactorMethod = TwoFactorMethod.EMAIL,
                is_admin: bool = False) -> User:
    if session.scalar(select(User).where(User.email == email)):
        raise AuthError("????????????????????")
    role_values = sorted({r.value for r in roles} | ({Role.ADMIN.value} if is_admin else set()))
    if two_factor_method == TwoFactorMethod.SMS and not phone:
        raise AuthError("SMS????????????????")
    user = User(
        email=email, full_name=full_name,
        password_hash=security.hash_password(password),
        phone=phone, roles=role_values, two_factor_method=two_factor_method,
        is_active=True, must_change_password=True,
    )
    session.add(user)
    session.flush()
    return user


# ---- ??????1??: ??????? ? OTP??? ----
def start_login(session: Session, *, email: str, password: str,
                integ: Integrations | None = None) -> dict:
    integ = get_integrations(integ)
    user = session.scalar(select(User).where(User.email == email))
    # ?????????????????????????????
    if user is None or not user.is_active or not security.verify_password(password, user.password_hash):
        raise AuthError("????????????????????????")

    code = security.generate_otp()
    challenge = LoginChallenge(
        challenge_uid=secrets.token_urlsafe(24),
        user_id=user.id,
        code_hash=security.hash_otp(code),
        method=user.two_factor_method,
        expires_at=utcnow() + timedelta(minutes=settings.otp_expire_minutes),
    )
    session.add(challenge)
    session.flush()

    destination = user.phone if user.two_factor_method == TwoFactorMethod.SMS else user.email
    integ.otp.send_otp(method=user.two_factor_method.value, destination=destination or "", code=code)

    masked = _mask(destination or "")
    out = {"challenge_id": challenge.challenge_uid,
           "method": user.two_factor_method.value,
           "destination_hint": masked}
    # ?????: ??????OTP?????????????????????
    if settings.dev_echo_otp and settings.use_mock():
        out["dev_code"] = code
        out["dev_note"] = "?????????????(live)??????????"
    return out


# ---- ??????2??: OTP?? ? ??????? ----
def verify_login(session: Session, *, challenge_id: str, code: str) -> dict:
    challenge = session.scalar(
        select(LoginChallenge).where(LoginChallenge.challenge_uid == challenge_id)
    )
    if challenge is None or challenge.consumed:
        raise AuthError("??????????????????????????")
    expires_at = challenge.expires_at
    if expires_at.tzinfo is None:  # SQLite?tz??????????UTC??????
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if utcnow() > expires_at:
        raise AuthError("????????????????")
    if challenge.attempts >= settings.otp_max_attempts:
        raise AuthError("???????????????????????????")

    challenge.attempts += 1
    if not security.verify_otp(code, challenge.code_hash):
        raise AuthError("??????????????")

    challenge.consumed = True
    user = session.get(User, challenge.user_id)
    token = security.create_access_token(user.id, user.email, user.roles)
    log.info("login success user=%s roles=%s", user.email, user.roles)
    return {"access_token": token, "token_type": "bearer",
            "must_change_password": user.must_change_password}


def change_password(session: Session, *, user: User, new_password: str) -> None:
    user.password_hash = security.hash_password(new_password)
    user.must_change_password = False


def _mask(value: str) -> str:
    """?????????????/????????"""
    if "@" in value:
        name, _, domain = value.partition("@")
        head = name[:2]
        return f"{head}{'*' * max(1, len(name) - 2)}@{domain}"
    if len(value) >= 4:
        return f"{'*' * (len(value) - 4)}{value[-4:]}"
    return "****"

"""??API: ???? ? ????? ? ???????????????????"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.api.deps import get_current_user, require_roles
from app.db.models import Role, TwoFactorMethod, User
from app.db.session import session_scope
from app.services import auth_service
from app.services.auth_service import AuthError

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginBody(BaseModel):
    email: str
    password: str


class VerifyBody(BaseModel):
    challenge_id: str
    code: str


class CreateUserBody(BaseModel):
    email: str
    password: str = Field(min_length=8)
    full_name: str | None = None
    roles: list[Role] = []
    phone: str | None = None
    two_factor_method: TwoFactorMethod = TwoFactorMethod.EMAIL
    is_admin: bool = False


class ChangePasswordBody(BaseModel):
    new_password: str = Field(min_length=8)


@router.post("/login")
def login(body: LoginBody):
    """?1??: ????????? ? ????????????"""
    try:
        with session_scope() as s:
            return auth_service.start_login(s, email=body.email, password=body.password)
    except AuthError as e:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(e))


@router.post("/verify")
def verify(body: VerifyBody):
    """?2??: ??????? ? ???????????"""
    try:
        with session_scope() as s:
            return auth_service.verify_login(s, challenge_id=body.challenge_id, code=body.code)
    except AuthError as e:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(e))


@router.post("/users", status_code=201)
def create_user(body: CreateUserBody, _=Depends(require_roles(Role.ADMIN))):
    """?????: ????????"""
    try:
        with session_scope() as s:
            user = auth_service.create_user(
                s, email=body.email, password=body.password, full_name=body.full_name,
                roles=body.roles, phone=body.phone,
                two_factor_method=body.two_factor_method, is_admin=body.is_admin,
            )
            return {"id": user.id, "email": user.email, "roles": user.roles}
    except AuthError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e))


@router.get("/me")
def me(current=Depends(get_current_user)):
    return current


@router.post("/change-password")
def change_password(body: ChangePasswordBody, current=Depends(get_current_user)):
    with session_scope() as s:
        user = s.get(User, current["id"])
        auth_service.change_password(s, user=user, new_password=body.new_password)
    return {"status": "ok"}

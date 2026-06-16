"""認証・二段階認証・ロール権限のテスト。"""
from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.db.models import APPROVAL_ROLE_MAP, ApprovalType, Role, TwoFactorMethod
from app.integrations.mock_adapters import MockOtpDelivery
from app.integrations.registry import Integrations
from app.services import auth_service
from app.services.auth_service import AuthError


def _session():
    engine = create_engine("sqlite:///:memory:", future=True)
    import app.db.models  # noqa
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, future=True)()


def _integ():
    otp = MockOtpDelivery()
    # 認証以外のポートはテストでは未使用なのでNoneでも可だが型のため簡易ダミーを設定
    return Integrations(vendor=None, catalog=None, listings=None, ads=None,
                        attribution=None, shipping_hub=None, ai=None, otp=otp), otp


def test_full_login_flow_with_2fa():
    s = _session()
    integ, otp = _integ()
    auth_service.create_user(s, email="md@example.com", password="StrongPass!23",
                             full_name="MD担当", roles=[Role.MERCHANDISING],
                             two_factor_method=TwoFactorMethod.EMAIL)
    # 第1段階
    res = auth_service.start_login(s, email="md@example.com", password="StrongPass!23", integ=integ)
    assert res["method"] == "email" and otp.last_code is not None
    # 第2段階
    out = auth_service.verify_login(s, challenge_id=res["challenge_id"], code=otp.last_code)
    assert out["access_token"] and out["token_type"] == "bearer"


def test_wrong_password_and_wrong_code():
    s = _session()
    integ, otp = _integ()
    auth_service.create_user(s, email="u@example.com", password="StrongPass!23",
                             full_name="U", roles=[Role.LOGISTICS])
    try:
        auth_service.start_login(s, email="u@example.com", password="bad", integ=integ)
        assert False, "wrong password should fail"
    except AuthError:
        pass
    res = auth_service.start_login(s, email="u@example.com", password="StrongPass!23", integ=integ)
    try:
        auth_service.verify_login(s, challenge_id=res["challenge_id"], code="000000")
        assert False, "wrong code should fail (unless it happens to match)"
    except AuthError:
        pass


def test_role_mapping_complete():
    # すべての承認種別にロールが割り当てられている
    for atype in ApprovalType:
        assert atype in APPROVAL_ROLE_MAP


if __name__ == "__main__":
    test_full_login_flow_with_2fa()
    test_wrong_password_and_wrong_code()
    test_role_mapping_complete()
    print("OK: 認証テスト全通過")

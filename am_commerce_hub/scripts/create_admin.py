"""初期管理者アカウントを作成、または既存なら パスワードを再設定する（upsert）。

実行例:
  python -m scripts.create_admin --email admin@am-teams.com --password 'StrongPass!23' --name 管理者

同じメールで再実行すると、そのユーザーのパスワードを再設定する（admin権限も付与）。
最初の1人はこのスクリプトで作成し、以降は管理者が API（POST /auth/users）から発行する。
"""
from __future__ import annotations

import argparse

from sqlalchemy import select

from app.db.models import Role, TwoFactorMethod, User
from app.db.session import init_db, session_scope
from app.services import auth_service
from app.services.auth_service import AuthError


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--email", required=True)
    p.add_argument("--password", required=True)
    p.add_argument("--name", default="管理者")
    p.add_argument("--phone", default=None)
    p.add_argument("--twofa", choices=["email", "sms"], default="email")
    args = p.parse_args()

    init_db()
    try:
        with session_scope() as s:
            existing = s.scalar(select(User).where(User.email == args.email))
            if existing is None:
                user = auth_service.create_user(
                    s, email=args.email, password=args.password, full_name=args.name,
                    roles=[], phone=args.phone,
                    two_factor_method=TwoFactorMethod(args.twofa), is_admin=True,
                )
                print(f"管理者を作成しました: {user.email} (roles={user.roles})")
            else:
                # 既存ユーザー: パスワードを再設定し、admin権限と有効状態を保証する
                auth_service.change_password(s, user=existing, new_password=args.password)
                if Role.ADMIN.value not in existing.roles:
                    existing.roles = sorted(set(existing.roles) | {Role.ADMIN.value})
                existing.is_active = True
                print(f"既存ユーザーのパスワードを再設定しました: {existing.email} (roles={existing.roles})")
    except AuthError as e:
        print(f"失敗: {e}")


if __name__ == "__main__":
    main()

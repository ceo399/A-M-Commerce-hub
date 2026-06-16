"""動作確認用の初期データ投入（開発用）。

管理者＋各ロールのユーザー＋サンプル商品/在庫を作成し、/docs から
すぐに触れる状態にする。本番では使わないこと。

実行: python -m scripts.dev_seed
その後: DEV_ECHO_OTP=1 uvicorn app.api.main:app --reload  → http://localhost:8000/docs
"""
from __future__ import annotations

from app.db.models import Product, ProductStatus, Role, TwoFactorMethod
from app.db.session import init_db, session_scope
from app.services import auth_service, inventory_service
from app.services.auth_service import AuthError

PASSWORD = "DevPass!234"
USERS = [
    ("admin@am-teams.com", [], True, "管理者"),
    ("logi@am-teams.com", [Role.LOGISTICS], False, "物流担当"),
    ("buy@am-teams.com", [Role.PURCHASING], False, "購買担当"),
    ("md@am-teams.com", [Role.MERCHANDISING], False, "商品企画(MD)"),
    ("mkt@am-teams.com", [Role.MARKETING], False, "マーケ担当"),
]
SAMPLE_PRODUCTS = [
    {"sku": "SKU-DEMO-001", "name": "デモ商品（受注テスト用）", "stock": 6},
    {"sku": "SKU-CABLE-XLR-3M", "name": "XLRマイクケーブル 3m", "stock": 25,
     "manufacturer": "Cordial", "brand": "Cordial", "list_price": 2200,
     "status": ProductStatus.ACTIVE},
]


def main() -> None:
    init_db()
    with session_scope() as s:
        for email, roles, is_admin, name in USERS:
            try:
                auth_service.create_user(s, email=email, password=PASSWORD, full_name=name,
                                         roles=roles, is_admin=is_admin,
                                         two_factor_method=TwoFactorMethod.EMAIL)
            except AuthError:
                pass  # 既存ならスキップ
        for p in SAMPLE_PRODUCTS:
            from sqlalchemy import select
            if s.scalar(select(Product).where(Product.sku == p["sku"])):
                continue
            product = Product(
                sku=p["sku"], name=p["name"], manufacturer=p.get("manufacturer"),
                brand=p.get("brand"), list_price=p.get("list_price"),
                status=p.get("status", ProductStatus.DRAFT))
            s.add(product); s.flush()
            if p.get("stock"):
                inventory_service.receive_stock(s, product.id, p["stock"], ref_type="seed")

    print("=== 投入完了 ===")
    print(f"共通パスワード: {PASSWORD}")
    for email, roles, is_admin, name in USERS:
        label = "admin" if is_admin else (roles[0].value if roles else "-")
        print(f"  {email:24s} role={label:13s} ({name})")
    print("\n次の手順:")
    print("  DEV_ECHO_OTP=1 uvicorn app.api.main:app --reload")
    print("  → http://localhost:8000/docs を開いて確認")


if __name__ == "__main__":
    main()

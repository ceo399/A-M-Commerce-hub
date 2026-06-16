"""???????????????????

????????????????????/???????/docs ??
???????????????????????

??: python -m scripts.dev_seed
???: DEV_ECHO_OTP=1 uvicorn app.api.main:app --reload  ? http://localhost:8000/docs
"""
from __future__ import annotations

from app.db.models import Product, ProductStatus, Role, TwoFactorMethod
from app.db.session import init_db, session_scope
from app.services import auth_service, inventory_service
from app.services.auth_service import AuthError

PASSWORD = "DevPass!234"
USERS = [
    ("admin@am-teams.com", [], True, "???"),
    ("logi@am-teams.com", [Role.LOGISTICS], False, "????"),
    ("buy@am-teams.com", [Role.PURCHASING], False, "????"),
    ("md@am-teams.com", [Role.MERCHANDISING], False, "????(MD)"),
    ("mkt@am-teams.com", [Role.MARKETING], False, "?????"),
]
SAMPLE_PRODUCTS = [
    {"sku": "SKU-DEMO-001", "name": "????????????", "stock": 6},
    {"sku": "SKU-CABLE-XLR-3M", "name": "XLR??????? 3m", "stock": 25,
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
                pass  # ????????
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

    print("=== ???? ===")
    print(f"???????: {PASSWORD}")
    for email, roles, is_admin, name in USERS:
        label = "admin" if is_admin else (roles[0].value if roles else "-")
        print(f"  {email:24s} role={label:13s} ({name})")
    print("\n????:")
    print("  DEV_ECHO_OTP=1 uvicorn app.api.main:app --reload")
    print("  ? http://localhost:8000/docs ??????")


if __name__ == "__main__":
    main()

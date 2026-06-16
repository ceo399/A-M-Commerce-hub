"""ダッシュボード/在庫に参考用のサンプル数値を投入する（モック・開発用）。

複数商品・各在庫ステート（自社/FBA）・3P受注を作成し、ダッシュボードと
在庫画面にそれらしい数値が並ぶようにする。再実行しても重複しない（冪等）。
本番では使わないこと。

実行: python -m scripts.seed_reference
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.db.models import (
    InventorySnapshot,
    InventoryState as S,
    Product,
    ProductStatus,
    SellerOrder,
    SellerOrderLine,
)
from app.db.session import init_db, session_scope
from app.services import inventory_service

NOW = datetime.now(timezone.utc)

# sku, 名称, ブランド, メーカー, 想定売価, [own_avail, own_reserved, fba_fulfillable, fba_inbound, fba_reserved]
PRODUCTS = [
    ("HAMA-USB-C-1M",   "hama USB-C 充電ケーブル 1m",  "hama",    "hama",    1280, [120, 10, 80, 20, 5]),
    ("HAMA-HDMI-2M",    "hama HDMI 2.1 ケーブル 2m",   "hama",    "hama",    2480, [60,  5,  40, 0,  3]),
    ("DURABLE-TRAY-A4", "DURABLE レタートレイ A4",      "DURABLE", "DURABLE", 1980, [200, 0,  150,30, 0]),
    ("DURABLE-BADGE",   "DURABLE 名札ホルダー 50枚",    "DURABLE", "DURABLE", 3200, [90,  12, 60, 0,  4]),
    ("CORDIAL-XLR-3M",  "Cordial XLRマイクケーブル 3m", "Cordial", "Cordial", 2200, [45,  8,  30, 10, 2]),
    ("CORDIAL-TRS-5M",  "Cordial TRSケーブル 5m",       "Cordial", "Cordial", 3400, [25,  3,  18, 0,  1]),
    ("HAMA-MOUSE-W",    "hama ワイヤレスマウス",        "hama",    "hama",    1680, [75,  6,  50, 15, 2]),
    ("DURABLE-STAND",   "DURABLE モニタースタンド",     "DURABLE", "DURABLE", 5600, [30,  2,  22, 5,  1]),
]

# amazon_order_id, 区分(AFN/MFN), ステータス, [(sku, 数量, 単価)], 何日前
SELLER_ORDERS = [
    ("249-0000001-0001001", "AFN", "Shipped",   [("HAMA-USB-C-1M", 2, 1280), ("HAMA-MOUSE-W", 1, 1680)], 1),
    ("249-0000002-0001002", "AFN", "Shipped",   [("CORDIAL-XLR-3M", 1, 2200)], 1),
    ("249-0000003-0001003", "MFN", "Unshipped", [("DURABLE-TRAY-A4", 3, 1980)], 0),
    ("249-0000004-0001004", "AFN", "Shipped",   [("DURABLE-BADGE", 1, 3200), ("DURABLE-STAND", 1, 5600)], 2),
    ("249-0000005-0001005", "AFN", "Shipped",   [("HAMA-HDMI-2M", 2, 2480)], 3),
    ("249-0000006-0001006", "MFN", "Shipped",   [("CORDIAL-TRS-5M", 1, 3400)], 4),
]

_STATES = [S.OWN_WAREHOUSE_AVAILABLE, S.OWN_WAREHOUSE_RESERVED,
           S.FBA_FULFILLABLE, S.FBA_INBOUND, S.FBA_RESERVED]
_FBA_SNAPSHOT = [S.FBA_FULFILLABLE, S.FBA_INBOUND, S.FBA_RESERVED]


def main() -> None:
    init_db()
    new_products = 0
    with session_scope() as s:
        prod_by_sku: dict[str, Product] = {}
        for sku, name, brand, maker, price, qtys in PRODUCTS:
            p = s.scalar(select(Product).where(Product.sku == sku))
            if p is None:
                p = Product(sku=sku, name=name, brand=brand, manufacturer=maker,
                            list_price=price, status=ProductStatus.ACTIVE)
                s.add(p); s.flush()
                new_products += 1
                # 在庫台帳: 各ステートへ独立に積む（追記専用）
                for state, q in zip(_STATES, qtys):
                    if q:
                        inventory_service._move(s, p.id, None, state, q, "seed",
                                                ref_type="seed", source_system="seed")
                # FBA絶対値スナップショット（突合用）
                for state in _FBA_SNAPSHOT:
                    q = qtys[_STATES.index(state)]
                    if q:
                        s.add(InventorySnapshot(product_id=p.id, state=state, quantity=q,
                                                source_system="seed", captured_at=NOW))
            prod_by_sku[sku] = p
        s.flush()

        new_orders = 0
        for oid, channel, status, lines, days_ago in SELLER_ORDERS:
            if s.scalar(select(SellerOrder).where(SellerOrder.amazon_order_id == oid)):
                continue
            so = SellerOrder(amazon_order_id=oid, fulfillment_channel=channel,
                             order_status=status, source_system="seed",
                             purchase_date=NOW - timedelta(days=days_ago))
            s.add(so); s.flush()
            for sku, qty, price in lines:
                p = prod_by_sku.get(sku)
                s.add(SellerOrderLine(seller_order_id=so.id, product_id=(p.id if p else None),
                                      sku=sku, quantity=qty, item_price=price))
            new_orders += 1

    with session_scope() as s:
        prods = s.scalars(select(Product)).all()
        orders = s.scalars(select(SellerOrder)).all()
        bals = inventory_service.all_balances(s)
        total_units = sum(sum(b.by_state.values()) for b in bals.values())
        order_amount = 0.0
        for so in orders:
            for ln in so.lines:
                order_amount += float(ln.item_price or 0) * ln.quantity
        print("=== 参考データ投入 完了 ===")
        print(f"商品: {len(prods)}件 (今回新規 {new_products}件)")
        print(f"3P受注(SellerOrder): {len(orders)}件 (今回新規 {new_orders}件)")
        print(f"在庫台帳の合計ユニット(全ステート): {total_units}")
        print(f"3P受注の総額(参考): ¥{order_amount:,.0f}")


if __name__ == "__main__":
    main()

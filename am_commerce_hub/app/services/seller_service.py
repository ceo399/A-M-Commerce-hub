"""3P（Seller）取込サービス。

FBA Inventory API 由来の絶対在庫を inventory_snapshots へ取り込む。
台帳(StockMovement)との差分reconcile（drift→補正movement）は次段で実装する。
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import (
    InventorySnapshot,
    InventoryState,
    Product,
    SellerOrder,
    SellerOrderLine,
)
from app.integrations.registry import Integrations, get_integrations
from app.services import inventory_service

S = InventoryState


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _resolve_product(session: Session, sku: str | None, asin: str | None) -> Product | None:
    product = None
    if sku:
        product = session.scalar(select(Product).where(Product.sku == sku))
    if product is None and asin:
        product = session.scalar(select(Product).where(Product.asin == asin))
    return product


def ingest_fba_snapshots(session: Session, integ: Integrations | None = None) -> int:
    """FBAの絶対在庫を取得し、ステート別の InventorySnapshot として記録。記録件数を返す。"""
    integ = get_integrations(integ)
    captured = datetime.now(timezone.utc)
    written = 0
    for row in integ.fba_inventory.fetch_inventory():
        product = _resolve_product(session, row.seller_sku, row.asin)
        if product is None:
            continue  # マスタ未登録のSKU/ASINはスキップ（名寄せ後に取り込む想定）
        for state, qty in [
            (S.FBA_FULFILLABLE, row.fulfillable),
            (S.FBA_INBOUND, row.inbound),
            (S.FBA_RESERVED, row.reserved),
            (S.FBA_UNFULFILLABLE, row.unfulfillable),
        ]:
            session.add(InventorySnapshot(
                product_id=product.id, state=state, quantity=qty,
                source_system="sp_api_seller_fba", captured_at=captured,
            ))
            written += 1
    session.flush()
    return written


def _apply_order_to_ledger(session: Session, so: SellerOrder) -> None:
    """3P注文を在庫台帳へ連動。

    - AFN(FBA): 自社倉庫在庫は動かさない（AmazonがFBA在庫から引当→reconcileで反映）。
    - MFN(自社出荷): 自社倉庫から引当(allocate)。注文が出荷済みなら出荷(ship_allocated)まで進める。
      在庫不足分はここでは引当せず残す（バックオーダーは別途）。
    """
    if (so.fulfillment_channel or "").upper() == "AFN":
        return
    shipped = (so.order_status or "").lower() in ("shipped", "partiallyshipped")
    for ln in so.lines:
        if ln.product_id is None or ln.quantity <= 0:
            continue
        bal = inventory_service.get_balance(session, ln.product_id)
        take = min(ln.quantity, bal.available)
        if take <= 0:
            continue
        inventory_service.allocate(
            session, ln.product_id, take, ref_type="seller_order_line", ref_id=ln.id,
            source_system="sp_api_seller", source_event_id=f"so:{so.id}:line:{ln.id}:alloc")
        if shipped:
            inventory_service.ship_allocated(
                session, ln.product_id, take, ref_type="seller_order_line", ref_id=ln.id,
                source_system="sp_api_seller", source_event_id=f"so:{so.id}:line:{ln.id}:ship")


def ingest_seller_orders(session: Session, integ: Integrations | None = None,
                         apply_ledger: bool = True) -> int:
    """3P注文(getOrders)を取り込み、SellerOrder/SellerOrderLineへ着地。新規取込件数を返す。

    amazon_order_id で冪等（既存はスキップ）。明細の商品はsku→asinで名寄せ（未登録はproduct_id=None）。
    apply_ledger=True のとき、新規注文を出荷経路で分岐して在庫台帳へ連動する。
    """
    integ = get_integrations(integ)
    created = 0
    for o in integ.seller.fetch_orders():
        exists = session.scalar(
            select(SellerOrder.id).where(SellerOrder.amazon_order_id == o.amazon_order_id)
        )
        if exists:
            continue
        so = SellerOrder(
            amazon_order_id=o.amazon_order_id,
            purchase_date=_parse_dt(o.purchase_date),
            order_status=o.order_status,
            fulfillment_channel=o.fulfillment_channel,
            source_system="sp_api_seller",
        )
        session.add(so)
        session.flush()  # so.id 採番
        for ln in o.lines:
            product = _resolve_product(session, ln.sku, ln.asin)
            session.add(SellerOrderLine(
                seller_order_id=so.id,
                product_id=product.id if product else None,
                sku=ln.sku, asin=ln.asin,
                quantity=ln.quantity, item_price=ln.item_price,
            ))
        session.flush()  # line.id 採番（台帳連動の参照に必要）
        if apply_ledger:
            _apply_order_to_ledger(session, so)
        created += 1
    session.flush()
    return created

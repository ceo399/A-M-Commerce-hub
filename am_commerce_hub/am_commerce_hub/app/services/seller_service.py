"""3P?Seller????????

FBA Inventory API ???????? inventory_snapshots ??????
(StockMovement)????reconcile?drift???movement??????????
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
    """FBA???????????????? InventorySnapshot ??????????????"""
    integ = get_integrations(integ)
    captured = datetime.now(timezone.utc)
    written = 0
    for row in integ.fba_inventory.fetch_inventory():
        product = _resolve_product(session, row.seller_sku, row.asin)
        if product is None:
            continue  # ???????SKU/ASIN??????????????????
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
    """3P???????????

    - AFN(FBA): ?????????????Amazon?FBA???????reconcile?????
    - MFN(????): ????????(allocate)????????????(ship_allocated)??????
      ?????????????????????????????
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
    """3P??(getOrders)??????SellerOrder/SellerOrderLine??????????????

    amazon_order_id ???????????????????sku?asin?????????product_id=None??
    apply_ledger=True ????????????????????????????
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
        session.flush()  # so.id ??
        for ln in o.lines:
            product = _resolve_product(session, ln.sku, ln.asin)
            session.add(SellerOrderLine(
                seller_order_id=so.id,
                product_id=product.id if product else None,
                sku=ln.sku, asin=ln.asin,
                quantity=ln.quantity, item_price=ln.item_price,
            ))
        session.flush()  # line.id ??????????????
        if apply_ledger:
            _apply_order_to_ledger(session, so)
        created += 1
    session.flush()
    return created

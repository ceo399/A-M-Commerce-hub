"""??????????2??

Amazon????????? ? Vendor Orders API ?PO??
 ? ????DB?????
    ? ???? ? ??????? ? PO Ack ? ?????? ? ??????&??
    ? ???? ? ????????? ? ???????purchasing_service?
 ? AIS?????CSV ? ASN?? ? Invoice?? ? ??DB??
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import (
    ApprovalType,
    OrderLine,
    OrderLineStatus,
    OrderStatus,
    Product,
    PurchaseOrder,
)
from app.integrations.registry import Integrations, get_integrations
from app.services import approval_service, inventory_service, purchasing_service


def ingest_new_pos(session: Session, integ: Integrations | None = None) -> list[PurchaseOrder]:
    """Vendor API????PO???????????????"""
    integ = get_integrations(integ)
    created: list[PurchaseOrder] = []
    for inc in integ.vendor.fetch_new_pos():
        if session.scalar(select(PurchaseOrder).where(
                PurchaseOrder.amazon_po_number == inc.amazon_po_number)):
            continue  # ????????
        po = PurchaseOrder(amazon_po_number=inc.amazon_po_number, ship_to=inc.ship_to)
        session.add(po)
        session.flush()
        for line in inc.lines:
            product = session.scalar(select(Product).where(Product.sku == line.sku))
            if product is None:
                # ???????SKU?????????????????????????
                continue
            session.add(OrderLine(
                order_id=po.id, product_id=product.id,
                qty_ordered=line.quantity, unit_price=line.unit_price,
            ))
        session.flush()
        _match_inventory(session, po, integ)
        created.append(po)
    return created


def _match_inventory(session: Session, po: PurchaseOrder, integ: Integrations) -> None:
    """????DB???????????????????????????"""
    po.status = OrderStatus.MATCHING
    confirmed: dict[str, int] = {}
    has_backorder = False

    for line in po.lines:
        product = session.get(Product, line.product_id)
        allocated = inventory_service.allocate(
            session, product.id, line.qty_ordered, ref_type="order_line", ref_id=line.id
        )
        line.qty_confirmed = allocated
        line.qty_backordered = line.qty_ordered - allocated
        confirmed[product.sku] = allocated

        # ????????????ALLOCATED?????????????
        line.status = (OrderLineStatus.ALLOCATED if line.qty_confirmed > 0
                       else OrderLineStatus.BACKORDERED)

        if line.qty_backordered > 0:
            has_backorder = True
            # ????? ? ??????????????????
            purchasing_service.create_supplier_po(
                session, product=product, quantity=line.qty_backordered, order_line=line
            )

    # PO Acknowledgement??????????
    integ.vendor.acknowledge_po(po.amazon_po_number, confirmed)

    po.status = (OrderStatus.PARTIALLY_BACKORDERED if has_backorder
                 else OrderStatus.READY_TO_SHIP)

    # ????????????????????????????
    if any(l.status == OrderLineStatus.ALLOCATED for l in po.lines):
        approval_service.create_task(
            session, ApprovalType.DAILY_SHIPMENT, ref_type="purchase_order",
            ref_id=po.id, summary=f"{po.amazon_po_number} ???? ????",
        )


def confirm_shipment(session: Session, po_id: int, integ: Integrations | None = None) -> PurchaseOrder:
    """?????: ??????&?? ? AIS???? ? ASN ? Invoice?"""
    integ = get_integrations(integ)
    po = session.get(PurchaseOrder, po_id)
    if po is None:
        raise ValueError(f"PurchaseOrder {po_id} ????????")

    ship_lines: dict[str, int] = {}
    total_amount = 0.0
    csv_rows = []
    for line in po.lines:
        if line.status != OrderLineStatus.ALLOCATED:
            continue
        product = session.get(Product, line.product_id)
        inventory_service.ship_allocated(
            session, product.id, line.qty_confirmed, ref_type="order_line", ref_id=line.id
        )
        line.status = OrderLineStatus.SHIPPED
        ship_lines[product.sku] = line.qty_confirmed
        amount = float(line.unit_price or 0) * line.qty_confirmed
        total_amount += amount
        csv_rows.append({"sku": product.sku, "asin": product.asin,
                         "qty": line.qty_confirmed, "amount": amount})

    if not ship_lines:
        return po  # ???????

    # AIS?????CSV???SFTP?
    integ.shipping_hub.send_shipment_csv(po.amazon_po_number, csv_rows)
    po.status = OrderStatus.PACKED
    # ASN??
    po.asn_id = integ.vendor.send_asn(po.amazon_po_number, ship_lines)
    po.status = OrderStatus.SHIPPED
    # Invoice??
    po.invoice_id = integ.vendor.send_invoice(po.amazon_po_number, total_amount)
    po.status = OrderStatus.INVOICED
    return po

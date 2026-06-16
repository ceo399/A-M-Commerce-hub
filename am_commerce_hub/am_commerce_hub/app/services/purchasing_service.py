"""??????????2???????????

 ? ???????????? ? ??????????
 ? ???????????????? ? ??
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.db.models import (
    ApprovalType,
    OrderLine,
    Product,
    SupplierPOStatus,
    SupplierPurchaseOrder,
)
from app.services import approval_service, inventory_service


def create_supplier_po(session: Session, product: Product, quantity: int,
                       order_line: OrderLine | None = None) -> SupplierPurchaseOrder:
    """???????????????????????????"""
    spo = SupplierPurchaseOrder(
        supplier_name=product.manufacturer or "???????",
        product_id=product.id,
        quantity=quantity,
        related_order_line_id=order_line.id if order_line else None,
        status=SupplierPOStatus.PENDING_APPROVAL,
    )
    session.add(spo)
    session.flush()
    approval_service.create_task(
        session, ApprovalType.SUPPLIER_PO, ref_type="supplier_po", ref_id=spo.id,
        summary=f"{product.manufacturer or '????'} ? {product.sku} ? {quantity} ??",
    )
    return spo


def mark_approved(session: Session, supplier_po_id: int) -> SupplierPurchaseOrder:
    spo = session.get(SupplierPurchaseOrder, supplier_po_id)
    spo.status = SupplierPOStatus.APPROVED
    return spo


def receive_goods(session: Session, supplier_po_id: int) -> SupplierPurchaseOrder:
    """????????????? ? ??????"""
    spo = session.get(SupplierPurchaseOrder, supplier_po_id)
    if spo.status != SupplierPOStatus.APPROVED:
        raise ValueError("???????????????")
    inventory_service.receive_stock(
        session, spo.product_id, spo.quantity, ref_type="supplier_po", ref_id=spo.id
    )
    spo.status = SupplierPOStatus.RECEIVED
    return spo

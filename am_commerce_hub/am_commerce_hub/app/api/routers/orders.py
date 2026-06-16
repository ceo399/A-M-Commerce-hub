"""??(Amazon PO) API?"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select

from app.api import serializers as ser
from app.api.deps import get_current_user, require_roles
from app.db.models import OrderStatus, PurchaseOrder, Role
from app.db.session import session_scope
from app.services import order_service

router = APIRouter(prefix="/orders", tags=["orders"])


@router.get("")
def list_orders(status: OrderStatus | None = None, limit: int = Query(50, le=200), offset: int = 0,
                _=Depends(get_current_user)):
    with session_scope() as s:
        stmt = select(PurchaseOrder).order_by(PurchaseOrder.id.desc())
        if status:
            stmt = stmt.where(PurchaseOrder.status == status)
        return ser.paginate(s, stmt, limit, offset, ser.order_dict)


@router.get("/{order_id}")
def get_order(order_id: int, _=Depends(get_current_user)):
    with session_scope() as s:
        po = s.get(PurchaseOrder, order_id)
        if po is None:
            raise HTTPException(404, "??????????")
        return ser.order_dict(po, with_lines=True)


@router.post("/ingest")
def ingest_orders(_=Depends(require_roles(Role.LOGISTICS))):
    """Vendor API????PO???????????PO Ack??????????"""
    with session_scope() as s:
        pos = order_service.ingest_new_pos(s)
        return {"ingested": [ser.order_dict(p, with_lines=True) for p in pos]}

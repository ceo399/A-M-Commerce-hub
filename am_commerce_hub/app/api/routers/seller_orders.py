"""3P??(Seller Central: FBA / ????) API?1P?PurchaseOrder?????"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select

from app.api import serializers as ser
from app.api.deps import get_current_user
from app.db.models import SellerOrder
from app.db.session import session_scope

router = APIRouter(prefix="/seller-orders", tags=["seller-orders"])


@router.get("")
def list_seller_orders(limit: int = Query(100, le=500), offset: int = 0,
                       _=Depends(get_current_user)):
    with session_scope() as s:
        stmt = select(SellerOrder).order_by(SellerOrder.id.desc())
        return ser.paginate(s, stmt, limit, offset, ser.seller_order_dict)


@router.get("/{order_id}")
def get_seller_order(order_id: int, _=Depends(get_current_user)):
    with session_scope() as s:
        so = s.get(SellerOrder, order_id)
        if so is None:
            raise HTTPException(404, "3P??????????")
        return ser.seller_order_dict(so, with_lines=True)

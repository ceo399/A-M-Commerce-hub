"""?? API??????????????? StockMovement ??????????"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.api import serializers as ser
from app.api.deps import get_current_user, require_roles
from app.db.models import Product, Role, StockMovement
from app.db.session import session_scope
from app.services import inventory_service

router = APIRouter(prefix="/inventory", tags=["inventory"])


class ReceiveBody(BaseModel):
    quantity: int = Field(gt=0)
    note: str | None = None


class AdjustBody(BaseModel):
    delta: int = Field(description="?????=???=??")
    note: str | None = None


@router.get("")
def list_inventory(low_stock: bool = False, limit: int = Query(50, le=5000), offset: int = 0,
                   _=Depends(get_current_user)):
    with session_scope() as s:
        balances = inventory_service.all_balances(s)
        products = s.scalars(select(Product).order_by(Product.id)).all()
        rows = []
        for p in products:
            bal = balances.get(p.id) or inventory_service.StockBalance(product_id=p.id)
            bal.reorder_point = p.reorder_point
            if low_stock and not (bal.available <= p.reorder_point):
                continue
            rows.append(ser.inventory_dict(bal, p))
        total = len(rows)
        return {"items": rows[offset:offset + limit], "total": total,
                "limit": limit, "offset": offset}


@router.get("/{product_id}")
def get_inventory(product_id: int, _=Depends(get_current_user)):
    with session_scope() as s:
        product = s.get(Product, product_id)
        if product is None:
            raise HTTPException(404, "??????????")
        bal = inventory_service.get_balance(s, product_id)
        return ser.inventory_dict(bal, product)


@router.get("/{product_id}/movements")
def list_movements(product_id: int, limit: int = Query(50, le=200), offset: int = 0,
                   _=Depends(get_current_user)):
    with session_scope() as s:
        stmt = (select(StockMovement)
                .where(StockMovement.product_id == product_id)
                .order_by(StockMovement.id.desc()))
        return ser.paginate(s, stmt, limit, offset, ser.movement_dict)


@router.post("/{product_id}/receive")
def receive(product_id: int, body: ReceiveBody, _=Depends(require_roles(Role.LOGISTICS))):
    with session_scope() as s:
        product = s.get(Product, product_id)
        if product is None:
            raise HTTPException(404, "??????????")
        inventory_service.receive_stock(s, product_id, body.quantity, ref_type="manual")
        bal = inventory_service.get_balance(s, product_id)
        return ser.inventory_dict(bal, product)


@router.post("/{product_id}/adjust")
def adjust(product_id: int, body: AdjustBody, _=Depends(require_roles(Role.LOGISTICS))):
    """???????available ? delta ????????????"""
    with session_scope() as s:
        product = s.get(Product, product_id)
        if product is None:
            raise HTTPException(404, "??????????")
        try:
            bal = inventory_service.adjust(s, product_id, body.delta, ref_type="manual", note=body.note)
        except ValueError as e:
            raise HTTPException(400, str(e))
        return ser.inventory_dict(bal, product)

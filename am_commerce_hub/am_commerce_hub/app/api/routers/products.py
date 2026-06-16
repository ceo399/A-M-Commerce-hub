"""????? API?"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import or_, select

from app.api import serializers as ser
from app.api.deps import get_current_user, require_roles
from app.db.models import Product, ProductStatus, Role
from app.db.session import session_scope

router = APIRouter(prefix="/products", tags=["products"])


class ProductBody(BaseModel):
    sku: str
    name: str
    jan: str | None = None
    manufacturer: str | None = None
    brand: str | None = None
    list_price: float | None = None
    cost_price: float | None = None
    spec: dict = {}


class ProductPatch(BaseModel):
    name: str | None = None
    jan: str | None = None
    manufacturer: str | None = None
    brand: str | None = None
    list_price: float | None = None
    cost_price: float | None = None
    spec: dict | None = None
    status: ProductStatus | None = None


@router.get("")
def list_products(q: str | None = None, status: ProductStatus | None = None,
                  limit: int = Query(50, le=200), offset: int = 0,
                  _=Depends(get_current_user)):
    with session_scope() as s:
        stmt = select(Product).order_by(Product.id.desc())
        if status:
            stmt = stmt.where(Product.status == status)
        if q:
            like = f"%{q}%"
            stmt = stmt.where(or_(Product.sku.ilike(like), Product.name.ilike(like),
                                  Product.jan.ilike(like), Product.asin.ilike(like)))
        return ser.paginate(s, stmt, limit, offset, ser.product_dict)


@router.get("/{product_id}")
def get_product(product_id: int, _=Depends(get_current_user)):
    with session_scope() as s:
        p = s.get(Product, product_id)
        if p is None:
            raise HTTPException(404, "??????????")
        return ser.product_dict(p)


@router.post("", status_code=201)
def create_product(body: ProductBody, _=Depends(require_roles(Role.MERCHANDISING))):
    with session_scope() as s:
        if s.scalar(select(Product).where(Product.sku == body.sku)):
            raise HTTPException(400, "??SKU????????")
        p = Product(**body.model_dump())
        s.add(p); s.flush()
        return ser.product_dict(p)


@router.patch("/{product_id}")
def update_product(product_id: int, body: ProductPatch,
                   _=Depends(require_roles(Role.MERCHANDISING))):
    with session_scope() as s:
        p = s.get(Product, product_id)
        if p is None:
            raise HTTPException(404, "??????????")
        for k, v in body.model_dump(exclude_unset=True).items():
            setattr(p, k, v)
        s.flush()
        return ser.product_dict(p)

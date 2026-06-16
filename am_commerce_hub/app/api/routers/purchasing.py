"""メーカー発注 API。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select

from app.api import serializers as ser
from app.api.deps import get_current_user, require_roles
from app.db.models import Role, SupplierPOStatus, SupplierPurchaseOrder
from app.db.session import session_scope
from app.services import purchasing_service

router = APIRouter(prefix="/supplier-pos", tags=["purchasing"])


@router.get("")
def list_supplier_pos(status: SupplierPOStatus | None = None,
                      limit: int = Query(50, le=200), offset: int = 0,
                      _=Depends(get_current_user)):
    with session_scope() as s:
        stmt = select(SupplierPurchaseOrder).order_by(SupplierPurchaseOrder.id.desc())
        if status:
            stmt = stmt.where(SupplierPurchaseOrder.status == status)
        return ser.paginate(s, stmt, limit, offset, ser.supplier_po_dict)


@router.get("/{spo_id}")
def get_supplier_po(spo_id: int, _=Depends(get_current_user)):
    with session_scope() as s:
        spo = s.get(SupplierPurchaseOrder, spo_id)
        if spo is None:
            raise HTTPException(404, "発注が見つかりません")
        return ser.supplier_po_dict(spo)


@router.post("/{spo_id}/receive")
def receive_goods(spo_id: int, _=Depends(require_roles(Role.PURCHASING, Role.LOGISTICS))):
    """メーカー入荷・検品完了 → 在庫へ入庫。承認済みの発注のみ。"""
    with session_scope() as s:
        spo = s.get(SupplierPurchaseOrder, spo_id)
        if spo is None:
            raise HTTPException(404, "発注が見つかりません")
        try:
            spo = purchasing_service.receive_goods(s, spo_id)
        except ValueError as e:
            raise HTTPException(400, str(e))
        return ser.supplier_po_dict(spo)

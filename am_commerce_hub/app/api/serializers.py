"""モデル → 辞書のシリアライザと、ページネーション補助。

レスポンスは辞書で返す（OpenAPIの厳密な型付けより記述量を優先）。
セッションが開いている間に呼ぶこと（リレーション参照のため）。
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session


def _num(v) -> float | None:
    return float(v) if v is not None else None


def product_dict(p) -> dict:
    return {
        "id": p.id, "sku": p.sku, "jan": p.jan, "asin": p.asin,
        "name": p.name, "manufacturer": p.manufacturer, "brand": p.brand,
        "status": p.status.value, "list_price": _num(p.list_price),
        "cost_price": _num(p.cost_price), "spec": p.spec or {},
        "reorder_point": p.reorder_point,
        "stock": None,  # 在庫は /inventory/{product_id} で取得（台帳から導出）
    }


def inventory_dict(bal, product=None) -> dict:
    """StockBalance（台帳から導出した残高）を辞書化。"""
    return {"product_id": bal.product_id,
            "on_hand": bal.on_hand, "allocated": bal.allocated,
            "available": bal.available, "reorder_point": bal.reorder_point,
            "by_state": bal.by_state,
            "sku": product.sku if product else None,
            "name": product.name if product else None,
            "status": product.status.value if product else None}


def movement_dict(m) -> dict:
    return {"id": m.id, "product_id": m.product_id,
            "from_state": m.from_state.value if m.from_state else None,
            "to_state": m.to_state.value if m.to_state else None,
            "quantity": m.quantity, "reason": m.reason,
            "ref_type": m.ref_type, "ref_id": m.ref_id, "note": m.note,
            "created_at": m.created_at.isoformat() if m.created_at else None}


def order_line_dict(l) -> dict:
    return {"id": l.id, "product_id": l.product_id, "qty_ordered": l.qty_ordered,
            "qty_confirmed": l.qty_confirmed, "qty_backordered": l.qty_backordered,
            "unit_price": _num(l.unit_price), "status": l.status.value}


def order_dict(po, with_lines: bool = False) -> dict:
    d = {"id": po.id, "amazon_po_number": po.amazon_po_number, "status": po.status.value,
         "ship_to": po.ship_to, "asn_id": po.asn_id, "invoice_id": po.invoice_id}
    if with_lines:
        d["lines"] = [order_line_dict(l) for l in po.lines]
    return d


def seller_order_dict(so, with_lines: bool = False) -> dict:
    """3P受注(SellerOrder)。合計金額 = Σ(単価×数量)。"""
    total = sum(float(l.item_price or 0) * l.quantity for l in so.lines)
    d = {"id": so.id, "amazon_order_id": so.amazon_order_id,
         "purchase_date": so.purchase_date.isoformat() if so.purchase_date else None,
         "order_status": so.order_status,
         "fulfillment_channel": so.fulfillment_channel,
         "line_count": len(so.lines), "total": total}
    if with_lines:
        d["lines"] = [{"sku": l.sku, "asin": l.asin, "quantity": l.quantity,
                       "item_price": _num(l.item_price)} for l in so.lines]
    return d


def supplier_po_dict(spo) -> dict:
    return {"id": spo.id, "supplier_name": spo.supplier_name, "product_id": spo.product_id,
            "quantity": spo.quantity, "related_order_line_id": spo.related_order_line_id,
            "status": spo.status.value}


def listing_draft_dict(d) -> dict:
    return {"id": d.id, "product_id": d.product_id, "title": d.title,
            "bullet_points": d.bullet_points or [], "description": d.description,
            "status": d.status.value, "generated_by": d.generated_by,
            "published_asin": d.published_asin}


def ad_entity_dict(e) -> dict:
    return {"id": e.id, "amazon_entity_id": e.amazon_entity_id,
            "type": e.entity_type.value, "name": e.name,
            "current_bid": _num(e.current_bid), "daily_budget": _num(e.daily_budget),
            "is_brand_awareness": e.is_brand_awareness}


def bid_rec_dict(r) -> dict:
    return {"id": r.id, "ad_entity_id": r.ad_entity_id, "action": r.action,
            "current_bid": _num(r.current_bid), "recommended_bid": _num(r.recommended_bid),
            "measured_acos": r.measured_acos, "rationale": r.rationale,
            "status": r.status.value,
            "entity_name": (r.entity.name if r.entity else None),
            "amazon_entity_id": (r.entity.amazon_entity_id if r.entity else None)}


def approval_dict(t) -> dict:
    return {"id": t.id, "type": t.approval_type.value, "status": t.status.value,
            "ref_type": t.ref_type, "ref_id": t.ref_id, "summary": t.summary,
            "decided_by": t.decided_by,
            "decided_at": t.decided_at.isoformat() if t.decided_at else None}


def paginate(session: Session, stmt, limit: int, offset: int,
             serializer) -> dict[str, Any]:
    """件数つきページネーション。{items, total, limit, offset} を返す。"""
    total = session.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    items = session.scalars(stmt.limit(limit).offset(offset)).all()
    return {"items": [serializer(x) for x in items],
            "total": int(total), "limit": limit, "offset": offset}

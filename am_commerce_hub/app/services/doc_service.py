"""他チャネル取り込みサービス。

- メールPDF注文: AI抽出結果(信頼度付き)を受け、高信頼のみ SellerOrder へコミット。
  低信頼は自動コミットせず保留情報を返す（永続的な例外キューは次段で追加）。
- Excel在庫表: 棚卸カウントを読み、自社倉庫の引当可能在庫を実数へ合わせる(adjust)。
外部からの取得（メール受信 / Drive・Chat添付）は配信層の責務で、ここは取り込みに専念する。
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Product, SellerOrder, SellerOrderLine
from app.integrations.registry import Integrations, get_integrations
from app.services import inventory_service, seller_service

DEFAULT_CONFIDENCE_THRESHOLD = 0.8


def _resolve_product(session: Session, sku: str | None, asin: str | None) -> Product | None:
    product = None
    if sku:
        product = session.scalar(select(Product).where(Product.sku == sku))
    if product is None and asin:
        product = session.scalar(select(Product).where(Product.asin == asin))
    return product


def ingest_order_document(session: Session, source: str, integ: Integrations | None = None,
                          threshold: float = DEFAULT_CONFIDENCE_THRESHOLD) -> dict:
    """メールPDF注文を取り込む。高信頼のみコミット、低信頼は保留(committed=False)。"""
    integ = get_integrations(integ)
    doc = integ.order_doc.parse(source)
    if doc.confidence < threshold:
        return {"committed": False, "reason": "low_confidence",
                "confidence": doc.confidence, "external_ref": doc.external_ref,
                "lines": len(doc.lines)}

    order_id = f"DOC-{doc.external_ref}"
    if session.scalar(select(SellerOrder.id).where(SellerOrder.amazon_order_id == order_id)):
        return {"committed": False, "reason": "duplicate", "order_id": order_id}

    so = SellerOrder(
        amazon_order_id=order_id, order_status="Unshipped",
        fulfillment_channel=doc.fulfillment_channel or "MFN",
        source_system=doc.source or "email_pdf",
    )
    session.add(so)
    session.flush()
    for ln in doc.lines:
        product = _resolve_product(session, ln.sku, ln.asin)
        session.add(SellerOrderLine(
            seller_order_id=so.id, product_id=product.id if product else None,
            sku=ln.sku, asin=ln.asin, quantity=ln.quantity, item_price=ln.unit_price,
        ))
    session.flush()
    seller_service._apply_order_to_ledger(session, so)  # MFNなら自社在庫を引当
    return {"committed": True, "order_id": order_id, "seller_order_id": so.id,
            "confidence": doc.confidence, "lines": len(doc.lines)}


def ingest_inventory_count(session: Session, source: str,
                           integ: Integrations | None = None) -> dict:
    """Excel在庫表の棚卸カウントを取り込み、自社倉庫の引当可能在庫を実数へ合わせる。"""
    integ = get_integrations(integ)
    updated = 0
    skipped = 0
    adjustments: list[dict] = []
    for row in integ.inventory_doc.parse(source):
        product = _resolve_product(session, row.sku, row.asin)
        if product is None:
            skipped += 1
            continue
        bal = inventory_service.get_balance(session, product.id)
        delta = row.quantity - bal.available
        if delta != 0:
            inventory_service.adjust(session, product.id, delta, ref_type="inventory_count",
                                     source_system="excel_upload", note="Excel棚卸反映")
            adjustments.append({"product_id": product.id, "sku": row.sku,
                                "delta": delta, "to": row.quantity})
        updated += 1
    session.flush()
    return {"updated": updated, "skipped": skipped, "adjustments": adjustments}

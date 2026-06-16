"""カタログサービス（フロー3）。

メーカー提供カタログ CSV/Excel → 最新マスタ取得
 → Amazon登録状況と自動差分照合 / Catalog API
    ├ 生産完了・在庫ゼロ → Amazonへ出品停止・廃盤フラグ
    └ 未登録・新製品 → AIへスペック送信 → タイトル/箇条書き/紹介文 自動生成
       → 出品準備ダッシュボードへ下書き → MD・商品企画責任者の最終承認
       → 画像と共に Listings Items API で出品 → 新規ASIN紐付け・マスタ更新
"""
from __future__ import annotations

from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.db.models import (
    ApprovalType,
    CatalogSyncJob,
    ListingDraft,
    ListingDraftStatus,
    Product,
    ProductStatus,
)
from app.integrations.registry import Integrations, get_integrations
from app.services import approval_service
from app.services.catalog_import import ParseResult, parse_catalog


def sync_catalog(session: Session, filename: str, rows: list[dict],
                 integ: Integrations | None = None) -> CatalogSyncJob:
    """カタログ行を取り込み、Amazon登録状況と照合して分岐処理。

    rows: [{sku, jan, name, manufacturer, brand, in_stock(bool), spec{...}}, ...]
    """
    integ = get_integrations(integ)
    job = CatalogSyncJob(source_filename=filename, total_rows=len(rows))
    session.add(job)
    session.flush()

    new_count = discontinued_count = 0
    for row in rows:
        product = _upsert_product(session, row)
        status = integ.catalog.check_status(product.sku, product.jan, product.asin)

        if not row.get("in_stock", True):
            # 廃盤シグナル（生産終了かつ在庫0）。登録状況より優先。
            # Amazon登録済みなら出品停止フラグを送信。未登録なら出品ドラフトも作らない。
            if status.is_registered and status.asin:
                integ.listings.set_discontinued(status.asin)
            product.status = ProductStatus.DISCONTINUED
            discontinued_count += 1
        elif not status.is_registered:
            # 未登録・新製品 → AIで出品ドラフト生成
            _generate_listing_draft(session, product, integ)
            new_count += 1
        # 既登録・在庫ありはマスタ更新のみ

    job.new_products = new_count
    job.discontinued = discontinued_count
    job.result = {"new": new_count, "discontinued": discontinued_count}
    return job


def _upsert_product(session: Session, row: dict) -> Product:
    product = session.scalar(select(Product).where(Product.sku == row["sku"]))
    if product is None:
        product = Product(sku=row["sku"])
        session.add(product)
    product.jan = row.get("jan") or product.jan
    product.name = row.get("name", product.name or row["sku"])
    product.manufacturer = row.get("manufacturer", product.manufacturer)
    product.brand = row.get("brand", product.brand)
    product.spec = row.get("spec", product.spec or {})
    if row.get("list_price") is not None:
        product.list_price = row["list_price"]
    if row.get("cost_price") is not None:
        product.cost_price = row["cost_price"]
    session.flush()
    return product


def _generate_listing_draft(session: Session, product: Product, integ: Integrations) -> ListingDraft:
    """AIで出品コピーを生成し、MD承認タスクを起票。"""
    spec = dict(product.spec or {})
    spec.setdefault("name", product.name)
    spec.setdefault("brand", product.brand)
    copy = integ.ai.generate_listing_copy(spec)

    draft = ListingDraft(
        product_id=product.id,
        title=copy.get("title"),
        bullet_points=copy.get("bullet_points", []),
        description=copy.get("description"),
        status=ListingDraftStatus.PENDING_APPROVAL,
        generated_by=settings.ai_model,
    )
    session.add(draft)
    session.flush()
    approval_service.create_task(
        session, ApprovalType.LISTING, ref_type="listing_draft", ref_id=draft.id,
        summary=f"出品ドラフト承認: {product.sku} / {draft.title}",
    )
    return draft


def publish_draft(session: Session, draft_id: int, images: list[str] | None = None,
                  integ: Integrations | None = None) -> ListingDraft:
    """承認済みドラフトを Listings Items API で出品し、ASINをマスタへ紐付け。"""
    integ = get_integrations(integ)
    draft = session.get(ListingDraft, draft_id)
    if draft.status != ListingDraftStatus.APPROVED:
        raise ValueError("承認済みドラフトのみ出品できます")
    product = session.get(Product, draft.product_id)

    spec_images = (product.spec or {}).get("images") or []
    asin = integ.listings.publish_listing(
        sku=product.sku, title=draft.title or product.name,
        bullets=draft.bullet_points or [], description=draft.description or "",
        images=images or spec_images,
    )
    draft.published_asin = asin
    draft.status = ListingDraftStatus.PUBLISHED
    product.asin = asin
    product.status = ProductStatus.ACTIVE
    return draft


def import_from_file(session: Session, path: str, sheet: str | None = None,
                     integ: Integrations | None = None) -> dict:
    """テンプレート形式のCSV/Excelを解析し、そのまま取り込む。

    解析エラー行はスキップし、取り込めた行だけを sync_catalog に渡す。
    解析の警告・エラーと取り込み結果をまとめて返す。
    """
    parsed: ParseResult = parse_catalog(path, sheet=sheet)
    job = sync_catalog(session, Path(path).name, parsed.rows, integ=integ)
    return {
        "file": Path(path).name,
        "parse": parsed.summary(),
        "errors": parsed.errors,
        "warnings": parsed.warnings,
        "imported": {"new_products": job.new_products,
                     "discontinued": job.discontinued,
                     "rows_processed": len(parsed.rows)},
    }

"""全フローをモックでエンドツーエンド実行するデモ。

実行: python -m scripts.run_demo
これにより、Amazon資格情報が無くても3フローが通しで動くことを確認できる。
"""
from __future__ import annotations

from datetime import date

from app.db.models import (
    ApprovalStatus,
    ApprovalType,
    ListingDraft,
    ListingDraftStatus,
    Product,
    ProductStatus,
)
from app.db.session import init_db, session_scope
from app.integrations.mock_adapters import (
    MockAds,
    MockAI,
    MockAttribution,
    MockCatalog,
    MockListings,
    MockOtpDelivery,
    MockShippingHub,
    MockVendorOrders,
)
from app.integrations.ports import CatalogStatus, IncomingPO, IncomingPOLine
from app.integrations.registry import Integrations
from app.services import (
    advertising_service,
    approval_service,
    catalog_service,
    inventory_service,
    order_service,
    purchasing_service,
)


def banner(title: str) -> None:
    print("\n" + "=" * 64)
    print(f"  {title}")
    print("=" * 64)


def build_integrations(scripted_po=None, catalog_registry=None) -> Integrations:
    return Integrations(
        vendor=MockVendorOrders(scripted_pos=scripted_po),
        catalog=MockCatalog(registry=catalog_registry or {}),
        listings=MockListings(),
        ads=MockAds(),
        attribution=MockAttribution(),
        shipping_hub=MockShippingHub(),
        ai=MockAI(),
        otp=MockOtpDelivery(),
    )


def demo_catalog_flow(integ: Integrations) -> None:
    banner("フロー3: カタログ自動照合・出品登録")
    rows = [
        {"sku": "SKU-CABLE-XLR-3M", "jan": "4500000000017", "name": "XLRマイクケーブル 3m",
         "manufacturer": "Cordial", "brand": "Cordial", "in_stock": True,
         "spec": {"features": ["ノイズ低減シールド", "金メッキ端子", "3m"]}},
        {"sku": "SKU-OLD-DI-BOX", "jan": "4500000000031", "name": "旧型DIボックス",
         "manufacturer": "Cordial", "brand": "Cordial", "in_stock": False,
         "spec": {}},
    ]
    with session_scope() as s:
        # 旧型は「登録済み」として差分照合させる
        integ.catalog._registry["SKU-OLD-DI-BOX"] = CatalogStatus(
            asin="B0OLDDIBOX", is_registered=True, is_active=True)
        job = catalog_service.sync_catalog(s, "cordial_2026q2.csv", rows, integ=integ)
        print(f"取込: {job.total_rows}行 / 新製品 {job.new_products} / 廃盤 {job.discontinued}")

    # MD承認 → 出品
    with session_scope() as s:
        tasks = approval_service.pending(s, ApprovalType.LISTING)
        for t in tasks:
            approval_service.decide(s, t.id, approved=True, by="MD")
            draft = s.get(ListingDraft, t.ref_id)
            draft.status = ListingDraftStatus.APPROVED
            print(f"承認: {t.summary}")
    with session_scope() as s:
        drafts = [d for d in s.query(ListingDraft).all()
                  if d.status == ListingDraftStatus.APPROVED]
        for d in drafts:
            published = catalog_service.publish_draft(s, d.id, images=["main.jpg"], integ=integ)
            print(f"出品完了: draft#{d.id} -> ASIN {published.published_asin}")


def demo_order_flow(integ: Integrations) -> None:
    banner("フロー2: 受注→出荷→請求（即納＋バックオーダー混在）")
    # 在庫を仕込む: XLRケーブルは5本だけ在庫（10本注文 → 5即納/5不足）
    with session_scope() as s:
        product = s.query(Product).filter_by(sku="SKU-CABLE-XLR-3M").first()
        inventory_service.receive_stock(s, product.id, 5, ref_type="seed")
        print(f"初期在庫: {product.sku} = 5本")

    # スクリプト化したPOを取り込み
    integ.vendor = MockVendorOrders(scripted_pos=[
        IncomingPO(amazon_po_number="PO-DEMO-1001", ship_to="Amazon FC 川崎",
                   lines=[IncomingPOLine(sku="SKU-CABLE-XLR-3M", asin=None,
                                         quantity=10, unit_price=1800)])
    ])
    with session_scope() as s:
        pos = order_service.ingest_new_pos(s, integ=integ)
        for po in pos:
            line = po.lines[0]
            print(f"PO {po.amazon_po_number}: 受注10 / 即納{line.qty_confirmed} "
                  f"/ 不足{line.qty_backordered} / status={po.status.value}")

    # メーカー発注の承認 → 入荷
    with session_scope() as s:
        for t in approval_service.pending(s, ApprovalType.SUPPLIER_PO):
            approval_service.decide(s, t.id, approved=True, by="購買担当")
            purchasing_service.mark_approved(s, t.ref_id)
            purchasing_service.receive_goods(s, t.ref_id)
            print(f"メーカー入荷・検品完了: {t.summary}")

    # 物流責任者の日次出荷承認 → 出荷確定
    with session_scope() as s:
        for t in approval_service.pending(s, ApprovalType.DAILY_SHIPMENT):
            approval_service.decide(s, t.id, approved=True, by="物流責任者")
            po = order_service.confirm_shipment(s, t.ref_id, integ=integ)
            print(f"出荷確定: {po.amazon_po_number} ASN={po.asn_id} Invoice={po.invoice_id} "
                  f"status={po.status.value}")


def demo_ads_flow(integ: Integrations) -> None:
    banner("フロー1: 広告運用（収集→AI分析→一括承認→反映）")
    today = date(2026, 6, 1)
    with session_scope() as s:
        recs = advertising_service.collect_and_recommend(s, report_date=today, integ=integ)
        print(f"AI提案 {len(recs)}件:")
        for r in recs:
            acos = f"{r.measured_acos:.0%}" if r.measured_acos is not None else "—"
            bid = f"¥{r.recommended_bid}" if r.recommended_bid is not None else "停止"
            print(f"  - {r.action:8s} ACoS={acos:>5s} -> {bid} : {r.rationale}")

    with session_scope() as s:
        n = advertising_service.approve_all(s, accept=True)
        print(f"マーケ責任者が一括承認: {n}件")
    with session_scope() as s:
        applied = advertising_service.apply_recommendations(s, report_date=today, integ=integ)
        print(f"Ads APIへ反映: {applied}件 + Attribution計測")


def main() -> None:
    init_db()
    integ = build_integrations()
    demo_catalog_flow(integ)
    demo_order_flow(integ)
    demo_ads_flow(integ)
    banner("完了: 3フローすべてモックで通し実行できました")


if __name__ == "__main__":
    main()

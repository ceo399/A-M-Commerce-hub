"""????????????????????????

??: python -m scripts.run_demo
??????Amazon?????????3??????????????????
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
    banner("???3: ?????????????")
    rows = [
        {"sku": "SKU-CABLE-XLR-3M", "jan": "4500000000017", "name": "XLR??????? 3m",
         "manufacturer": "Cordial", "brand": "Cordial", "in_stock": True,
         "spec": {"features": ["?????????", "??????", "3m"]}},
        {"sku": "SKU-OLD-DI-BOX", "jan": "4500000000031", "name": "??DI????",
         "manufacturer": "Cordial", "brand": "Cordial", "in_stock": False,
         "spec": {}},
    ]
    with session_scope() as s:
        # ???????????????????
        integ.catalog._registry["SKU-OLD-DI-BOX"] = CatalogStatus(
            asin="B0OLDDIBOX", is_registered=True, is_active=True)
        job = catalog_service.sync_catalog(s, "cordial_2026q2.csv", rows, integ=integ)
        print(f"??: {job.total_rows}? / ??? {job.new_products} / ?? {job.discontinued}")

    # MD?? ? ??
    with session_scope() as s:
        tasks = approval_service.pending(s, ApprovalType.LISTING)
        for t in tasks:
            approval_service.decide(s, t.id, approved=True, by="MD")
            draft = s.get(ListingDraft, t.ref_id)
            draft.status = ListingDraftStatus.APPROVED
            print(f"??: {t.summary}")
    with session_scope() as s:
        drafts = [d for d in s.query(ListingDraft).all()
                  if d.status == ListingDraftStatus.APPROVED]
        for d in drafts:
            published = catalog_service.publish_draft(s, d.id, images=["main.jpg"], integ=integ)
            print(f"????: draft#{d.id} -> ASIN {published.published_asin}")


def demo_order_flow(integ: Integrations) -> None:
    banner("???2: ??????????????????????")
    # ??????: XLR?????5??????10??? ? 5??/5???
    with session_scope() as s:
        product = s.query(Product).filter_by(sku="SKU-CABLE-XLR-3M").first()
        inventory_service.receive_stock(s, product.id, 5, ref_type="seed")
        print(f"????: {product.sku} = 5?")

    # ????????PO?????
    integ.vendor = MockVendorOrders(scripted_pos=[
        IncomingPO(amazon_po_number="PO-DEMO-1001", ship_to="Amazon FC ??",
                   lines=[IncomingPOLine(sku="SKU-CABLE-XLR-3M", asin=None,
                                         quantity=10, unit_price=1800)])
    ])
    with session_scope() as s:
        pos = order_service.ingest_new_pos(s, integ=integ)
        for po in pos:
            line = po.lines[0]
            print(f"PO {po.amazon_po_number}: ??10 / ??{line.qty_confirmed} "
                  f"/ ??{line.qty_backordered} / status={po.status.value}")

    # ????????? ? ??
    with session_scope() as s:
        for t in approval_service.pending(s, ApprovalType.SUPPLIER_PO):
            approval_service.decide(s, t.id, approved=True, by="????")
            purchasing_service.mark_approved(s, t.ref_id)
            purchasing_service.receive_goods(s, t.ref_id)
            print(f"???????????: {t.summary}")

    # ???????????? ? ????
    with session_scope() as s:
        for t in approval_service.pending(s, ApprovalType.DAILY_SHIPMENT):
            approval_service.decide(s, t.id, approved=True, by="?????")
            po = order_service.confirm_shipment(s, t.ref_id, integ=integ)
            print(f"????: {po.amazon_po_number} ASN={po.asn_id} Invoice={po.invoice_id} "
                  f"status={po.status.value}")


def demo_ads_flow(integ: Integrations) -> None:
    banner("???1: ????????AI???????????")
    today = date(2026, 6, 1)
    with session_scope() as s:
        recs = advertising_service.collect_and_recommend(s, report_date=today, integ=integ)
        print(f"AI?? {len(recs)}?:")
        for r in recs:
            acos = f"{r.measured_acos:.0%}" if r.measured_acos is not None else "?"
            bid = f"?{r.recommended_bid}" if r.recommended_bid is not None else "??"
            print(f"  - {r.action:8s} ACoS={acos:>5s} -> {bid} : {r.rationale}")

    with session_scope() as s:
        n = advertising_service.approve_all(s, accept=True)
        print(f"???????????: {n}?")
    with session_scope() as s:
        applied = advertising_service.apply_recommendations(s, report_date=today, integ=integ)
        print(f"Ads API???: {applied}? + Attribution??")


def main() -> None:
    init_db()
    integ = build_integrations()
    demo_catalog_flow(integ)
    demo_order_flow(integ)
    demo_ads_flow(integ)
    banner("??: 3???????????????????")


if __name__ == "__main__":
    main()

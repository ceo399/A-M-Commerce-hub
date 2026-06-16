"""SP-API??????? Vendor Orders ???????????httpx MockTransport?????


1) ????? ? IncomingPO ???????
2) ??????????????
3) ????????????PurchaseOrder????????????

"""
from __future__ import annotations

import warnings

warnings.filterwarnings("ignore")

import httpx
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.config import Settings
from app.db.base import Base
from app.db.models import OrderLine, Product, PurchaseOrder
from app.integrations import mock_adapters as mock
from app.integrations.live_adapters import (
    SpApiFbaInventory,
    SpApiSellerOrders,
    SpApiVendorOrders,
)
from app.integrations.registry import Integrations
from app.services import inventory_service, order_service

SANDBOX_HOST = "sandbox.sellingpartnerapi-fe.amazon.com"

CANNED_ORDERS = {
    "payload": {
        "orders": [{
            "purchaseOrderNumber": "TEST-PO-001",
            "purchaseOrderState": "New",
            "orderDetails": {
                "shipToParty": {"partyId": "WAREHOUSE-A"},
                "items": [{
                    "itemSequenceNumber": "1",
                    "amazonProductIdentifier": "B0SANDBOX1",
                    "vendorProductIdentifier": "SKU-SANDBOX-1",
                    "orderedQuantity": {"amount": 4, "unitOfMeasure": "Eaches"},
                    "netCost": {"amount": "1200.00", "currencyCode": "JPY"},
                }],
            },
        }]
    }
}


def _handler(seen):
    def handler(req: httpx.Request) -> httpx.Response:
        path = req.url.path
        if path == "/auth/o2/token":
            return httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})
        if req.method == "GET" and path == "/vendor/orders/v1/purchaseOrders":
            seen["host"] = req.url.host
            return httpx.Response(200, json=CANNED_ORDERS)
        if req.method == "POST" and path.startswith("/vendor/"):
            return httpx.Response(200, json={"payload": {"transactionId": "txn-1"}})
        return httpx.Response(404, text=f"unexpected {req.method} {req.url}")
    return handler


def _sandbox_settings():
    return Settings(integration_mode="sandbox", sp_api_region="fe",
                    sp_api_client_id="cid", sp_api_client_secret="sec",
                    sp_api_refresh_token="rt")


def test_sandbox_fetch_new_pos_maps_payload():
    seen = {}
    transport = httpx.MockTransport(_handler(seen))
    s = _sandbox_settings()
    assert s.sp_api_sandbox is True and s.use_mock() is False
    vendor = SpApiVendorOrders(s, transport=transport)
    pos = vendor.fetch_new_pos()
    assert seen["host"] == SANDBOX_HOST           # ?????????????
    assert len(pos) == 1
    po = pos[0]
    assert po.amazon_po_number == "TEST-PO-001" and po.ship_to == "WAREHOUSE-A"
    assert len(po.lines) == 1
    line = po.lines[0]
    assert (line.sku, line.asin, line.quantity, line.unit_price) == \
        ("SKU-SANDBOX-1", "B0SANDBOX1", 4, 1200.0)


def test_sandbox_ingest_into_order_pipeline():
    transport = httpx.MockTransport(_handler({}))
    s = _sandbox_settings()
    vendor = SpApiVendorOrders(s, transport=transport)
    integ = Integrations(
        vendor=vendor, catalog=mock.MockCatalog(), listings=mock.MockListings(),
        ads=mock.MockAds(), attribution=mock.MockAttribution(),
        shipping_hub=mock.MockShippingHub(), ai=mock.MockAI(), otp=mock.MockOtpDelivery(),
    )

    engine = create_engine("sqlite:///:memory:", future=True)
    import app.db.models  # noqa
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, future=True)()

    # ???????PO?SKU???????????????10??
    p = Product(sku="SKU-SANDBOX-1", name="????????")
    session.add(p); session.flush()
    inventory_service.receive_stock(session, p.id, 10)

    created = order_service.ingest_new_pos(session, integ=integ)
    assert len(created) == 1
    po = session.scalar(select(PurchaseOrder).where(PurchaseOrder.amazon_po_number == "TEST-PO-001"))
    assert po is not None
    line = session.scalar(select(OrderLine).where(OrderLine.order_id == po.id))
    assert line is not None and line.qty_ordered == 4
    # ??10???4????? ? ????
    assert line.qty_confirmed == 4 and line.qty_backordered == 0
    bal = inventory_service.get_balance(session, p.id)
    assert bal.allocated == 4 and bal.available == 6



# ---------------------------------------------------------------------
# Seller(3P) / FBA Inventory
# ---------------------------------------------------------------------
SELLER_ORDERS = {"payload": {"Orders": [
    {"AmazonOrderId": "3P-0001", "PurchaseDate": "2026-06-01T00:00:00Z",
     "OrderStatus": "Unshipped", "FulfillmentChannel": "AFN"}]}}
SELLER_ITEMS = {"payload": {"OrderItems": [
    {"SellerSKU": "SKU-3P-1", "ASIN": "B03P0001", "QuantityOrdered": 2,
     "ItemPrice": {"Amount": "1980.00", "CurrencyCode": "JPY"}}]}}
FBA_SUMMARIES = {"payload": {"inventorySummaries": [
    {"sellerSku": "SKU-3P-1", "asin": "B03P0001", "fnSku": "X00001",
     "inventoryDetails": {"fulfillableQuantity": 12,
        "inboundWorkingQuantity": 2, "inboundShippedQuantity": 1, "inboundReceivingQuantity": 1,
        "reservedQuantity": {"totalReservedQuantity": 3},
        "unfulfillableQuantity": {"totalUnfulfillableQuantity": 0}}}]}}


def _seller_handler(seen):
    def handler(req: httpx.Request) -> httpx.Response:
        path = req.url.path
        if path == "/auth/o2/token":
            return httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})
        if path == "/orders/v0/orders":
            seen["host"] = req.url.host
            return httpx.Response(200, json=SELLER_ORDERS)
        if path.startswith("/orders/v0/orders/") and path.endswith("/orderItems"):
            return httpx.Response(200, json=SELLER_ITEMS)
        if path == "/fba/inventory/v1/summaries":
            seen["host"] = req.url.host
            return httpx.Response(200, json=FBA_SUMMARIES)
        return httpx.Response(404, text=f"unexpected {req.method} {req.url}")
    return handler


def _seller_settings():
    return Settings(integration_mode="sandbox", sp_api_region="fe",
                    sp_api_client_id="cid", sp_api_client_secret="sec",
                    sp_api_seller_refresh_token="srt")


def test_sandbox_seller_orders_maps_payload():
    seen = {}
    transport = httpx.MockTransport(_seller_handler(seen))
    orders = SpApiSellerOrders(_seller_settings(), transport=transport).fetch_orders()
    assert seen["host"] == SANDBOX_HOST
    assert len(orders) == 1
    o = orders[0]
    assert o.amazon_order_id == "3P-0001" and o.fulfillment_channel == "AFN"
    assert len(o.lines) == 1
    ln = o.lines[0]
    assert (ln.sku, ln.asin, ln.quantity, ln.item_price) == ("SKU-3P-1", "B03P0001", 2, 1980.0)


def test_sandbox_fba_inventory_maps_payload():
    seen = {}
    transport = httpx.MockTransport(_seller_handler(seen))
    rows = SpApiFbaInventory(_seller_settings(), transport=transport).fetch_inventory()
    assert seen["host"] == SANDBOX_HOST
    assert len(rows) == 1
    r = rows[0]
    assert r.seller_sku == "SKU-3P-1" and r.asin == "B03P0001"
    assert (r.fulfillable, r.inbound, r.reserved, r.unfulfillable) == (12, 4, 3, 0)


def test_fba_snapshots_ingested_into_table():
    from app.db.models import InventorySnapshot
    from app.services import seller_service
    transport = httpx.MockTransport(_seller_handler({}))
    fba = SpApiFbaInventory(_seller_settings(), transport=transport)
    integ = Integrations(
        vendor=mock.MockVendorOrders(), catalog=mock.MockCatalog(), listings=mock.MockListings(),
        ads=mock.MockAds(), attribution=mock.MockAttribution(), shipping_hub=mock.MockShippingHub(),
        ai=mock.MockAI(), otp=mock.MockOtpDelivery(), fba_inventory=fba,
    )
    engine = create_engine("sqlite:///:memory:", future=True)
    import app.db.models  # noqa
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine, future=True)()
    p = Product(sku="SKU-3P-1", name="3P?")
    s.add(p); s.flush()
    n = seller_service.ingest_fba_snapshots(s, integ=integ)
    assert n == 4
    snaps = s.scalars(select(InventorySnapshot).where(InventorySnapshot.product_id == p.id)).all()
    by = {(sn.state.value if hasattr(sn.state, "value") else sn.state): sn.quantity for sn in snaps}
    assert by["fba_fulfillable"] == 12 and by["fba_inbound"] == 4 and by["fba_reserved"] == 3



# ---------------------------------------------------------------------
# 3P?????? / FBA reconcile????????
# ---------------------------------------------------------------------
def _mem_session():
    import app.db.models  # noqa
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, future=True)()


def _integ(**over):
    base = dict(
        vendor=mock.MockVendorOrders(), catalog=mock.MockCatalog(), listings=mock.MockListings(),
        ads=mock.MockAds(), attribution=mock.MockAttribution(), shipping_hub=mock.MockShippingHub(),
        ai=mock.MockAI(), otp=mock.MockOtpDelivery(),
    )
    base.update(over)
    return Integrations(**base)


def test_ingest_seller_orders_idempotent():
    from app.db.models import SellerOrder, SellerOrderLine
    from app.services import seller_service
    s = _mem_session()
    p = Product(sku="SKU-DEMO-001", name="3P?")
    s.add(p); s.flush()
    integ = _integ(seller=mock.MockSellerOrders())

    n1 = seller_service.ingest_seller_orders(s, integ=integ)
    assert n1 == 1
    so = s.scalars(select(SellerOrder)).one()
    assert so.amazon_order_id == "MOCK-3P-0001" and so.fulfillment_channel == "AFN"
    line = s.scalars(select(SellerOrderLine)).one()
    assert line.quantity == 2 and line.product_id == p.id  # sku?????

    # ???????amazon_order_id????????
    n2 = seller_service.ingest_seller_orders(s, integ=integ)
    assert n2 == 0
    assert len(s.scalars(select(SellerOrder)).all()) == 1


def test_reconcile_fba_converges_ledger_to_snapshot():
    from app.services import inventory_service, seller_service
    s = _mem_session()
    p = Product(sku="SKU-DEMO-001", name="3P?")
    s.add(p); s.flush()
    integ = _integ(fba_inventory=mock.MockFbaInventory())

    # FBA??????????fulfillable=12, inbound=4, reserved=1, unfulfillable=0?
    seller_service.ingest_fba_snapshots(s, integ=integ)
    # ?????? ? reconcile??????movement???
    corrections = inventory_service.reconcile_fba(s)
    # delta=0 ? unfulfillable ???3??????????
    assert {c["state"] for c in corrections} == {"fba_fulfillable", "fba_inbound", "fba_reserved"}

    bal = inventory_service.get_balance(s, p.id)
    assert bal.by_state.get("fba_fulfillable") == 12
    assert bal.by_state.get("fba_inbound") == 4
    assert bal.by_state.get("fba_reserved") == 1

    # 2???????????
    assert inventory_service.reconcile_fba(s) == []


# ---------------------------------------------------------------------
# #1 3P?? ? ???????FBA/MFN???
# ---------------------------------------------------------------------
def _seed_product_with_stock(s, qty):
    from app.services import inventory_service
    p = Product(sku="SKU-DEMO-001", name="3P?")
    s.add(p); s.flush()
    if qty:
        inventory_service.receive_stock(s, p.id, qty)
    return p


def test_seller_order_mfn_allocates_ledger():
    from app.integrations.ports import IncomingSellerOrder, IncomingSellerOrderLine
    from app.services import inventory_service, seller_service
    s = _mem_session()
    p = _seed_product_with_stock(s, 10)
    mfn = IncomingSellerOrder(amazon_order_id="MFN-1", purchase_date=None, order_status="Unshipped",
            fulfillment_channel="MFN",
            lines=[IncomingSellerOrderLine(sku="SKU-DEMO-001", asin="B0DEMO0001", quantity=2, item_price=1000.0)])
    seller_service.ingest_seller_orders(s, integ=_integ(seller=mock.MockSellerOrders(scripted=[mfn])))
    bal = inventory_service.get_balance(s, p.id)
    assert bal.allocated == 2 and bal.available == 8  # ????=??


def test_seller_order_afn_does_not_touch_own_stock():
    from app.services import inventory_service, seller_service
    s = _mem_session()
    p = _seed_product_with_stock(s, 10)
    # ??MockSellerOrders?AFN(FBA)
    seller_service.ingest_seller_orders(s, integ=_integ(seller=mock.MockSellerOrders()))
    bal = inventory_service.get_balance(s, p.id)
    assert bal.available == 10 and bal.allocated == 0  # FBA???????????


def test_seller_order_mfn_shipped_ships_out():
    from app.integrations.ports import IncomingSellerOrder, IncomingSellerOrderLine
    from app.services import inventory_service, seller_service
    s = _mem_session()
    p = _seed_product_with_stock(s, 10)
    shipped = IncomingSellerOrder(amazon_order_id="MFN-2", purchase_date=None, order_status="Shipped",
            fulfillment_channel="MFN",
            lines=[IncomingSellerOrderLine(sku="SKU-DEMO-001", asin="B0DEMO0001", quantity=2, item_price=1000.0)])
    seller_service.ingest_seller_orders(s, integ=_integ(seller=mock.MockSellerOrders(scripted=[shipped])))
    bal = inventory_service.get_balance(s, p.id)
    assert bal.allocated == 0 and bal.available == 8  # ????????????


# ---------------------------------------------------------------------
# #3 ?????????????PDF?? / Excel???
# ---------------------------------------------------------------------
def test_ingest_order_document_commits_high_confidence():
    from app.db.models import SellerOrder
    from app.services import doc_service, inventory_service
    s = _mem_session()
    p = _seed_product_with_stock(s, 10)
    res = doc_service.ingest_order_document(s, "mail-1", integ=_integ())  # ??MockOrderDoc=??0.95/MFN
    assert res["committed"] is True
    assert s.scalars(select(SellerOrder)).one().amazon_order_id == "DOC-mail-1"
    bal = inventory_service.get_balance(s, p.id)
    assert bal.allocated == 3  # MFN???


def test_ingest_order_document_holds_low_confidence():
    from app.db.models import SellerOrder
    from app.integrations.ports import ParsedOrderDoc, ParsedOrderDocLine
    from app.services import doc_service
    s = _mem_session()
    _seed_product_with_stock(s, 10)
    low = ParsedOrderDoc(source="email_pdf", external_ref="mail-x", confidence=0.4,
                         fulfillment_channel="MFN",
                         lines=[ParsedOrderDocLine(sku="SKU-DEMO-001", asin=None, quantity=1, unit_price=100.0)])
    res = doc_service.ingest_order_document(s, "mail-x", integ=_integ(order_doc=mock.MockOrderDoc(scripted=low)))
    assert res["committed"] is False and res["reason"] == "low_confidence"
    assert s.scalars(select(SellerOrder)).all() == []  # ????????????


def test_ingest_inventory_count_adjusts_own_warehouse():
    from app.services import doc_service, inventory_service
    s = _mem_session()
    p = Product(sku="SKU-DEMO-001", name="3P?"); s.add(p); s.flush()  # ??0
    res = doc_service.ingest_inventory_count(s, "book.xlsx", integ=_integ())  # ??MockInventoryDoc=50
    assert res["updated"] == 1
    assert inventory_service.get_balance(s, p.id).available == 50


def test_excel_inventory_doc_parses_real_xlsx(tmp_path):
    import openpyxl
    from app.integrations.live_adapters import ExcelInventoryDoc
    wb = openpyxl.Workbook(); ws = wb.active
    ws.append(["SKU", "ASIN", "??"])
    ws.append(["SKU-A", "B0A", 12])
    ws.append(["SKU-B", None, 7])
    ws.append([None, None, None])  # ???????
    fp = tmp_path / "inv.xlsx"; wb.save(fp)
    rows = ExcelInventoryDoc().parse(str(fp))
    assert len(rows) == 2
    assert (rows[0].sku, rows[0].asin, rows[0].quantity) == ("SKU-A", "B0A", 12)
    assert (rows[1].sku, rows[1].quantity) == ("SKU-B", 7)

if __name__ == "__main__":
    test_sandbox_fetch_new_pos_maps_payload()
    test_sandbox_ingest_into_order_pipeline()
    test_sandbox_seller_orders_maps_payload()
    test_sandbox_fba_inventory_maps_payload()
    test_fba_snapshots_ingested_into_table()
    test_ingest_seller_orders_idempotent()
    test_reconcile_fba_converges_ledger_to_snapshot()
    print("OK: SP-API??????????????")

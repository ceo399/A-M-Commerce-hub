"""????????

Amazon????????????????????????????????????
API??????????????????????? live ????????
"""
from __future__ import annotations

import random
import uuid
from datetime import date

from app.integrations.ports import (
    AdMetricRow,
    AdsPort,
    AIPort,
    AttributionPort,
    CatalogPort,
    CatalogStatus,
    FbaInventoryPort,
    FbaInventoryRow,
    IncomingPO,
    IncomingPOLine,
    IncomingSellerOrder,
    IncomingSellerOrderLine,
    InventoryCountRow,
    InventoryDocPort,
    ListingsPort,
    OrderDocPort,
    OtpDeliveryPort,
    ParsedOrderDoc,
    ParsedOrderDocLine,
    SellerOrdersPort,
    ShippingHubPort,
    VendorOrdersPort,
)


class MockVendorOrders(VendorOrdersPort):
    def __init__(self, scripted_pos: list[IncomingPO] | None = None):
        # ??/???????????????PO????????????
        self._scripted = scripted_pos

    def fetch_new_pos(self) -> list[IncomingPO]:
        if self._scripted is not None:
            out, self._scripted = self._scripted, []
            return out
        # ??: ???????PO?1???
        return [
            IncomingPO(
                amazon_po_number=f"PO-{uuid.uuid4().hex[:8].upper()}",
                ship_to="Amazon FC (Mock)",
                lines=[IncomingPOLine(sku="SKU-DEMO-001", asin=None, quantity=10, unit_price=1500)],
            )
        ]

    def acknowledge_po(self, amazon_po_number: str, confirmed: dict[str, int]) -> None:
        print(f"[MOCK Vendor] PO Ack: {amazon_po_number} -> {confirmed}")

    def send_asn(self, amazon_po_number: str, lines: dict[str, int]) -> str:
        asn = f"ASN-{uuid.uuid4().hex[:10].upper()}"
        print(f"[MOCK Vendor] ASN??: {amazon_po_number} {lines} -> {asn}")
        return asn

    def send_invoice(self, amazon_po_number: str, amount: float) -> str:
        inv = f"INV-{uuid.uuid4().hex[:10].upper()}"
        print(f"[MOCK Vendor] Invoice??: {amazon_po_number} ?{amount:,.0f} -> {inv}")
        return inv


class MockCatalog(CatalogPort):
    def __init__(self, registry: dict[str, CatalogStatus] | None = None):
        self._registry = registry or {}

    def check_status(self, sku: str, jan: str | None, asin: str | None) -> CatalogStatus:
        if sku in self._registry:
            return self._registry[sku]
        # ??: ?????????
        return CatalogStatus(asin=None, is_registered=False, is_active=False)


class MockListings(ListingsPort):
    def publish_listing(self, sku, title, bullets, description, images) -> str:
        asin = "B0" + uuid.uuid4().hex[:8].upper()
        print(f"[MOCK Listings] ????: {sku} -> {asin} / title='{title[:30]}...'")
        return asin

    def set_discontinued(self, asin: str) -> None:
        print(f"[MOCK Listings] ???????: {asin}")


class MockAds(AdsPort):
    def fetch_daily_report(self, report_date: date) -> list[AdMetricRow]:
        rng = random.Random(report_date.toordinal())
        rows = []
        for i in range(1, 4):
            clicks = rng.randint(20, 200)
            spend = round(clicks * rng.uniform(30, 80), 2)
            orders = rng.randint(0, max(1, clicks // 15))
            sales = round(orders * rng.uniform(1500, 6000), 2)
            rows.append(AdMetricRow(
                amazon_entity_id=f"KW-{i:03d}",
                entity_type="keyword",
                name=f"?????{i}",
                report_date=report_date,
                impressions=clicks * rng.randint(8, 30),
                clicks=clicks, spend=spend, sales=sales, orders=orders,
                current_bid=round(rng.uniform(30, 90), 2),
            ))
        return rows

    def update_bid(self, amazon_entity_id: str, new_bid: float | None) -> None:
        action = "??" if new_bid is None else f"?{new_bid}"
        print(f"[MOCK Ads] ????: {amazon_entity_id} -> {action}")

    def update_budget(self, amazon_entity_id: str, daily_budget: float) -> None:
        print(f"[MOCK Ads] ????: {amazon_entity_id} -> ?{daily_budget:,.0f}")


class MockAttribution(AttributionPort):
    def fetch_attribution(self, report_date: date) -> dict:
        return {"date": report_date.isoformat(), "external_clicks": 0, "external_sales": 0.0}


class MockShippingHub(ShippingHubPort):
    def send_shipment_csv(self, amazon_po_number: str, rows: list[dict]) -> str:
        path = f"/mock-sftp/outbound/{amazon_po_number}.csv"
        print(f"[MOCK AIS??] ??CSV??: {path} ({len(rows)}?)")
        return path


class MockAI(AIPort):
    """???????????ANTHROPIC_API_KEY ???? live ???????"""

    def generate_listing_copy(self, product_spec: dict) -> dict:
        name = product_spec.get("name", "??")
        brand = product_spec.get("brand", "")
        features = product_spec.get("features", [])
        title = f"{brand} {name}".strip()[:200]
        bullets = [f"??: {f}" for f in features[:5]] or [f"{name}???????"]
        desc = f"{title}?{' '.join(bullets)}"
        return {"title": title, "bullet_points": bullets, "description": desc}

    def analyze_ads(self, metrics: list[dict], target_acos: float) -> list[dict]:
        recs = []
        for m in metrics:
            spend, sales = float(m["spend"]), float(m["sales"])
            acos = (spend / sales) if sales else None
            bid = m.get("current_bid")
            if acos is None and spend > 0:
                action, new_bid, why = "pause", None, "????????????????"
            elif acos is None:
                action, new_bid, why = "keep", bid, "??????????"
            elif acos > target_acos * 1.2:
                action, new_bid = "decrease", round((bid or 0) * 0.85, 2)
                why = f"ACoS {acos:.0%} ??? {target_acos:.0%} ??????????15%??"
            elif acos < target_acos * 0.7:
                action, new_bid = "increase", round((bid or 0) * 1.15, 2)
                why = f"ACoS {acos:.0%} ??????????????15%?????"
            else:
                action, new_bid, why = "keep", bid, f"ACoS {acos:.0%} ?????????"
            recs.append({
                "amazon_entity_id": m["amazon_entity_id"],
                "action": action, "recommended_bid": new_bid,
                "rationale": why, "measured_acos": acos,
            })
        return recs


class MockOtpDelivery(OtpDeliveryPort):
    """OTP??????????????????

    ???? Twilio(SMS) ? Amazon SES/SMTP(???) ????? live ????????
    ????????????????????
    """
    def __init__(self):
        self.last_code: str | None = None
        self.last_destination: str | None = None

    def send_otp(self, *, method: str, destination: str, code: str) -> None:
        self.last_code = code
        self.last_destination = destination
        print(f"[MOCK OTP] {method} -> {destination}: ????? {code}")


class MockSellerOrders(SellerOrdersPort):
    """3P?????????/?????????????"""
    def __init__(self, scripted: list[IncomingSellerOrder] | None = None):
        self._scripted = scripted

    def fetch_orders(self, *, created_after: str | None = None) -> list[IncomingSellerOrder]:
        if self._scripted is not None:
            return self._scripted
        return [IncomingSellerOrder(
            amazon_order_id="MOCK-3P-0001",
            purchase_date="2026-06-01T00:00:00Z",
            order_status="Unshipped",
            fulfillment_channel="AFN",
            lines=[IncomingSellerOrderLine(sku="SKU-DEMO-001", asin="B0DEMO0001",
                                           quantity=2, item_price=1980.0)],
        )]


class MockFbaInventory(FbaInventoryPort):
    """FBA?????????"""
    def fetch_inventory(self) -> list[FbaInventoryRow]:
        return [FbaInventoryRow(seller_sku="SKU-DEMO-001", asin="B0DEMO0001",
                                fnsku="X00DEMO001", fulfillable=12, inbound=4,
                                reserved=1, unfulfillable=0)]


class MockOrderDoc(OrderDocPort):
    """???PDF?????????????????1????scripted????????"""
    def __init__(self, scripted: ParsedOrderDoc | None = None):
        self._scripted = scripted

    def parse(self, source: str) -> ParsedOrderDoc:
        if self._scripted is not None:
            return self._scripted
        return ParsedOrderDoc(
            source="email_pdf", external_ref=source or "mock-mail-001",
            confidence=0.95, fulfillment_channel="MFN",
            lines=[ParsedOrderDocLine(sku="SKU-DEMO-001", asin="B0DEMO0001",
                                      quantity=3, unit_price=1200.0)],
        )


class MockInventoryDoc(InventoryDocPort):
    """Excel??????????????????"""
    def __init__(self, scripted: list[InventoryCountRow] | None = None):
        self._scripted = scripted

    def parse(self, source: str) -> list[InventoryCountRow]:
        if self._scripted is not None:
            return self._scripted
        return [InventoryCountRow(sku="SKU-DEMO-001", asin="B0DEMO0001", quantity=50)]

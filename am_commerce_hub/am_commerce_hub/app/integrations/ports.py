"""?????????????????????

???????????????????????? mock_adapters ?????
??? Amazon ??????????? live_adapters ???????????
????/????????????????????????????????????
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date


# ---- DTO????????Amazon?????????????????????? ----
@dataclass
class IncomingPOLine:
    sku: str
    asin: str | None
    quantity: int
    unit_price: float | None


@dataclass
class IncomingPO:
    amazon_po_number: str
    ship_to: str | None
    lines: list[IncomingPOLine] = field(default_factory=list)


# ---- 3P?Seller?DTO ----
@dataclass
class IncomingSellerOrderLine:
    sku: str
    asin: str | None
    quantity: int
    item_price: float | None


@dataclass
class IncomingSellerOrder:
    amazon_order_id: str
    purchase_date: str | None
    order_status: str | None
    fulfillment_channel: str | None   # AFN(FBA) / MFN(????)
    lines: list[IncomingSellerOrderLine] = field(default_factory=list)


@dataclass
class FbaInventoryRow:
    """FBA??????getInventorySummaries ????????inventory_state????"""
    seller_sku: str
    asin: str | None
    fnsku: str | None
    fulfillable: int
    inbound: int
    reserved: int
    unfulfillable: int


# ---- ?????????????PDF?? / Excel???DTO ----
@dataclass
class ParsedOrderDocLine:
    sku: str
    asin: str | None
    quantity: int
    unit_price: float | None


@dataclass
class ParsedOrderDoc:
    source: str                 # ?: email_pdf
    external_ref: str           # ???ID/???????
    confidence: float           # 0.0-1.0 ????????????????????
    fulfillment_channel: str    # ?? MFN??????
    lines: list[ParsedOrderDocLine] = field(default_factory=list)


@dataclass
class InventoryCountRow:
    sku: str
    asin: str | None
    quantity: int


@dataclass
class CatalogStatus:
    asin: str | None
    is_registered: bool
    is_active: bool   # Amazon???????


@dataclass
class AdMetricRow:
    amazon_entity_id: str
    entity_type: str
    name: str
    report_date: date
    impressions: int
    clicks: int
    spend: float
    sales: float
    orders: int
    current_bid: float | None


# =========================================================================
# ?????
# =========================================================================
class VendorOrdersPort(ABC):
    """???2: Amazon ????????? / Vendor Orders API?"""

    @abstractmethod
    def fetch_new_pos(self) -> list[IncomingPO]: ...

    @abstractmethod
    def acknowledge_po(self, amazon_po_number: str, confirmed: dict[str, int]) -> None:
        """PO Acknowledgement???????????"""

    @abstractmethod
    def send_asn(self, amazon_po_number: str, lines: dict[str, int]) -> str:
        """???????ASN-ID????"""

    @abstractmethod
    def send_invoice(self, amazon_po_number: str, amount: float) -> str:
        """??????Invoice-ID????"""


class SellerOrdersPort(ABC):
    """3P: Seller Central / Orders API?getOrders + getOrderItems??"""

    @abstractmethod
    def fetch_orders(self, *, created_after: str | None = None) -> list["IncomingSellerOrder"]: ...


class FbaInventoryPort(ABC):
    """3P: FBA Inventory API?getInventorySummaries??????????"""

    @abstractmethod
    def fetch_inventory(self) -> list["FbaInventoryRow"]: ...


class OrderDocPort(ABC):
    """???/PDF?????????????????????????????????"""

    @abstractmethod
    def parse(self, source: str) -> "ParsedOrderDoc": ...


class InventoryDocPort(ABC):
    """Excel??????????????????????"""

    @abstractmethod
    def parse(self, source: str) -> list["InventoryCountRow"]: ...


class CatalogPort(ABC):
    """???3: SP-API Catalog Items?"""

    @abstractmethod
    def check_status(self, sku: str, jan: str | None, asin: str | None) -> CatalogStatus: ...


class ListingsPort(ABC):
    """???3: SP-API Listings Items?"""

    @abstractmethod
    def publish_listing(self, sku: str, title: str, bullets: list[str],
                        description: str, images: list[str]) -> str:
        """????????????ASIN????"""

    @abstractmethod
    def set_discontinued(self, asin: str) -> None:
        """?????????????"""


class AdsPort(ABC):
    """???1: Amazon Ads API?"""

    @abstractmethod
    def fetch_daily_report(self, report_date: date) -> list[AdMetricRow]: ...

    @abstractmethod
    def update_bid(self, amazon_entity_id: str, new_bid: float | None) -> None:
        """?????new_bid=None ??????"""

    @abstractmethod
    def update_budget(self, amazon_entity_id: str, daily_budget: float) -> None: ...


class AttributionPort(ABC):
    """???1: ???????? Attribution ???"""

    @abstractmethod
    def fetch_attribution(self, report_date: date) -> dict: ...


class ShippingHubPort(ABC):
    """???2: AIS??????CSV???SFTP???"""

    @abstractmethod
    def send_shipment_csv(self, amazon_po_number: str, rows: list[dict]) -> str:
        """?????????/??????"""


class AIPort(ABC):
    """AI?Claude?????????????"""

    @abstractmethod
    def generate_listing_copy(self, product_spec: dict) -> dict:
        """{title, bullet_points, description} ????"""

    @abstractmethod
    def analyze_ads(self, metrics: list[dict], target_acos: float) -> list[dict]:
        """?????????????????"""


class OtpDeliveryPort(ABC):
    """????????????SMS / ?????"""

    @abstractmethod
    def send_otp(self, *, method: str, destination: str, code: str) -> None:
        """method='sms'|'email'?destination=????/????????"""

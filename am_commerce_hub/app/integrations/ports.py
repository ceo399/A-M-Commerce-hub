"""外部連携のポート（抽象インターフェース）。

ここに定義されたインターフェースに対して、現状は mock_adapters の実装が、
将来は Amazon の本番資格情報を使った live_adapters の実装が差し込まれる。
ドメイン/サービス層はこのインターフェースだけに依存し、本物かモックかを知らない。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date


# ---- DTO（連携の入出力。Amazonの生フォーマットを自社語彙へ正規化したもの） ----
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


# ---- 3P（Seller）DTO ----
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
    fulfillment_channel: str | None   # AFN(FBA) / MFN(自社出荷)
    lines: list[IncomingSellerOrderLine] = field(default_factory=list)


@dataclass
class FbaInventoryRow:
    """FBAの絶対在庫（getInventorySummaries 由来）。各数量はinventory_stateに対応。"""
    seller_sku: str
    asin: str | None
    fnsku: str | None
    fulfillable: int
    inbound: int
    reserved: int
    unfulfillable: int


# ---- 他チャネル取り込み（メールPDF注文 / Excel在庫）DTO ----
@dataclass
class ParsedOrderDocLine:
    sku: str
    asin: str | None
    quantity: int
    unit_price: float | None


@dataclass
class ParsedOrderDoc:
    source: str                 # 例: email_pdf
    external_ref: str           # メールID/ファイル名など
    confidence: float           # 0.0-1.0 抽出信頼度（低信頼は自動コミットしない）
    fulfillment_channel: str    # 既定 MFN（自社出荷）
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
    is_active: bool   # Amazon上で販売可能か


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
# ポート定義
# =========================================================================
class VendorOrdersPort(ABC):
    """フロー2: Amazon ベンダーセントラル / Vendor Orders API。"""

    @abstractmethod
    def fetch_new_pos(self) -> list[IncomingPO]: ...

    @abstractmethod
    def acknowledge_po(self, amazon_po_number: str, confirmed: dict[str, int]) -> None:
        """PO Acknowledgement（即納可能数の回答）。"""

    @abstractmethod
    def send_asn(self, amazon_po_number: str, lines: dict[str, int]) -> str:
        """事前出荷通知。ASN-IDを返す。"""

    @abstractmethod
    def send_invoice(self, amazon_po_number: str, amount: float) -> str:
        """請求書送信。Invoice-IDを返す。"""


class SellerOrdersPort(ABC):
    """3P: Seller Central / Orders API（getOrders + getOrderItems）。"""

    @abstractmethod
    def fetch_orders(self, *, created_after: str | None = None) -> list["IncomingSellerOrder"]: ...


class FbaInventoryPort(ABC):
    """3P: FBA Inventory API（getInventorySummaries）。絶対在庫を返す。"""

    @abstractmethod
    def fetch_inventory(self) -> list["FbaInventoryRow"]: ...


class OrderDocPort(ABC):
    """メール/PDF等の注文ドキュメントを構造化抽出する（低信頼は呼び出し側で保留）。"""

    @abstractmethod
    def parse(self, source: str) -> "ParsedOrderDoc": ...


class InventoryDocPort(ABC):
    """Excel等の在庫表を読み取り、棚卸カウント行を返す。"""

    @abstractmethod
    def parse(self, source: str) -> list["InventoryCountRow"]: ...


class CatalogPort(ABC):
    """フロー3: SP-API Catalog Items。"""

    @abstractmethod
    def check_status(self, sku: str, jan: str | None, asin: str | None) -> CatalogStatus: ...


class ListingsPort(ABC):
    """フロー3: SP-API Listings Items。"""

    @abstractmethod
    def publish_listing(self, sku: str, title: str, bullets: list[str],
                        description: str, images: list[str]) -> str:
        """出品実行。割り当てられたASINを返す。"""

    @abstractmethod
    def set_discontinued(self, asin: str) -> None:
        """出品停止・廃盤フラグ送信。"""


class AdsPort(ABC):
    """フロー1: Amazon Ads API。"""

    @abstractmethod
    def fetch_daily_report(self, report_date: date) -> list[AdMetricRow]: ...

    @abstractmethod
    def update_bid(self, amazon_entity_id: str, new_bid: float | None) -> None:
        """入札更新。new_bid=None は一時停止。"""

    @abstractmethod
    def update_budget(self, amazon_entity_id: str, daily_budget: float) -> None: ...


class AttributionPort(ABC):
    """フロー1: 外部トラフィック Attribution 計測。"""

    @abstractmethod
    def fetch_attribution(self, report_date: date) -> dict: ...


class ShippingHubPort(ABC):
    """フロー2: AISハブへの出荷CSV送信（SFTP等）。"""

    @abstractmethod
    def send_shipment_csv(self, amazon_po_number: str, rows: list[dict]) -> str:
        """送信したファイル名/パスを返す。"""


class AIPort(ABC):
    """AI（Claude）。出品文生成と広告分析。"""

    @abstractmethod
    def generate_listing_copy(self, product_spec: dict) -> dict:
        """{title, bullet_points, description} を返す。"""

    @abstractmethod
    def analyze_ads(self, metrics: list[dict], target_acos: float) -> list[dict]:
        """入札増減・停止提案のリストを返す。"""


class OtpDeliveryPort(ABC):
    """二段階認証コードの配信（SMS / メール）。"""

    @abstractmethod
    def send_otp(self, *, method: str, destination: str, code: str) -> None:
        """method='sms'|'email'、destination=電話番号/メールアドレス。"""

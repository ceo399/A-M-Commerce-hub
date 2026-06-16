"""統合レジストリ（ファクトリ）。

設定の integration_mode に応じてモック/本番アダプタを返す。
サービス層は get_integrations() を通してのみ外部に触れる。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from app.config import settings
from app.integrations import mock_adapters as mock
from app.integrations.ports import (
    AdsPort,
    AIPort,
    AttributionPort,
    CatalogPort,
    FbaInventoryPort,
    InventoryDocPort,
    ListingsPort,
    OrderDocPort,
    OtpDeliveryPort,
    SellerOrdersPort,
    ShippingHubPort,
    VendorOrdersPort,
)


@dataclass
class Integrations:
    vendor: VendorOrdersPort
    catalog: CatalogPort
    listings: ListingsPort
    ads: AdsPort
    attribution: AttributionPort
    shipping_hub: ShippingHubPort
    ai: AIPort
    otp: OtpDeliveryPort
    # 3P（Seller）系。既存の生成箇所を壊さないよう既定はmock。
    seller: SellerOrdersPort = field(default_factory=mock.MockSellerOrders)
    fba_inventory: FbaInventoryPort = field(default_factory=mock.MockFbaInventory)
    # 他チャネル取り込み（メールPDF注文 / Excel在庫）。既定はmock。
    order_doc: OrderDocPort = field(default_factory=mock.MockOrderDoc)
    inventory_doc: InventoryDocPort = field(default_factory=mock.MockInventoryDoc)


def _build_mock() -> Integrations:
    return Integrations(
        vendor=mock.MockVendorOrders(),
        catalog=mock.MockCatalog(),
        listings=mock.MockListings(),
        ads=mock.MockAds(),
        attribution=mock.MockAttribution(),
        shipping_hub=mock.MockShippingHub(),
        ai=mock.MockAI(),
        otp=mock.MockOtpDelivery(),
        seller=mock.MockSellerOrders(),
        fba_inventory=mock.MockFbaInventory(),
        order_doc=mock.MockOrderDoc(),
        inventory_doc=mock.MockInventoryDoc(),
    )


def _build_live() -> Integrations:
    """本番/サンドボックスのアダプタを組み立てる。

    各アダプタは個別に構築し、資格情報不足等で失敗したものだけ mock にフォールバックする
    （例: SP-APIサンドボックスのトークンはあるが Ads 資格情報が無い場合でも vendor は実接続）。
    """
    from app.integrations import live_adapters as live
    from app.logging_config import get_logger
    log = get_logger("amhub.integrations")
    log.warning("INTEGRATION_MODE=%s: Amazon実接続アダプタを使用します", settings.integration_mode)

    def _safe(build, fallback, name):
        try:
            return build()
        except Exception as e:  # noqa: BLE001
            log.warning("live adapter %s の構築に失敗→mockにフォールバック: %s", name, e)
            return fallback()

    return Integrations(
        vendor=_safe(lambda: live.SpApiVendorOrders(settings), mock.MockVendorOrders, "vendor"),
        catalog=_safe(lambda: live.SpApiCatalog(settings), mock.MockCatalog, "catalog"),
        listings=_safe(lambda: live.SpApiListings(settings), mock.MockListings, "listings"),
        ads=_safe(lambda: live.AmazonAds(settings), mock.MockAds, "ads"),
        attribution=_safe(lambda: live.AmazonAttribution(settings), mock.MockAttribution, "attribution"),
        shipping_hub=_safe(lambda: live.SftpShippingHub(settings), mock.MockShippingHub, "shipping_hub"),
        ai=mock.MockAI(),          # AI実接続は別途（差し替え口は用意済み）
        otp=mock.MockOtpDelivery(),  # OTP実配信は別途（Twilio/SES）
        seller=_safe(lambda: live.SpApiSellerOrders(settings), mock.MockSellerOrders, "seller"),
        fba_inventory=_safe(lambda: live.SpApiFbaInventory(settings), mock.MockFbaInventory, "fba_inventory"),
        order_doc=_safe(lambda: live.PdfOrderDoc(settings), mock.MockOrderDoc, "order_doc"),
        inventory_doc=_safe(lambda: live.ExcelInventoryDoc(), mock.MockInventoryDoc, "inventory_doc"),
    )


_cached: Integrations | None = None


def get_integrations(override: Integrations | None = None) -> Integrations:
    """既定はキャッシュを返す。テスト時は override で差し替え可能。"""
    global _cached
    if override is not None:
        return override
    if _cached is None:
        _cached = _build_mock() if settings.use_mock() else _build_live()
    return _cached

"""???????????????

 integration_mode ???????/??????????
 get_integrations() ?????????????
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
    # 3P?Seller????????????????????mock?
    seller: SellerOrdersPort = field(default_factory=mock.MockSellerOrders)
    fba_inventory: FbaInventoryPort = field(default_factory=mock.MockFbaInventory)
    # ?????????????PDF?? / Excel???????mock?
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
    """??/???????????????????

    ????????????????????????????? mock ??????????
    ??: SP-API???????????????? Ads ??????????? vendor ??????
    """
    from app.integrations import live_adapters as live
    from app.logging_config import get_logger
    log = get_logger("amhub.integrations")
    log.warning("INTEGRATION_MODE=%s: Amazon?????????????", settings.integration_mode)

    def _safe(build, fallback, name):
        try:
            return build()
        except Exception as e:  # noqa: BLE001
            log.warning("live adapter %s ???????mock????????: %s", name, e)
            return fallback()

    return Integrations(
        vendor=_safe(lambda: live.SpApiVendorOrders(settings), mock.MockVendorOrders, "vendor"),
        catalog=_safe(lambda: live.SpApiCatalog(settings), mock.MockCatalog, "catalog"),
        listings=_safe(lambda: live.SpApiListings(settings), mock.MockListings, "listings"),
        ads=_safe(lambda: live.AmazonAds(settings), mock.MockAds, "ads"),
        attribution=_safe(lambda: live.AmazonAttribution(settings), mock.MockAttribution, "attribution"),
        shipping_hub=_safe(lambda: live.SftpShippingHub(settings), mock.MockShippingHub, "shipping_hub"),
        ai=mock.MockAI(),          # AI??????????????????
        otp=mock.MockOtpDelivery(),  # OTP???????Twilio/SES?
        seller=_safe(lambda: live.SpApiSellerOrders(settings), mock.MockSellerOrders, "seller"),
        fba_inventory=_safe(lambda: live.SpApiFbaInventory(settings), mock.MockFbaInventory, "fba_inventory"),
        order_doc=_safe(lambda: live.PdfOrderDoc(settings), mock.MockOrderDoc, "order_doc"),
        inventory_doc=_safe(lambda: live.ExcelInventoryDoc(), mock.MockInventoryDoc, "inventory_doc"),
    )


_cached: Integrations | None = None


def get_integrations(override: Integrations | None = None) -> Integrations:
    """????????????????? override ????????"""
    global _cached
    if override is not None:
        return override
    if _cached is None:
        _cached = _build_mock() if settings.use_mock() else _build_live()
    return _cached

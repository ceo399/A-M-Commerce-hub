"""本番アダプタ（Amazon実接続）。

横断機能（OAuthトークン管理・リトライ/レート制御・Adsレポートのポーリング）は
app/integrations/amazon/ 配下に実装済みで、ここではそれらを組み立てて各ポートに接続する。

各オペレーションの「リクエストpath/body」と「レスポンス→DTO整形」は、
実APIの契約（資格情報取得後に確定）に合わせて埋める箇所を TODO として明示している。
プラミング（認証・再試行・レート制御・レポート枠組み）は完成・検証済みのため、
残作業は各オペレーションの個別マッピングのみ。
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone

from app.config import Settings
from app.integrations.amazon.ads_api import AdsApiClient, AdsReportClient
from app.integrations.amazon.auth import LwaTokenManager
from app.integrations.amazon.sp_api import SpApiClient
from app.integrations.ports import (
    AdMetricRow,
    AdsPort,
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
    ParsedOrderDoc,
    ParsedOrderDocLine,
    SellerOrdersPort,
    ShippingHubPort,
    VendorOrdersPort,
)

_FILL = "実APIの契約に合わせて path/body とレスポンス整形を実装してください（資格情報取得後）"


def _txn_id(resp) -> str | None:
    try:
        return (resp.json() or {}).get("payload", {}).get("transactionId")
    except Exception:
        return None


# =========================================================================
# SP-API: Vendor Orders（フロー2）
# =========================================================================
class SpApiVendorOrders(VendorOrdersPort):
    """SP-API Vendor Orders。INTEGRATION_MODE=sandbox なら静的サンドボックスに接続。"""

    def __init__(self, settings: Settings, *, transport=None, token_manager=None):
        self.sp = SpApiClient(settings, region=settings.sp_api_region,
                              transport=transport, token_manager=token_manager)

    def fetch_new_pos(self, *, created_after: str | None = None) -> list[IncomingPO]:
        # createdAfter は必須相当。未指定なら直近7日。
        if created_after is None:
            created_after = (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%SZ")
        resp = self.sp.get("/vendor/orders/v1/purchaseOrders",
                           params={"createdAfter": created_after, "limit": 50})
        payload = (resp.json() or {}).get("payload", {}) or {}
        out: list[IncomingPO] = []
        for order in payload.get("orders", []) or []:
            details = order.get("orderDetails", {}) or {}
            ship_to = (details.get("shipToParty", {}) or {}).get("partyId")
            lines: list[IncomingPOLine] = []
            for it in details.get("items", []) or []:
                qty = (it.get("orderedQuantity", {}) or {}).get("amount", 0)
                net = it.get("netCost", {}) or {}
                price = float(net["amount"]) if net.get("amount") is not None else None
                lines.append(IncomingPOLine(
                    sku=it.get("vendorProductIdentifier") or it.get("amazonProductIdentifier") or "",
                    asin=it.get("amazonProductIdentifier"),
                    quantity=int(qty or 0),
                    unit_price=price,
                ))
            out.append(IncomingPO(
                amazon_po_number=order.get("purchaseOrderNumber", ""),
                ship_to=ship_to, lines=lines,
            ))
        return out

    def acknowledge_po(self, amazon_po_number, confirmed):
        self.sp.post("/vendor/orders/v1/acknowledgements", json={
            "acknowledgements": [{
                "purchaseOrderNumber": amazon_po_number,
                "items": [{"vendorProductIdentifier": sku,
                           "acknowledgedQuantity": {"amount": qty, "unitOfMeasure": "Eaches"}}
                          for sku, qty in confirmed.items()],
            }]
        })

    def send_asn(self, amazon_po_number, lines) -> str:
        resp = self.sp.post("/vendor/shipping/v1/shipmentConfirmations", json={
            "shipmentConfirmations": [{"purchaseOrderNumber": amazon_po_number}]
        })
        return _txn_id(resp) or f"ASN-{uuid.uuid4().hex[:10]}"

    def send_invoice(self, amazon_po_number, amount) -> str:
        resp = self.sp.post("/vendor/payments/v1/invoices", json={
            "invoices": [{"invoiceNumber": amazon_po_number,
                          "invoiceTotal": {"amount": str(amount), "currencyCode": "JPY"}}]
        })
        return _txn_id(resp) or f"INV-{uuid.uuid4().hex[:10]}"


# =========================================================================
# SP-API: Catalog / Listings（フロー3）
# =========================================================================
class SpApiCatalog(CatalogPort):
    def __init__(self, settings: Settings):
        self.sp = SpApiClient(settings, region=settings.sp_api_region)

    def check_status(self, sku, jan, asin) -> CatalogStatus:
        # 例: self.sp.get("/catalog/2022-04-01/items", params={"identifiers": jan, ...})
        raise NotImplementedError(f"searchCatalogItems: {_FILL}")


class SpApiListings(ListingsPort):
    def __init__(self, settings: Settings):
        self.sp = SpApiClient(settings, region=settings.sp_api_region)

    def publish_listing(self, sku, title, bullets, description, images) -> str:
        # 例: self.sp.put(f"/listings/2021-08-01/items/{sellerId}/{sku}", json={...})
        raise NotImplementedError(f"putListingsItem: {_FILL}")

    def set_discontinued(self, asin) -> None:
        raise NotImplementedError(f"出品停止(数量0/クローズ): {_FILL}")


# =========================================================================
# Ads API（フロー1）— レポート枠組みは実装済み、spec/列名のみ調整
# =========================================================================
class AmazonAds(AdsPort):
    def __init__(self, settings: Settings):
        self.ads = AdsApiClient(settings, region=settings.ads_api_region)
        self.reports = AdsReportClient(self.ads)

    def fetch_daily_report(self, report_date: date) -> list[AdMetricRow]:
        # レポート定義(spec)。列名・レポートタイプは実APIに合わせて調整する。
        spec = {
            "name": f"daily-keyword-{report_date.isoformat()}",
            "startDate": report_date.isoformat(),
            "endDate": report_date.isoformat(),
            "configuration": {
                "adProduct": "SPONSORED_PRODUCTS",
                "groupBy": ["targeting"],
                "columns": ["keywordId", "keyword", "impressions", "clicks",
                            "cost", "sales", "purchases", "bid"],
                "reportTypeId": "spTargeting",
                "timeUnit": "DAILY",
                "format": "GZIP_JSON",
            },
        }
        rows = self.reports.run_report(spec)  # 作成→ポーリング→DL（実装・検証済み）
        out: list[AdMetricRow] = []
        for r in rows:
            # TODO: 実レポートの列名に合わせてキーを調整
            out.append(AdMetricRow(
                amazon_entity_id=str(r.get("keywordId", "")),
                entity_type="keyword",
                name=r.get("keyword", ""),
                report_date=report_date,
                impressions=int(r.get("impressions", 0)),
                clicks=int(r.get("clicks", 0)),
                spend=float(r.get("cost", 0)),
                sales=float(r.get("sales", 0)),
                orders=int(r.get("purchases", 0)),
                current_bid=float(r["bid"]) if r.get("bid") is not None else None,
            ))
        return out

    def update_bid(self, amazon_entity_id, new_bid) -> None:
        # 例: self.ads.put("/sp/keywords", json=[{"keywordId":..., "bid":new_bid,
        #     "state": "paused" if new_bid is None else "enabled"}])
        raise NotImplementedError(f"keywords更新(入札/停止): {_FILL}")

    def update_budget(self, amazon_entity_id, daily_budget) -> None:
        raise NotImplementedError(f"campaigns予算更新: {_FILL}")


class AmazonAttribution(AttributionPort):
    def __init__(self, settings: Settings):
        self.ads = AdsApiClient(settings, region=settings.ads_api_region)

    def fetch_attribution(self, report_date: date) -> dict:
        raise NotImplementedError(f"Amazon Attributionレポート: {_FILL}")


# =========================================================================
# 出荷ハブ（SFTP）— paramiko等の導入後に実装
# =========================================================================
class SftpShippingHub(ShippingHubPort):
    def __init__(self, settings: Settings):
        self.settings = settings

    def send_shipment_csv(self, amazon_po_number, rows) -> str:
        raise NotImplementedError("paramiko等でAISハブへSFTP送信を実装（要: paramiko追加）")


# =========================================================================
# SP-API: Seller（3P）Orders / FBA Inventory
#   1P(Vendor)とは別アカウント・別認可 → Seller用リフレッシュトークンを使う。
#   client_id/secret は同一アプリで共有。INTEGRATION_MODE=sandbox なら静的サンドボックス。
# =========================================================================
_MARKETPLACE_JP = "A1VC38T7YXB528"  # Amazon.co.jp


def _seller_client(settings: Settings, *, transport=None, token_manager=None) -> SpApiClient:
    # Seller専用のclient_id/secretがあればそれを使い、無ければVendorと共有のものにフォールバック
    client_id = settings.sp_api_seller_client_id or settings.sp_api_client_id or ""
    client_secret = settings.sp_api_seller_client_secret or settings.sp_api_client_secret or ""
    tm = token_manager or LwaTokenManager(
        client_id,
        client_secret,
        settings.sp_api_seller_refresh_token or "",  # ← Seller認可のトークン
        transport=transport,
    )
    return SpApiClient(settings, region=settings.sp_api_region,
                       transport=transport, token_manager=tm)


def _to_int(v) -> int:
    try:
        return int(v or 0)
    except (TypeError, ValueError):
        return 0


class SpApiSellerOrders(SellerOrdersPort):
    """Seller Central / Orders API。getOrders で受注、getOrderItems で明細を取得。"""

    def __init__(self, settings: Settings, *, transport=None, token_manager=None):
        self.sp = _seller_client(settings, transport=transport, token_manager=token_manager)
        self.marketplace_id = _MARKETPLACE_JP

    def fetch_orders(self, *, created_after: str | None = None) -> list[IncomingSellerOrder]:
        from datetime import datetime, timedelta, timezone
        if created_after is None:
            created_after = (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%SZ")
        resp = self.sp.get("/orders/v0/orders", params={
            "MarketplaceIds": self.marketplace_id, "CreatedAfter": created_after})
        payload = (resp.json() or {}).get("payload", {}) or {}
        out: list[IncomingSellerOrder] = []
        for o in payload.get("Orders", []) or []:
            order_id = o.get("AmazonOrderId", "")
            out.append(IncomingSellerOrder(
                amazon_order_id=order_id,
                purchase_date=o.get("PurchaseDate"),
                order_status=o.get("OrderStatus"),
                fulfillment_channel=o.get("FulfillmentChannel"),
                lines=self._fetch_items(order_id) if order_id else [],
            ))
        return out

    def _fetch_items(self, order_id: str) -> list[IncomingSellerOrderLine]:
        resp = self.sp.get(f"/orders/v0/orders/{order_id}/orderItems")
        payload = (resp.json() or {}).get("payload", {}) or {}
        lines: list[IncomingSellerOrderLine] = []
        for it in payload.get("OrderItems", []) or []:
            price = (it.get("ItemPrice", {}) or {}).get("Amount")
            lines.append(IncomingSellerOrderLine(
                sku=it.get("SellerSKU") or "",
                asin=it.get("ASIN"),
                quantity=_to_int(it.get("QuantityOrdered")),
                item_price=float(price) if price is not None else None,
            ))
        return lines


class SpApiFbaInventory(FbaInventoryPort):
    """FBA Inventory API。getInventorySummaries で絶対在庫を取得し DTO へ整形。"""

    def __init__(self, settings: Settings, *, transport=None, token_manager=None):
        self.sp = _seller_client(settings, transport=transport, token_manager=token_manager)
        self.marketplace_id = _MARKETPLACE_JP

    def fetch_inventory(self) -> list[FbaInventoryRow]:
        resp = self.sp.get("/fba/inventory/v1/summaries", params={
            "details": "true", "granularityType": "Marketplace",
            "granularityId": self.marketplace_id, "marketplaceIds": self.marketplace_id})
        payload = (resp.json() or {}).get("payload", {}) or {}
        out: list[FbaInventoryRow] = []
        for s in payload.get("inventorySummaries", []) or []:
            d = s.get("inventoryDetails", {}) or {}
            inbound = (_to_int(d.get("inboundWorkingQuantity"))
                       + _to_int(d.get("inboundShippedQuantity"))
                       + _to_int(d.get("inboundReceivingQuantity")))
            reserved = _to_int((d.get("reservedQuantity", {}) or {}).get("totalReservedQuantity"))
            unfulfillable = _to_int((d.get("unfulfillableQuantity", {}) or {}).get("totalUnfulfillableQuantity"))
            out.append(FbaInventoryRow(
                seller_sku=s.get("sellerSku") or "",
                asin=s.get("asin"),
                fnsku=s.get("fnSku"),
                fulfillable=_to_int(d.get("fulfillableQuantity")),
                inbound=inbound,
                reserved=reserved,
                unfulfillable=unfulfillable,
            ))
        return out


# =========================================================================
# 他チャネル取り込み: Excel在庫(実装) / メールPDF注文(差し替え口)
# =========================================================================
class ExcelInventoryDoc(InventoryDocPort):
    """Excel在庫表(.xlsx)をopenpyxlで解析し、棚卸カウント行を返す。

    ヘッダ行から SKU / ASIN / 数量 列を推定する。source はファイルパス。
    Chat添付/Driveリンクからの取得は外側(配信層)の責務とし、ここはパス→行に専念する。
    """
    _SKU_KEYS = {"sku"}
    _ASIN_KEYS = {"asin"}
    _QTY_KEYS = {"数量", "在庫", "在庫数", "qty", "quantity", "count", "数"}

    def parse(self, source: str) -> list[InventoryCountRow]:
        import openpyxl  # 実行時import（既にrequirementsに含む）
        wb = openpyxl.load_workbook(source, read_only=True, data_only=True)
        ws = wb.active
        rows = ws.iter_rows(values_only=True)
        try:
            header = [str(c).strip().lower() if c is not None else "" for c in next(rows)]
        except StopIteration:
            return []

        def _find(keys: set[str]) -> int | None:
            for i, h in enumerate(header):
                if h in keys:
                    return i
            return None

        i_sku = _find(self._SKU_KEYS)
        i_asin = _find(self._ASIN_KEYS)
        i_qty = _find(self._QTY_KEYS)
        out: list[InventoryCountRow] = []
        if i_sku is None or i_qty is None:
            return out
        for r in rows:
            sku = r[i_sku] if i_sku < len(r) else None
            qty = r[i_qty] if i_qty < len(r) else None
            if sku is None or qty is None:
                continue
            try:
                qn = int(qty)
            except (TypeError, ValueError):
                continue
            asin = r[i_asin] if (i_asin is not None and i_asin < len(r)) else None
            out.append(InventoryCountRow(sku=str(sku).strip(),
                                         asin=str(asin).strip() if asin else None,
                                         quantity=qn))
        return out


class PdfOrderDoc(OrderDocPort):
    """メール/PDF注文の構造化抽出（差し替え口）。

    本実装では PDF/メール本文を読み取り、AIPort(Claude)で
    {外部参照, 明細(sku/asin/qty/price), 信頼度} を抽出する想定。
    低信頼の結果は呼び出し側(seller_service.ingest_order_documents)が保留する。
    """
    def __init__(self, settings: Settings, ai=None):
        self.settings = settings
        self.ai = ai  # AIPort（Claude）を注入

    def parse(self, source: str) -> ParsedOrderDoc:
        raise NotImplementedError(
            f"PDF注文抽出: PDF/メールの読込とAI抽出(信頼度付き)を実装してください。source={source}"
        )

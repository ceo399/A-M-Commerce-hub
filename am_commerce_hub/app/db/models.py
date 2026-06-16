"""ドメインモデル（全フロー統合）。

設計方針:
- Amazon固有の識別子（ASIN, PO番号など）は欄として持つが、自社の主キーは独立。
  → API未取得でも自社内でレコードを完結して扱える。
- 人手の承認ゲートは ApprovalTask で横断的に表現。
- 在庫はロケーション×所有状態の単一台帳 StockMovement に一本化（現在値テーブルは持たない）。
  現在在庫は台帳の集計から導出し、Amazon等の絶対値は InventorySnapshot で突合する。
"""
from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin

MARKETPLACE_JP = "A1VC38T7YXB528"  # Amazon.co.jp


# =========================================================================
# 列挙型
# =========================================================================
class ProductStatus(str, enum.Enum):
    DRAFT = "draft"            # マスタ登録のみ／Amazon未出品
    ACTIVE = "active"          # 出品中
    DISCONTINUED = "discontinued"  # 廃盤・出品停止


class ListingDraftStatus(str, enum.Enum):
    GENERATING = "generating"        # AI生成中
    PENDING_APPROVAL = "pending_approval"
    APPROVED = "approved"
    PUBLISHED = "published"
    REJECTED = "rejected"


class OrderStatus(str, enum.Enum):
    RECEIVED = "received"            # PO受信
    MATCHING = "matching"            # 在庫照合中
    PARTIALLY_BACKORDERED = "partially_backordered"
    READY_TO_SHIP = "ready_to_ship"  # 出荷承認待ち／引当済
    PACKED = "packed"
    SHIPPED = "shipped"              # ASN送信済
    INVOICED = "invoiced"
    CLOSED = "closed"


class OrderLineStatus(str, enum.Enum):
    PENDING = "pending"
    ALLOCATED = "allocated"          # 即納引当可能
    BACKORDERED = "backordered"      # 在庫不足→メーカー発注
    PACKED = "packed"
    SHIPPED = "shipped"


class SupplierPOStatus(str, enum.Enum):
    DRAFT = "draft"                  # 自動作成された発注書
    PENDING_APPROVAL = "pending_approval"
    APPROVED = "approved"
    RECEIVED = "received"            # メーカー入荷・検品済
    CANCELLED = "cancelled"


class InventoryState(str, enum.Enum):
    """在庫のロケーション×所有状態（単一台帳のステート）。"""
    SUPPLIER_PIPELINE = "supplier_pipeline"              # 仕入先発注済み・未入荷
    OWN_WAREHOUSE_AVAILABLE = "own_warehouse_available"  # 自社倉庫・引当可能
    OWN_WAREHOUSE_RESERVED = "own_warehouse_reserved"    # 自社倉庫・引当済み
    IN_TRANSIT_TO_AMAZON_1P = "in_transit_to_amazon_1p"  # 1P出荷中（Amazon受領前=自社所有）
    FBA_INBOUND = "fba_inbound"                          # FBA入庫中
    FBA_FULFILLABLE = "fba_fulfillable"                  # FBA販売可能
    FBA_RESERVED = "fba_reserved"                        # FBA引当済み/予約
    FBA_UNFULFILLABLE = "fba_unfulfillable"              # FBA不良在庫


class ApprovalType(str, enum.Enum):
    DAILY_SHIPMENT = "daily_shipment"          # 物流責任者の日次出荷承認
    SUPPLIER_PO = "supplier_po"                # 購買担当者の発注承認
    LISTING = "listing"                        # MD・商品企画の出品承認
    AD_BID = "ad_bid"                          # マーケ責任者の入札一括承認


class ApprovalStatus(str, enum.Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class Role(str, enum.Enum):
    """ユーザーロール。承認種別に対応（ADMINは全権）。"""
    ADMIN = "admin"
    LOGISTICS = "logistics"          # 物流責任者 → DAILY_SHIPMENT
    PURCHASING = "purchasing"        # 購買担当 → SUPPLIER_PO
    MERCHANDISING = "merchandising"  # MD・商品企画 → LISTING
    MARKETING = "marketing"          # マーケ責任者 → AD_BID


class TwoFactorMethod(str, enum.Enum):
    EMAIL = "email"
    SMS = "sms"


# 承認種別 → 必要ロールの対応表
APPROVAL_ROLE_MAP: dict[ApprovalType, Role] = {
    ApprovalType.DAILY_SHIPMENT: Role.LOGISTICS,
    ApprovalType.SUPPLIER_PO: Role.PURCHASING,
    ApprovalType.LISTING: Role.MERCHANDISING,
    ApprovalType.AD_BID: Role.MARKETING,
}


class AdEntityType(str, enum.Enum):
    CAMPAIGN = "campaign"
    AD_GROUP = "ad_group"
    KEYWORD = "keyword"


class BidRecommendationStatus(str, enum.Enum):
    PROPOSED = "proposed"            # AIが提案
    APPROVED = "approved"
    REJECTED = "rejected"
    APPLIED = "applied"              # Ads API反映済


# =========================================================================
# 商品マスタ / 在庫
# =========================================================================
class Product(Base, TimestampMixin):
    __tablename__ = "products"

    id: Mapped[int] = mapped_column(primary_key=True)
    sku: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    jan: Mapped[str | None] = mapped_column(String(20), index=True)  # JAN/EAN
    asin: Mapped[str | None] = mapped_column(String(20), index=True)  # Amazon登録後に付与
    manufacturer: Mapped[str | None] = mapped_column(String(128))
    brand: Mapped[str | None] = mapped_column(String(128))
    name: Mapped[str] = mapped_column(String(255))
    spec: Mapped[dict] = mapped_column(JSON, default=dict)   # メーカーカタログ由来のスペック
    cost_price: Mapped[float | None] = mapped_column(Numeric(12, 2))
    list_price: Mapped[float | None] = mapped_column(Numeric(12, 2))
    status: Mapped[ProductStatus] = mapped_column(
        Enum(ProductStatus), default=ProductStatus.DRAFT, index=True
    )
    reorder_point: Mapped[int] = mapped_column(Integer, default=0)  # 発注点（商品属性）

    listing_drafts: Mapped[list["ListingDraft"]] = relationship(
        back_populates="product", cascade="all, delete-orphan"
    )


class StockMovement(Base, TimestampMixin):
    """在庫の単一台帳（追記専用）。1行 = ある数量を from_state → to_state へ移動。
    入庫は from_state=None、流出（販売/Amazon受領）は to_state=None。
    現在在庫はこの台帳の集計から導出し、現在値テーブルは持たない（単一の真実）。"""
    __tablename__ = "stock_movements"
    __table_args__ = (
        UniqueConstraint("source_system", "source_event_id", name="uq_movement_idempotency"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), index=True)
    marketplace_id: Mapped[str] = mapped_column(String(20), default=MARKETPLACE_JP)
    from_state: Mapped[InventoryState | None] = mapped_column(Enum(InventoryState, values_callable=lambda e: [m.value for m in e]))
    to_state: Mapped[InventoryState | None] = mapped_column(Enum(InventoryState, values_callable=lambda e: [m.value for m in e]))
    quantity: Mapped[int] = mapped_column(Integer)
    reason: Mapped[str] = mapped_column(String(32))            # inbound/allocate/release/outbound/adjust/reconcile
    ref_type: Mapped[str | None] = mapped_column(String(32))   # 参照元種別（order_line/supplier_po等）
    ref_id: Mapped[int | None] = mapped_column(Integer)
    source_system: Mapped[str] = mapped_column(String(32), default="manual")
    source_event_id: Mapped[str] = mapped_column(String(64))   # 冪等キー（無指定時はサービスがUUID付与）
    note: Mapped[str | None] = mapped_column(Text)


class InventorySnapshot(Base, TimestampMixin):
    """外部（Amazon FBA等）が報告する絶対在庫。台帳との差分reconcile用。"""
    __tablename__ = "inventory_snapshots"

    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), index=True)
    marketplace_id: Mapped[str] = mapped_column(String(20), default=MARKETPLACE_JP)
    state: Mapped[InventoryState] = mapped_column(Enum(InventoryState, values_callable=lambda e: [m.value for m in e]))
    quantity: Mapped[int] = mapped_column(Integer)
    source_system: Mapped[str] = mapped_column(String(32))
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


# =========================================================================
# フロー3: カタログ自動照合・出品ドラフト
# =========================================================================
class CatalogSyncJob(Base, TimestampMixin):
    __tablename__ = "catalog_sync_jobs"

    id: Mapped[int] = mapped_column(primary_key=True)
    source_filename: Mapped[str] = mapped_column(String(255))
    total_rows: Mapped[int] = mapped_column(Integer, default=0)
    new_products: Mapped[int] = mapped_column(Integer, default=0)
    discontinued: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(32), default="completed")
    result: Mapped[dict] = mapped_column(JSON, default=dict)


class ListingDraft(Base, TimestampMixin):
    """AIが生成したAmazon出品ドラフト。"""
    __tablename__ = "listing_drafts"

    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), index=True)
    title: Mapped[str | None] = mapped_column(String(255))
    bullet_points: Mapped[list] = mapped_column(JSON, default=list)
    description: Mapped[str | None] = mapped_column(Text)
    status: Mapped[ListingDraftStatus] = mapped_column(
        Enum(ListingDraftStatus), default=ListingDraftStatus.GENERATING, index=True
    )
    generated_by: Mapped[str | None] = mapped_column(String(64))  # 使用AIモデル
    published_asin: Mapped[str | None] = mapped_column(String(20))

    product: Mapped[Product] = relationship(back_populates="listing_drafts")


# =========================================================================
# フロー2: 受注（Amazon PO）→ 出荷 → 請求
# =========================================================================
class PurchaseOrder(Base, TimestampMixin):
    """Amazonからの発注（ベンダーPO）。"""
    __tablename__ = "purchase_orders"

    id: Mapped[int] = mapped_column(primary_key=True)
    amazon_po_number: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    status: Mapped[OrderStatus] = mapped_column(
        Enum(OrderStatus), default=OrderStatus.RECEIVED, index=True
    )
    ship_to: Mapped[str | None] = mapped_column(String(255))
    asn_id: Mapped[str | None] = mapped_column(String(64))   # ASN送信後
    invoice_id: Mapped[str | None] = mapped_column(String(64))

    lines: Mapped[list["OrderLine"]] = relationship(
        back_populates="order", cascade="all, delete-orphan"
    )


class OrderLine(Base, TimestampMixin):
    __tablename__ = "order_lines"

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("purchase_orders.id"), index=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), index=True)
    qty_ordered: Mapped[int] = mapped_column(Integer)
    qty_confirmed: Mapped[int] = mapped_column(Integer, default=0)     # 即納可能
    qty_backordered: Mapped[int] = mapped_column(Integer, default=0)   # 不足分
    unit_price: Mapped[float | None] = mapped_column(Numeric(12, 2))
    status: Mapped[OrderLineStatus] = mapped_column(
        Enum(OrderLineStatus), default=OrderLineStatus.PENDING
    )

    order: Mapped[PurchaseOrder] = relationship(back_populates="lines")
    product: Mapped[Product] = relationship()


class SellerOrder(Base, TimestampMixin):
    """3P（Seller Central: FBA / 自社出荷）の注文。1PのPurchaseOrderとは別物。"""
    __tablename__ = "seller_orders"

    id: Mapped[int] = mapped_column(primary_key=True)
    amazon_order_id: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    marketplace_id: Mapped[str] = mapped_column(String(20), default=MARKETPLACE_JP)
    purchase_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    order_status: Mapped[str | None] = mapped_column(String(32))        # Unshipped/Shipped/Canceled等
    fulfillment_channel: Mapped[str | None] = mapped_column(String(8))  # AFN(FBA)/MFN(自社出荷)
    source_system: Mapped[str] = mapped_column(String(32), default="sp_api_seller")

    lines: Mapped[list["SellerOrderLine"]] = relationship(
        back_populates="order", cascade="all, delete-orphan"
    )


class SellerOrderLine(Base, TimestampMixin):
    __tablename__ = "seller_order_lines"

    id: Mapped[int] = mapped_column(primary_key=True)
    seller_order_id: Mapped[int] = mapped_column(ForeignKey("seller_orders.id"), index=True)
    product_id: Mapped[int | None] = mapped_column(ForeignKey("products.id"), index=True)
    sku: Mapped[str] = mapped_column(String(64))
    asin: Mapped[str | None] = mapped_column(String(20))
    quantity: Mapped[int] = mapped_column(Integer)
    item_price: Mapped[float | None] = mapped_column(Numeric(12, 2))

    order: Mapped[SellerOrder] = relationship(back_populates="lines")
    product: Mapped[Product | None] = relationship()


class SupplierPurchaseOrder(Base, TimestampMixin):
    """不足在庫に対するメーカー発注書。"""
    __tablename__ = "supplier_purchase_orders"

    id: Mapped[int] = mapped_column(primary_key=True)
    supplier_name: Mapped[str] = mapped_column(String(128))
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), index=True)
    quantity: Mapped[int] = mapped_column(Integer)
    related_order_line_id: Mapped[int | None] = mapped_column(ForeignKey("order_lines.id"))
    status: Mapped[SupplierPOStatus] = mapped_column(
        Enum(SupplierPOStatus), default=SupplierPOStatus.DRAFT, index=True
    )

    product: Mapped[Product] = relationship()


# =========================================================================
# フロー1: 広告運用
# =========================================================================
class AdEntity(Base, TimestampMixin):
    """キャンペーン/広告グループ/キーワードの階層を1テーブルで表現。"""
    __tablename__ = "ad_entities"

    id: Mapped[int] = mapped_column(primary_key=True)
    amazon_entity_id: Mapped[str | None] = mapped_column(String(64), index=True)
    entity_type: Mapped[AdEntityType] = mapped_column(Enum(AdEntityType))
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("ad_entities.id"))
    name: Mapped[str] = mapped_column(String(255))
    current_bid: Mapped[float | None] = mapped_column(Numeric(10, 2))
    daily_budget: Mapped[float | None] = mapped_column(Numeric(12, 2))
    is_brand_awareness: Mapped[bool] = mapped_column(Boolean, default=False)


class AdMetricSnapshot(Base, TimestampMixin):
    """日次の広告指標スナップショット。"""
    __tablename__ = "ad_metric_snapshots"

    id: Mapped[int] = mapped_column(primary_key=True)
    ad_entity_id: Mapped[int] = mapped_column(ForeignKey("ad_entities.id"), index=True)
    report_date: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    impressions: Mapped[int] = mapped_column(Integer, default=0)
    clicks: Mapped[int] = mapped_column(Integer, default=0)
    spend: Mapped[float] = mapped_column(Numeric(12, 2), default=0)
    sales: Mapped[float] = mapped_column(Numeric(12, 2), default=0)
    orders: Mapped[int] = mapped_column(Integer, default=0)

    @property
    def acos(self) -> float | None:
        s = float(self.sales or 0)
        return float(self.spend) / s if s else None


class BidRecommendation(Base, TimestampMixin):
    """AIによる入札増減・停止提案。"""
    __tablename__ = "bid_recommendations"

    id: Mapped[int] = mapped_column(primary_key=True)
    ad_entity_id: Mapped[int] = mapped_column(ForeignKey("ad_entities.id"), index=True)
    current_bid: Mapped[float | None] = mapped_column(Numeric(10, 2))
    recommended_bid: Mapped[float | None] = mapped_column(Numeric(10, 2))  # Noneは停止提案
    action: Mapped[str] = mapped_column(String(32))   # increase / decrease / pause / keep
    rationale: Mapped[str | None] = mapped_column(Text)
    measured_acos: Mapped[float | None] = mapped_column(Float)
    status: Mapped[BidRecommendationStatus] = mapped_column(
        Enum(BidRecommendationStatus), default=BidRecommendationStatus.PROPOSED, index=True
    )
    entity: Mapped["AdEntity"] = relationship()


# =========================================================================
# 横断: 承認ゲート
# =========================================================================
class ApprovalTask(Base, TimestampMixin):
    """人手の承認を要する各種ゲートを統一的に管理。"""
    __tablename__ = "approval_tasks"

    id: Mapped[int] = mapped_column(primary_key=True)
    approval_type: Mapped[ApprovalType] = mapped_column(Enum(ApprovalType), index=True)
    status: Mapped[ApprovalStatus] = mapped_column(
        Enum(ApprovalStatus), default=ApprovalStatus.PENDING, index=True
    )
    ref_type: Mapped[str] = mapped_column(String(32))   # 対象種別
    ref_id: Mapped[int] = mapped_column(Integer)        # 対象ID
    summary: Mapped[str | None] = mapped_column(Text)
    decided_by: Mapped[str | None] = mapped_column(String(64))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


# =========================================================================
# 認証 / ユーザー
# =========================================================================
class User(Base, TimestampMixin):
    """管理者が発行するユーザーアカウント。"""
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    full_name: Mapped[str | None] = mapped_column(String(128))
    password_hash: Mapped[str] = mapped_column(String(255))
    phone: Mapped[str | None] = mapped_column(String(32))      # SMS二段階認証用
    roles: Mapped[list] = mapped_column(JSON, default=list)     # Roleの値のリスト
    two_factor_method: Mapped[TwoFactorMethod] = mapped_column(
        Enum(TwoFactorMethod), default=TwoFactorMethod.EMAIL
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=True)

    def has_role(self, role: Role) -> bool:
        return Role.ADMIN.value in self.roles or role.value in self.roles


class LoginChallenge(Base, TimestampMixin):
    """ログイン時に発行するワンタイムコード（二段階認証）。"""
    __tablename__ = "login_challenges"

    id: Mapped[int] = mapped_column(primary_key=True)
    challenge_uid: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    code_hash: Mapped[str] = mapped_column(String(255))
    method: Mapped[TwoFactorMethod] = mapped_column(Enum(TwoFactorMethod))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    consumed: Mapped[bool] = mapped_column(Boolean, default=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0)

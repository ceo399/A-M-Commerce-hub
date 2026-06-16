"""????????????????

????:
- Amazon???????ASIN, PO????????????????????????
  ? API??????????????????????
- ????????? ApprovalTask ????????
- ??????????????????? StockMovement ???????????????????
  ????????????????Amazon?????? InventorySnapshot ??????
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
# ???
# =========================================================================
class ProductStatus(str, enum.Enum):
    DRAFT = "draft"            # ????????Amazon???
    ACTIVE = "active"          # ???
    DISCONTINUED = "discontinued"  # ???????


class ListingDraftStatus(str, enum.Enum):
    GENERATING = "generating"        # AI???
    PENDING_APPROVAL = "pending_approval"
    APPROVED = "approved"
    PUBLISHED = "published"
    REJECTED = "rejected"


class OrderStatus(str, enum.Enum):
    RECEIVED = "received"            # PO??
    MATCHING = "matching"            # ?????
    PARTIALLY_BACKORDERED = "partially_backordered"
    READY_TO_SHIP = "ready_to_ship"  # ??????????
    PACKED = "packed"
    SHIPPED = "shipped"              # ASN???
    INVOICED = "invoiced"
    CLOSED = "closed"


class OrderLineStatus(str, enum.Enum):
    PENDING = "pending"
    ALLOCATED = "allocated"          # ??????
    BACKORDERED = "backordered"      # ???????????
    PACKED = "packed"
    SHIPPED = "shipped"


class SupplierPOStatus(str, enum.Enum):
    DRAFT = "draft"                  # ??????????
    PENDING_APPROVAL = "pending_approval"
    APPROVED = "approved"
    RECEIVED = "received"            # ??????????
    CANCELLED = "cancelled"


class InventoryState(str, enum.Enum):
    """??????????????????????????"""
    SUPPLIER_PIPELINE = "supplier_pipeline"              # ???????????
    OWN_WAREHOUSE_AVAILABLE = "own_warehouse_available"  # ?????????
    OWN_WAREHOUSE_RESERVED = "own_warehouse_reserved"    # ?????????
    IN_TRANSIT_TO_AMAZON_1P = "in_transit_to_amazon_1p"  # 1P????Amazon???=?????
    FBA_INBOUND = "fba_inbound"                          # FBA???
    FBA_FULFILLABLE = "fba_fulfillable"                  # FBA????
    FBA_RESERVED = "fba_reserved"                        # FBA????/??
    FBA_UNFULFILLABLE = "fba_unfulfillable"              # FBA????


class ApprovalType(str, enum.Enum):
    DAILY_SHIPMENT = "daily_shipment"          # ????????????
    SUPPLIER_PO = "supplier_po"                # ??????????
    LISTING = "listing"                        # MD??????????
    AD_BID = "ad_bid"                          # ?????????????


class ApprovalStatus(str, enum.Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class Role(str, enum.Enum):
    """????????????????ADMIN?????"""
    ADMIN = "admin"
    LOGISTICS = "logistics"          # ????? ? DAILY_SHIPMENT
    PURCHASING = "purchasing"        # ???? ? SUPPLIER_PO
    MERCHANDISING = "merchandising"  # MD????? ? LISTING
    MARKETING = "marketing"          # ?????? ? AD_BID


class TwoFactorMethod(str, enum.Enum):
    EMAIL = "email"
    SMS = "sms"


# ???? ? ?????????
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
    PROPOSED = "proposed"            # AI???
    APPROVED = "approved"
    REJECTED = "rejected"
    APPLIED = "applied"              # Ads API???


# =========================================================================
# ????? / ??
# =========================================================================
class Product(Base, TimestampMixin):
    __tablename__ = "products"

    id: Mapped[int] = mapped_column(primary_key=True)
    sku: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    jan: Mapped[str | None] = mapped_column(String(20), index=True)  # JAN/EAN
    asin: Mapped[str | None] = mapped_column(String(20), index=True)  # Amazon??????
    manufacturer: Mapped[str | None] = mapped_column(String(128))
    brand: Mapped[str | None] = mapped_column(String(128))
    name: Mapped[str] = mapped_column(String(255))
    spec: Mapped[dict] = mapped_column(JSON, default=dict)   # ???????????????
    cost_price: Mapped[float | None] = mapped_column(Numeric(12, 2))
    list_price: Mapped[float | None] = mapped_column(Numeric(12, 2))
    status: Mapped[ProductStatus] = mapped_column(
        Enum(ProductStatus), default=ProductStatus.DRAFT, index=True
    )
    reorder_point: Mapped[int] = mapped_column(Integer, default=0)  # ?????????

    listing_drafts: Mapped[list["ListingDraft"]] = relationship(
        back_populates="product", cascade="all, delete-orphan"
    )


class StockMovement(Base, TimestampMixin):
    """??????????????1? = ????? from_state ? to_state ????
    ??? from_state=None??????/Amazon???? to_state=None?
    ??????????????????????????????????????"""
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
    ref_type: Mapped[str | None] = mapped_column(String(32))   # ??????order_line/supplier_po??
    ref_id: Mapped[int | None] = mapped_column(Integer)
    source_system: Mapped[str] = mapped_column(String(32), default="manual")
    source_event_id: Mapped[str] = mapped_column(String(64))   # ???????????????UUID???
    note: Mapped[str | None] = mapped_column(Text)


class InventorySnapshot(Base, TimestampMixin):
    """???Amazon FBA??????????????????reconcile??"""
    __tablename__ = "inventory_snapshots"

    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), index=True)
    marketplace_id: Mapped[str] = mapped_column(String(20), default=MARKETPLACE_JP)
    state: Mapped[InventoryState] = mapped_column(Enum(InventoryState, values_callable=lambda e: [m.value for m in e]))
    quantity: Mapped[int] = mapped_column(Integer)
    source_system: Mapped[str] = mapped_column(String(32))
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


# =========================================================================
# ???3: ???????????????
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
    """AI?????Amazon???????"""
    __tablename__ = "listing_drafts"

    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), index=True)
    title: Mapped[str | None] = mapped_column(String(255))
    bullet_points: Mapped[list] = mapped_column(JSON, default=list)
    description: Mapped[str | None] = mapped_column(Text)
    status: Mapped[ListingDraftStatus] = mapped_column(
        Enum(ListingDraftStatus), default=ListingDraftStatus.GENERATING, index=True
    )
    generated_by: Mapped[str | None] = mapped_column(String(64))  # ??AI???
    published_asin: Mapped[str | None] = mapped_column(String(20))

    product: Mapped[Product] = relationship(back_populates="listing_drafts")


# =========================================================================
# ???2: ???Amazon PO?? ?? ? ??
# =========================================================================
class PurchaseOrder(Base, TimestampMixin):
    """Amazon??????????PO??"""
    __tablename__ = "purchase_orders"

    id: Mapped[int] = mapped_column(primary_key=True)
    amazon_po_number: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    status: Mapped[OrderStatus] = mapped_column(
        Enum(OrderStatus), default=OrderStatus.RECEIVED, index=True
    )
    ship_to: Mapped[str | None] = mapped_column(String(255))
    asn_id: Mapped[str | None] = mapped_column(String(64))   # ASN???
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
    qty_confirmed: Mapped[int] = mapped_column(Integer, default=0)     # ????
    qty_backordered: Mapped[int] = mapped_column(Integer, default=0)   # ???
    unit_price: Mapped[float | None] = mapped_column(Numeric(12, 2))
    status: Mapped[OrderLineStatus] = mapped_column(
        Enum(OrderLineStatus), default=OrderLineStatus.PENDING
    )

    order: Mapped[PurchaseOrder] = relationship(back_populates="lines")
    product: Mapped[Product] = relationship()


class SellerOrder(Base, TimestampMixin):
    """3P?Seller Central: FBA / ?????????1P?PurchaseOrder?????"""
    __tablename__ = "seller_orders"

    id: Mapped[int] = mapped_column(primary_key=True)
    amazon_order_id: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    marketplace_id: Mapped[str] = mapped_column(String(20), default=MARKETPLACE_JP)
    purchase_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    order_status: Mapped[str | None] = mapped_column(String(32))        # Unshipped/Shipped/Canceled?
    fulfillment_channel: Mapped[str | None] = mapped_column(String(8))  # AFN(FBA)/MFN(????)
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
    """????????????????"""
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
# ???1: ????
# =========================================================================
class AdEntity(Base, TimestampMixin):
    """??????/??????/?????????1????????"""
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
    """????????????????"""
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
    """AI?????????????"""
    __tablename__ = "bid_recommendations"

    id: Mapped[int] = mapped_column(primary_key=True)
    ad_entity_id: Mapped[int] = mapped_column(ForeignKey("ad_entities.id"), index=True)
    current_bid: Mapped[float | None] = mapped_column(Numeric(10, 2))
    recommended_bid: Mapped[float | None] = mapped_column(Numeric(10, 2))  # None?????
    action: Mapped[str] = mapped_column(String(32))   # increase / decrease / pause / keep
    rationale: Mapped[str | None] = mapped_column(Text)
    measured_acos: Mapped[float | None] = mapped_column(Float)
    status: Mapped[BidRecommendationStatus] = mapped_column(
        Enum(BidRecommendationStatus), default=BidRecommendationStatus.PROPOSED, index=True
    )
    entity: Mapped["AdEntity"] = relationship()


# =========================================================================
# ??: ?????
# =========================================================================
class ApprovalTask(Base, TimestampMixin):
    """??????????????????????"""
    __tablename__ = "approval_tasks"

    id: Mapped[int] = mapped_column(primary_key=True)
    approval_type: Mapped[ApprovalType] = mapped_column(Enum(ApprovalType), index=True)
    status: Mapped[ApprovalStatus] = mapped_column(
        Enum(ApprovalStatus), default=ApprovalStatus.PENDING, index=True
    )
    ref_type: Mapped[str] = mapped_column(String(32))   # ????
    ref_id: Mapped[int] = mapped_column(Integer)        # ??ID
    summary: Mapped[str | None] = mapped_column(Text)
    decided_by: Mapped[str | None] = mapped_column(String(64))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


# =========================================================================
# ?? / ????
# =========================================================================
class User(Base, TimestampMixin):
    """??????????????????"""
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    full_name: Mapped[str | None] = mapped_column(String(128))
    password_hash: Mapped[str] = mapped_column(String(255))
    phone: Mapped[str | None] = mapped_column(String(32))      # SMS??????
    roles: Mapped[list] = mapped_column(JSON, default=list)     # Role??????
    two_factor_method: Mapped[TwoFactorMethod] = mapped_column(
        Enum(TwoFactorMethod), default=TwoFactorMethod.EMAIL
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=True)

    def has_role(self, role: Role) -> bool:
        return Role.ADMIN.value in self.roles or role.value in self.roles


class LoginChallenge(Base, TimestampMixin):
    """??????????????????????????"""
    __tablename__ = "login_challenges"

    id: Mapped[int] = mapped_column(primary_key=True)
    challenge_uid: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    code_hash: Mapped[str] = mapped_column(String(255))
    method: Mapped[TwoFactorMethod] = mapped_column(Enum(TwoFactorMethod))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    consumed: Mapped[bool] = mapped_column(Boolean, default=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0)

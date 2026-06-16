"""seller orders (3P) tables

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-06-09

3P（Seller Central: FBA / 自社出荷）の注文を格納する seller_orders / seller_order_lines を追加。
1P の purchase_orders とは別系統。状態・出荷経路は移行を簡潔にするため文字列で保持。
"""
from alembic import op
import sqlalchemy as sa

revision = "c3d4e5f6a7b8"
down_revision = "b2c3d4e5f6a7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "seller_orders",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("amazon_order_id", sa.String(length=32), nullable=False),
        sa.Column("marketplace_id", sa.String(length=20), nullable=False,
                  server_default=sa.text("'A1VC38T7YXB528'")),
        sa.Column("purchase_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("order_status", sa.String(length=32), nullable=True),
        sa.Column("fulfillment_channel", sa.String(length=8), nullable=True),
        sa.Column("source_system", sa.String(length=32), nullable=False,
                  server_default=sa.text("'sp_api_seller'")),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_seller_orders_amazon_order_id"), "seller_orders",
                    ["amazon_order_id"], unique=True)

    op.create_table(
        "seller_order_lines",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("seller_order_id", sa.Integer(), nullable=False),
        sa.Column("product_id", sa.Integer(), nullable=True),
        sa.Column("sku", sa.String(length=64), nullable=False),
        sa.Column("asin", sa.String(length=20), nullable=True),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("item_price", sa.Numeric(precision=12, scale=2), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.ForeignKeyConstraint(["seller_order_id"], ["seller_orders.id"]),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_seller_order_lines_seller_order_id"), "seller_order_lines",
                    ["seller_order_id"], unique=False)
    op.create_index(op.f("ix_seller_order_lines_product_id"), "seller_order_lines",
                    ["product_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_seller_order_lines_product_id"), table_name="seller_order_lines")
    op.drop_index(op.f("ix_seller_order_lines_seller_order_id"), table_name="seller_order_lines")
    op.drop_table("seller_order_lines")
    op.drop_index(op.f("ix_seller_orders_amazon_order_id"), table_name="seller_orders")
    op.drop_table("seller_orders")

"""unified inventory ledger (replace InventoryItem/InventoryMovement)

旧 inventory_items / inventory_movements を廃し、ロケーション×所有状態の
単一台帳 stock_movements + 突合用 inventory_snapshots に置換する。
products に reorder_point を追加。SQLite/PostgreSQL 両対応。

Revision ID: b2c3d4e5f6a7
Revises: 7de3e6dbe2d9
Create Date: 2026-06-09
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "b2c3d4e5f6a7"
down_revision = "7de3e6dbe2d9"
branch_labels = None
depends_on = None

INV_STATES = (
    "supplier_pipeline", "own_warehouse_available", "own_warehouse_reserved",
    "in_transit_to_amazon_1p", "fba_inbound", "fba_fulfillable",
    "fba_reserved", "fba_unfulfillable",
)


def _inv_enum(is_pg: bool):
    # PG: 型生成はマイグレーションが明示的に1回だけ行うため、列では生成させない。
    #     sa.Enum の create_type=False は PG に伝わらないので postgresql.ENUM を使う。
    if is_pg:
        return postgresql.ENUM(*INV_STATES, name="inventorystate", create_type=False)
    # SQLite 等: VARCHAR(+CHECK) として扱う
    return sa.Enum(*INV_STATES, name="inventorystate")


def upgrade() -> None:
    bind = op.get_bind()
    is_pg = bind.dialect.name == "postgresql"

    # --- 旧在庫テーブルを廃止 ---
    op.drop_table("inventory_movements")
    op.drop_table("inventory_items")
    if is_pg:
        op.execute("DROP TYPE IF EXISTS movementtype")

    # --- products に発注点を追加 ---
    with op.batch_alter_table("products") as batch:
        batch.add_column(sa.Column("reorder_point", sa.Integer(), nullable=False, server_default="0"))

    # --- PG では enum 型を先に1回だけ作成（列側では create させない） ---
    if is_pg:
        postgresql.ENUM(*INV_STATES, name="inventorystate").create(bind, checkfirst=True)

    # --- 単一台帳 ---
    op.create_table(
        "stock_movements",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("product_id", sa.Integer(), sa.ForeignKey("products.id"), nullable=False),
        sa.Column("marketplace_id", sa.String(length=20), nullable=False, server_default="A1VC38T7YXB528"),
        sa.Column("from_state", _inv_enum(is_pg), nullable=True),
        sa.Column("to_state", _inv_enum(is_pg), nullable=True),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("reason", sa.String(length=32), nullable=False),
        sa.Column("ref_type", sa.String(length=32), nullable=True),
        sa.Column("ref_id", sa.Integer(), nullable=True),
        sa.Column("source_system", sa.String(length=32), nullable=False, server_default="manual"),
        sa.Column("source_event_id", sa.String(length=64), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("source_system", "source_event_id", name="uq_movement_idempotency"),
    )
    op.create_index("ix_stock_movements_product_id", "stock_movements", ["product_id"])

    # --- 突合用スナップショット ---
    op.create_table(
        "inventory_snapshots",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("product_id", sa.Integer(), sa.ForeignKey("products.id"), nullable=False),
        sa.Column("marketplace_id", sa.String(length=20), nullable=False, server_default="A1VC38T7YXB528"),
        sa.Column("state", _inv_enum(is_pg), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("source_system", sa.String(length=32), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_inventory_snapshots_product_id", "inventory_snapshots", ["product_id"])


def downgrade() -> None:
    bind = op.get_bind()
    is_pg = bind.dialect.name == "postgresql"

    op.drop_index("ix_inventory_snapshots_product_id", table_name="inventory_snapshots")
    op.drop_table("inventory_snapshots")
    op.drop_index("ix_stock_movements_product_id", table_name="stock_movements")
    op.drop_table("stock_movements")
    if is_pg:
        op.execute("DROP TYPE IF EXISTS inventorystate")

    with op.batch_alter_table("products") as batch:
        batch.drop_column("reorder_point")

    # 旧テーブルを復元
    movementtype = sa.Enum("inbound", "allocate", "release", "outbound", "adjust", name="movementtype")
    op.create_table(
        "inventory_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("product_id", sa.Integer(), sa.ForeignKey("products.id"), nullable=False),
        sa.Column("location", sa.String(length=32), nullable=True),
        sa.Column("on_hand", sa.Integer(), nullable=True),
        sa.Column("allocated", sa.Integer(), nullable=True),
        sa.Column("reorder_point", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("product_id", "location", name="uq_inv_product_location"),
    )
    op.create_index("ix_inventory_items_product_id", "inventory_items", ["product_id"])
    op.create_table(
        "inventory_movements",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("product_id", sa.Integer(), sa.ForeignKey("products.id"), nullable=False),
        sa.Column("movement_type", movementtype, nullable=True),
        sa.Column("quantity", sa.Integer(), nullable=True),
        sa.Column("ref_type", sa.String(length=32), nullable=True),
        sa.Column("ref_id", sa.Integer(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_inventory_movements_product_id", "inventory_movements", ["product_id"])

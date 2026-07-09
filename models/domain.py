"""
Section 24.1–24.2 domain models (Inventory & Procurement).

Entities:
  - Product (+ Ingredients via `is_ingredient` flag)
  - Supplier
  - PurchaseOrder / PurchaseOrderLineItem
  - GoodsReceipt
  - StockBatch
  - StockTransaction / StockTransactionLine
"""

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    select,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database import Base
from models.db_types import (
    purchase_order_status_enum,
    stock_movement_direction_enum,
    stock_reference_type_enum,
    stock_transaction_type_enum,
)
from models.enums import (
    PurchaseOrderStatus,
    StockMovementDirection,
    StockReferenceType,
    StockTransactionType,
)


class Organization(Base):
    """SaaS tenant — all business tables are scoped by organization_id."""

    __tablename__ = "organizations"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String, nullable=False)
    slug: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    branches: Mapped[list["Branch"]] = relationship(back_populates="organization")
    products: Mapped[list["Product"]] = relationship(back_populates="organization")
    suppliers: Mapped[list["Supplier"]] = relationship(back_populates="organization")
    purchase_orders: Mapped[list["PurchaseOrder"]] = relationship(
        back_populates="organization"
    )
    goods_receipts: Mapped[list["GoodsReceipt"]] = relationship(
        back_populates="organization"
    )
    stock_batches: Mapped[list["StockBatch"]] = relationship(
        back_populates="organization"
    )
    stock_transactions: Mapped[list["StockTransaction"]] = relationship(
        back_populates="organization"
    )


class Branch(Base):
    """Receiving location / store within an organization."""

    __tablename__ = "branches"
    __table_args__ = (
        UniqueConstraint("organization_id", "name"),
        Index("branches_organization_id_idx", "organization_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="RESTRICT"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String, nullable=False)
    location: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    organization: Mapped["Organization"] = relationship(back_populates="branches")
    purchase_orders: Mapped[list["PurchaseOrder"]] = relationship(
        back_populates="branch"
    )
    goods_receipts: Mapped[list["GoodsReceipt"]] = relationship(back_populates="branch")
    stock_batches: Mapped[list["StockBatch"]] = relationship(back_populates="branch")
    stock_transactions: Mapped[list["StockTransaction"]] = relationship(
        back_populates="branch"
    )


class Product(Base):
    """
    Product catalog (Section 24.1).

    Ingredients are NOT a separate table — they are products with `is_ingredient=True`.
    Use `Product.ingredients()` to query the ingredient subset.
    """

    __tablename__ = "products"
    __table_args__ = (
        UniqueConstraint("organization_id", "sku"),
        Index("products_organization_id_is_ingredient_idx", "organization_id", "is_ingredient"),
        Index("products_organization_id_deleted_at_idx", "organization_id", "deleted_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="RESTRICT"),
        nullable=False,
    )
    sku: Mapped[str] = mapped_column(String, nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    unit_of_measure: Mapped[str] = mapped_column(String, default="EA", nullable=False)
    is_ingredient: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    reorder_level: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(18, 4), nullable=True
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
    deleted_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    organization: Mapped["Organization"] = relationship(back_populates="products")
    purchase_order_line_items: Mapped[list["PurchaseOrderLineItem"]] = relationship(
        back_populates="product"
    )
    stock_batches: Mapped[list["StockBatch"]] = relationship(back_populates="product")
    stock_transaction_lines: Mapped[list["StockTransactionLine"]] = relationship(
        back_populates="product"
    )

    @classmethod
    def ingredients(cls):
        """SQLAlchemy selectable for ingredient (raw material) products only."""
        return select(cls).where(cls.is_ingredient.is_(True), cls.deleted_at.is_(None))


class Supplier(Base):
    """Vendor master data (Section 24.1)."""

    __tablename__ = "suppliers"
    __table_args__ = (
        UniqueConstraint("organization_id", "code"),
        Index("suppliers_organization_id_deleted_at_idx", "organization_id", "deleted_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="RESTRICT"),
        nullable=False,
    )
    code: Mapped[str] = mapped_column(String, nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    contact_email: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    contact_phone: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    address: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
    deleted_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    organization: Mapped["Organization"] = relationship(back_populates="suppliers")
    purchase_orders: Mapped[list["PurchaseOrder"]] = relationship(
        back_populates="supplier"
    )


class PurchaseOrder(Base):
    """Purchase order header (Section 24.1)."""

    __tablename__ = "purchase_orders"
    __table_args__ = (
        UniqueConstraint("organization_id", "order_number"),
        Index("purchase_orders_organization_id_status_idx", "organization_id", "status"),
        Index("purchase_orders_branch_id_idx", "branch_id"),
        Index("purchase_orders_supplier_id_idx", "supplier_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="RESTRICT"),
        nullable=False,
    )
    branch_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("branches.id", ondelete="RESTRICT"),
        nullable=False,
    )
    supplier_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("suppliers.id", ondelete="RESTRICT"),
        nullable=False,
    )
    order_number: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[PurchaseOrderStatus] = mapped_column(
        purchase_order_status_enum,
        default=PurchaseOrderStatus.DRAFT,
        nullable=False,
    )
    order_date: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    expected_delivery_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    organization: Mapped["Organization"] = relationship(
        back_populates="purchase_orders"
    )
    branch: Mapped["Branch"] = relationship(back_populates="purchase_orders")
    supplier: Mapped["Supplier"] = relationship(back_populates="purchase_orders")
    line_items: Mapped[list["PurchaseOrderLineItem"]] = relationship(
        back_populates="purchase_order", cascade="all, delete-orphan"
    )
    goods_receipts: Mapped[list["GoodsReceipt"]] = relationship(
        back_populates="purchase_order"
    )


class PurchaseOrderLineItem(Base):
    """Purchase order line item (Section 24.1)."""

    __tablename__ = "purchase_order_line_items"
    __table_args__ = (
        UniqueConstraint("purchase_order_id", "product_id"),
        Index("purchase_order_line_items_product_id_idx", "product_id"),
        CheckConstraint("ordered_quantity > 0", name="ck_po_line_ordered_qty_positive"),
        CheckConstraint("received_quantity >= 0", name="ck_po_line_received_qty_nonneg"),
        CheckConstraint(
            "received_quantity <= ordered_quantity",
            name="ck_po_line_received_lte_ordered",
        ),
        CheckConstraint("unit_price >= 0", name="ck_po_line_unit_price_nonneg"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    purchase_order_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("purchase_orders.id", ondelete="CASCADE"),
        nullable=False,
    )
    product_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("products.id", ondelete="RESTRICT"),
        nullable=False,
    )
    ordered_quantity: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    received_quantity: Mapped[Decimal] = mapped_column(
        Numeric(18, 4), default=Decimal("0"), nullable=False
    )
    unit_price: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    purchase_order: Mapped["PurchaseOrder"] = relationship(back_populates="line_items")
    product: Mapped["Product"] = relationship(back_populates="purchase_order_line_items")


class GoodsReceipt(Base):
    """Goods receipt against a purchase order (Section 24.2)."""

    __tablename__ = "goods_receipts"
    __table_args__ = (
        UniqueConstraint("organization_id", "receipt_number"),
        Index("goods_receipts_organization_id_received_at_idx", "organization_id", "received_at"),
        Index("goods_receipts_purchase_order_id_idx", "purchase_order_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="RESTRICT"),
        nullable=False,
    )
    branch_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("branches.id", ondelete="RESTRICT"),
        nullable=False,
    )
    purchase_order_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("purchase_orders.id", ondelete="RESTRICT"),
        nullable=False,
    )
    receipt_number: Mapped[str] = mapped_column(String, nullable=False)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    organization: Mapped["Organization"] = relationship(back_populates="goods_receipts")
    branch: Mapped["Branch"] = relationship(back_populates="goods_receipts")
    purchase_order: Mapped["PurchaseOrder"] = relationship(back_populates="goods_receipts")
    stock_batches: Mapped[list["StockBatch"]] = relationship(back_populates="goods_receipt")
    stock_transaction: Mapped[Optional["StockTransaction"]] = relationship(
        back_populates="goods_receipt", uselist=False
    )


class StockBatch(Base):
    """Batch-level inventory: batch number, expiry, quantity on hand (Section 24.2)."""

    __tablename__ = "stock_batches"
    __table_args__ = (
        UniqueConstraint("organization_id", "branch_id", "product_id", "batch_number"),
        Index(
            "stock_batches_organization_id_branch_id_product_id_idx",
            "organization_id",
            "branch_id",
            "product_id",
        ),
        Index("stock_batches_expiry_date_idx", "expiry_date"),
        Index("stock_batches_goods_receipt_id_idx", "goods_receipt_id"),
        CheckConstraint("received_quantity > 0", name="ck_stock_batch_received_qty_positive"),
        CheckConstraint("quantity_on_hand >= 0", name="ck_stock_batch_on_hand_nonneg"),
        CheckConstraint(
            "quantity_on_hand <= received_quantity",
            name="ck_stock_batch_on_hand_lte_received",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="RESTRICT"),
        nullable=False,
    )
    branch_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("branches.id", ondelete="RESTRICT"),
        nullable=False,
    )
    product_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("products.id", ondelete="RESTRICT"),
        nullable=False,
    )
    goods_receipt_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("goods_receipts.id", ondelete="RESTRICT"),
        nullable=False,
    )
    batch_number: Mapped[str] = mapped_column(String, nullable=False)
    expiry_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    received_quantity: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    quantity_on_hand: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    unit_cost: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 4), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    organization: Mapped["Organization"] = relationship(back_populates="stock_batches")
    branch: Mapped["Branch"] = relationship(back_populates="stock_batches")
    product: Mapped["Product"] = relationship(back_populates="stock_batches")
    goods_receipt: Mapped["GoodsReceipt"] = relationship(back_populates="stock_batches")
    stock_transaction_lines: Mapped[list["StockTransactionLine"]] = relationship(
        back_populates="stock_batch"
    )


class StockTransaction(Base):
    """
    Immutable stock ledger header (Section 24.2).

    One record per source document. Goods receipts use reference_type=GOODS_RECEIPT
    with an optional goods_receipt_id FK for navigation.
    """

    __tablename__ = "stock_transactions"
    __table_args__ = (
        UniqueConstraint("reference_type", "reference_id"),
        Index(
            "stock_transactions_organization_id_occurred_at_idx",
            "organization_id",
            "occurred_at",
        ),
        Index(
            "stock_transactions_organization_id_transaction_type_idx",
            "organization_id",
            "transaction_type",
        ),
        Index("stock_transactions_branch_id_idx", "branch_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="RESTRICT"),
        nullable=False,
    )
    branch_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("branches.id", ondelete="RESTRICT"),
        nullable=False,
    )
    reference_type: Mapped[StockReferenceType] = mapped_column(
        stock_reference_type_enum, nullable=False
    )
    reference_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    goods_receipt_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("goods_receipts.id", ondelete="RESTRICT"),
        unique=True,
        nullable=True,
    )
    transaction_type: Mapped[StockTransactionType] = mapped_column(
        stock_transaction_type_enum, nullable=False
    )
    reference_number: Mapped[str] = mapped_column(String, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    organization: Mapped["Organization"] = relationship(
        back_populates="stock_transactions"
    )
    branch: Mapped["Branch"] = relationship(back_populates="stock_transactions")
    goods_receipt: Mapped[Optional["GoodsReceipt"]] = relationship(
        back_populates="stock_transaction"
    )
    lines: Mapped[list["StockTransactionLine"]] = relationship(
        back_populates="stock_transaction", cascade="all, delete-orphan"
    )


class StockTransactionLine(Base):
    """Line-level stock movement detail for a StockTransaction (Section 24.2)."""

    __tablename__ = "stock_transaction_lines"
    __table_args__ = (
        Index("stock_transaction_lines_stock_transaction_id_idx", "stock_transaction_id"),
        Index("stock_transaction_lines_product_id_idx", "product_id"),
        Index("stock_transaction_lines_stock_batch_id_idx", "stock_batch_id"),
        CheckConstraint("quantity > 0", name="ck_stock_tx_line_qty_positive"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    stock_transaction_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("stock_transactions.id", ondelete="CASCADE"),
        nullable=False,
    )
    product_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("products.id", ondelete="RESTRICT"),
        nullable=False,
    )
    stock_batch_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("stock_batches.id", ondelete="RESTRICT"),
        nullable=False,
    )
    direction: Mapped[StockMovementDirection] = mapped_column(
        stock_movement_direction_enum, nullable=False
    )
    quantity: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    unit_cost: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 4), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    stock_transaction: Mapped["StockTransaction"] = relationship(back_populates="lines")
    product: Mapped["Product"] = relationship(back_populates="stock_transaction_lines")
    stock_batch: Mapped["StockBatch"] = relationship(
        back_populates="stock_transaction_lines"
    )

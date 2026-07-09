CREATE SCHEMA IF NOT EXISTS public;

CREATE TYPE "PurchaseOrderStatus" AS ENUM (
    'DRAFT',
    'SUBMITTED',
    'APPROVED',
    'SENT',
    'PARTIALLY_RECEIVED',
    'RECEIVED',
    'CANCELLED'
);

CREATE TYPE "StockTransactionType" AS ENUM (
    'GOODS_RECEIPT',
    'ADJUSTMENT',
    'TRANSFER_IN',
    'TRANSFER_OUT',
    'CONSUMPTION',
    'WASTE',
    'RETURN'
);

CREATE TYPE "StockMovementDirection" AS ENUM ('IN', 'OUT');

CREATE TYPE "StockReferenceType" AS ENUM (
    'GOODS_RECEIPT',
    'PRODUCTION_ORDER',
    'DISPATCH',
    'TRANSFER',
    'MATERIAL_REQUEST',
    'MANUAL_ADJUSTMENT'
);

CREATE TABLE organizations (
    id UUID PRIMARY KEY,
    name TEXT NOT NULL,
    slug TEXT NOT NULL UNIQUE,
    is_active BOOLEAN NOT NULL DEFAULT true,
    created_at TIMESTAMPTZ(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ(6) NOT NULL
);

CREATE TABLE branches (
    id UUID PRIMARY KEY,
    organization_id UUID NOT NULL REFERENCES organizations(id) ON DELETE RESTRICT,
    name TEXT NOT NULL,
    location TEXT,
    is_active BOOLEAN NOT NULL DEFAULT true,
    created_at TIMESTAMPTZ(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ(6) NOT NULL,
    UNIQUE (organization_id, name)
);

CREATE INDEX branches_organization_id_idx ON branches (organization_id);

CREATE TABLE products (
    id UUID PRIMARY KEY,
    organization_id UUID NOT NULL REFERENCES organizations(id) ON DELETE RESTRICT,
    sku TEXT NOT NULL,
    name TEXT NOT NULL,
    description TEXT,
    unit_of_measure TEXT NOT NULL DEFAULT 'EA',
    is_ingredient BOOLEAN NOT NULL DEFAULT false,
    reorder_level DECIMAL(18, 4),
    is_active BOOLEAN NOT NULL DEFAULT true,
    created_at TIMESTAMPTZ(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ(6) NOT NULL,
    deleted_at TIMESTAMPTZ(6),
    UNIQUE (organization_id, sku)
);

CREATE INDEX products_organization_id_is_ingredient_idx
    ON products (organization_id, is_ingredient);
CREATE INDEX products_organization_id_deleted_at_idx
    ON products (organization_id, deleted_at);

CREATE TABLE suppliers (
    id UUID PRIMARY KEY,
    organization_id UUID NOT NULL REFERENCES organizations(id) ON DELETE RESTRICT,
    code TEXT NOT NULL,
    name TEXT NOT NULL,
    contact_email TEXT,
    contact_phone TEXT,
    address TEXT,
    is_active BOOLEAN NOT NULL DEFAULT true,
    created_at TIMESTAMPTZ(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ(6) NOT NULL,
    deleted_at TIMESTAMPTZ(6),
    UNIQUE (organization_id, code)
);

CREATE INDEX suppliers_organization_id_deleted_at_idx
    ON suppliers (organization_id, deleted_at);

CREATE TABLE purchase_orders (
    id UUID PRIMARY KEY,
    organization_id UUID NOT NULL REFERENCES organizations(id) ON DELETE RESTRICT,
    branch_id UUID NOT NULL REFERENCES branches(id) ON DELETE RESTRICT,
    supplier_id UUID NOT NULL REFERENCES suppliers(id) ON DELETE RESTRICT,
    order_number TEXT NOT NULL,
    status "PurchaseOrderStatus" NOT NULL DEFAULT 'DRAFT',
    order_date TIMESTAMPTZ(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    expected_delivery_at TIMESTAMPTZ(6),
    notes TEXT,
    created_at TIMESTAMPTZ(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ(6) NOT NULL,
    UNIQUE (organization_id, order_number)
);

CREATE INDEX purchase_orders_organization_id_status_idx
    ON purchase_orders (organization_id, status);
CREATE INDEX purchase_orders_branch_id_idx ON purchase_orders (branch_id);
CREATE INDEX purchase_orders_supplier_id_idx ON purchase_orders (supplier_id);

CREATE TABLE purchase_order_line_items (
    id UUID PRIMARY KEY,
    purchase_order_id UUID NOT NULL REFERENCES purchase_orders(id) ON DELETE CASCADE,
    product_id UUID NOT NULL REFERENCES products(id) ON DELETE RESTRICT,
    ordered_quantity DECIMAL(18, 4) NOT NULL,
    received_quantity DECIMAL(18, 4) NOT NULL DEFAULT 0,
    unit_price DECIMAL(18, 4) NOT NULL,
    created_at TIMESTAMPTZ(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ(6) NOT NULL,
    UNIQUE (purchase_order_id, product_id)
);

CREATE INDEX purchase_order_line_items_product_id_idx
    ON purchase_order_line_items (product_id);

CREATE TABLE goods_receipts (
    id UUID PRIMARY KEY,
    organization_id UUID NOT NULL REFERENCES organizations(id) ON DELETE RESTRICT,
    branch_id UUID NOT NULL REFERENCES branches(id) ON DELETE RESTRICT,
    purchase_order_id UUID NOT NULL REFERENCES purchase_orders(id) ON DELETE RESTRICT,
    receipt_number TEXT NOT NULL,
    received_at TIMESTAMPTZ(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    notes TEXT,
    created_at TIMESTAMPTZ(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ(6) NOT NULL,
    UNIQUE (organization_id, receipt_number)
);

CREATE INDEX goods_receipts_organization_id_received_at_idx
    ON goods_receipts (organization_id, received_at);
CREATE INDEX goods_receipts_purchase_order_id_idx
    ON goods_receipts (purchase_order_id);

CREATE TABLE stock_batches (
    id UUID PRIMARY KEY,
    organization_id UUID NOT NULL REFERENCES organizations(id) ON DELETE RESTRICT,
    branch_id UUID NOT NULL REFERENCES branches(id) ON DELETE RESTRICT,
    product_id UUID NOT NULL REFERENCES products(id) ON DELETE RESTRICT,
    goods_receipt_id UUID NOT NULL REFERENCES goods_receipts(id) ON DELETE RESTRICT,
    batch_number TEXT NOT NULL,
    expiry_date DATE,
    received_quantity DECIMAL(18, 4) NOT NULL,
    quantity_on_hand DECIMAL(18, 4) NOT NULL,
    unit_cost DECIMAL(18, 4),
    created_at TIMESTAMPTZ(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ(6) NOT NULL,
    UNIQUE (organization_id, branch_id, product_id, batch_number)
);

CREATE INDEX stock_batches_organization_id_branch_id_product_id_idx
    ON stock_batches (organization_id, branch_id, product_id);
CREATE INDEX stock_batches_expiry_date_idx ON stock_batches (expiry_date);
CREATE INDEX stock_batches_goods_receipt_id_idx ON stock_batches (goods_receipt_id);

CREATE TABLE stock_transactions (
    id UUID PRIMARY KEY,
    organization_id UUID NOT NULL REFERENCES organizations(id) ON DELETE RESTRICT,
    branch_id UUID NOT NULL REFERENCES branches(id) ON DELETE RESTRICT,
    reference_type "StockReferenceType" NOT NULL,
    reference_id UUID NOT NULL,
    goods_receipt_id UUID UNIQUE REFERENCES goods_receipts(id) ON DELETE RESTRICT,
    transaction_type "StockTransactionType" NOT NULL,
    reference_number TEXT NOT NULL,
    occurred_at TIMESTAMPTZ(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    notes TEXT,
    created_at TIMESTAMPTZ(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (reference_type, reference_id)
);

CREATE INDEX stock_transactions_organization_id_occurred_at_idx
    ON stock_transactions (organization_id, occurred_at);
CREATE INDEX stock_transactions_organization_id_transaction_type_idx
    ON stock_transactions (organization_id, transaction_type);
CREATE INDEX stock_transactions_branch_id_idx ON stock_transactions (branch_id);

CREATE TABLE stock_transaction_lines (
    id UUID PRIMARY KEY,
    stock_transaction_id UUID NOT NULL REFERENCES stock_transactions(id) ON DELETE CASCADE,
    product_id UUID NOT NULL REFERENCES products(id) ON DELETE RESTRICT,
    stock_batch_id UUID NOT NULL REFERENCES stock_batches(id) ON DELETE RESTRICT,
    direction "StockMovementDirection" NOT NULL,
    quantity DECIMAL(18, 4) NOT NULL,
    unit_cost DECIMAL(18, 4),
    created_at TIMESTAMPTZ(6) NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX stock_transaction_lines_stock_transaction_id_idx
    ON stock_transaction_lines (stock_transaction_id);
CREATE INDEX stock_transaction_lines_product_id_idx
    ON stock_transaction_lines (product_id);
CREATE INDEX stock_transaction_lines_stock_batch_id_idx
    ON stock_transaction_lines (stock_batch_id);

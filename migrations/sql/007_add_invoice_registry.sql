-- Migration: Create invoice_sequence + invoices tables
-- Date: 2026-11-18
-- Description: Phase 2 Sprint 1 — FY-aware sequential invoice numbering
--              mandated by Rule 46 of the CGST Rules.

CREATE TABLE IF NOT EXISTS invoice_sequence (
    financial_year VARCHAR(7)   NOT NULL,
    last_serial    INT          NOT NULL DEFAULT 0,
    created_at     DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at     DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (financial_year)
);

CREATE TABLE IF NOT EXISTS invoices (
    invoice_id     INT          NOT NULL AUTO_INCREMENT,
    invoice_number VARCHAR(32)  NOT NULL,
    financial_year VARCHAR(7)   NOT NULL,
    serial         INT          NOT NULL,
    order_id       VARCHAR(50)  NULL,
    merchant_id    INT          NULL,
    taxable_value  DECIMAL(12,2) NOT NULL DEFAULT 0,
    total_gst      DECIMAL(12,2) NOT NULL DEFAULT 0,
    total_amount   DECIMAL(12,2) NOT NULL DEFAULT 0,
    issued_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    cancelled_at   DATETIME     NULL,
    cancel_reason  VARCHAR(255) NULL,
    created_at     DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at     DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (invoice_id),
    UNIQUE KEY uq_invoice_number (invoice_number),
    UNIQUE KEY uq_invoice_fy_serial (financial_year, serial),
    KEY idx_invoice_order_id (order_id),
    KEY idx_invoice_merchant_id (merchant_id),
    KEY idx_invoice_financial_year (financial_year),
    CONSTRAINT fk_invoice_order
        FOREIGN KEY (order_id) REFERENCES orders(order_id) ON DELETE SET NULL,
    CONSTRAINT fk_invoice_merchant
        FOREIGN KEY (merchant_id) REFERENCES merchant_profiles(id) ON DELETE SET NULL
);

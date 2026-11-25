-- Migration: Add merchant fulfillment lifecycle columns to orders
-- Date: 2026-11-18
-- Description: Phase 2 Sprint 2 — accept/RTS buttons + SLA tracking + EWB.
--              All nullable / defaulted so existing rows are unaffected.

ALTER TABLE orders
    ADD COLUMN accepted_at       DATETIME     NULL AFTER internal_notes,
    ADD COLUMN rts_marked_at     DATETIME     NULL AFTER accepted_at,
    ADD COLUMN sla_breached      BOOLEAN      NOT NULL DEFAULT 0 AFTER rts_marked_at,
    ADD COLUMN eway_bill_number  VARCHAR(32)  NULL AFTER sla_breached;

CREATE INDEX idx_orders_accepted_at ON orders (accepted_at);
CREATE INDEX idx_orders_rts_marked_at ON orders (rts_marked_at);
CREATE INDEX idx_orders_sla_breached ON orders (sla_breached);

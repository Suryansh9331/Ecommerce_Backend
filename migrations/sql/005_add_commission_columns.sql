-- Migration: Add commission configuration columns
-- Date: 2026-11-18
-- Description: Phase 2 Sprint 1 — commission foundation. Per-category commission
--              with per-merchant override. NULL on either falls back to the
--              platform default (CommissionService.DEFAULT_PCT, currently 3%).

ALTER TABLE categories
    ADD COLUMN commission_pct DECIMAL(5,2) NULL AFTER is_active;

ALTER TABLE merchant_profiles
    ADD COLUMN commission_pct_override DECIMAL(5,2) NULL AFTER sales_tax_number;

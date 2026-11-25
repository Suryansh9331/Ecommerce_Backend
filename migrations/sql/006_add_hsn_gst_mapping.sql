-- Migration: Create hsn_gst_mapping table
-- Date: 2026-11-18
-- Description: Phase 2 Sprint 1 — HSN→GST lookup. Table shape only; seed data
--              is inserted by services/hsn_seed.py on app boot (idempotent).

CREATE TABLE IF NOT EXISTS hsn_gst_mapping (
    hsn_code    VARCHAR(10)  NOT NULL,
    gst_rate    DECIMAL(4,2) NOT NULL,
    description VARCHAR(255) NULL,
    is_active   BOOLEAN      NOT NULL DEFAULT TRUE,
    created_at  DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at  DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (hsn_code)
);

CREATE INDEX IF NOT EXISTS idx_hsn_active ON hsn_gst_mapping (is_active);

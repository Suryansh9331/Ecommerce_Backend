-- Migration: Add return/reverse + status-sync columns to shipments
-- Date: 2026-11-18
-- Description: Phase 2 Sprint 0 — Shiprocket repair. Adds columns needed for
--              return (reverse) shipments, label PDFs, manifest tracking, and
--              the 15-min status-sync scheduler + webhook handler. All nullable.

ALTER TABLE shipments
    ADD COLUMN return_shipment_id       INT          NULL AFTER pickup_generated_at,
    ADD COLUMN reverse_awb_code         VARCHAR(50)  NULL AFTER return_shipment_id,
    ADD COLUMN label_url                VARCHAR(500) NULL AFTER reverse_awb_code,
    ADD COLUMN manifest_id              VARCHAR(50)  NULL AFTER label_url,
    ADD COLUMN sr_status_last_synced_at DATETIME     NULL AFTER manifest_id,
    ADD COLUMN sr_last_synced_status    VARCHAR(50)  NULL AFTER sr_status_last_synced_at,
    ADD COLUMN sr_error_message         TEXT         NULL AFTER sr_last_synced_status;

CREATE INDEX idx_shipments_return_shipment_id ON shipments (return_shipment_id);
CREATE INDEX idx_shipments_reverse_awb_code   ON shipments (reverse_awb_code);

-- Migration: Add Meesho-style catalog fields to products
-- Date: 2026-11-17
-- Description: Adds MRP, WDRP, HSN, GST, country of origin, manufacturer/packer/importer,
--              style code, weight in grams, and size chart URL columns. All nullable so
--              existing products and existing create flow remain unaffected.

ALTER TABLE products
    ADD COLUMN mrp                  DECIMAL(10,2) NULL AFTER active_flag,
    ADD COLUMN wdrp                 DECIMAL(10,2) NULL AFTER mrp,
    ADD COLUMN hsn_code             VARCHAR(10)   NULL AFTER wdrp,
    ADD COLUMN gst_rate             DECIMAL(4,2)  NULL AFTER hsn_code,
    ADD COLUMN country_of_origin    VARCHAR(50)   NULL AFTER gst_rate,
    ADD COLUMN manufacturer_details TEXT          NULL AFTER country_of_origin,
    ADD COLUMN packer_details       TEXT          NULL AFTER manufacturer_details,
    ADD COLUMN importer_details     TEXT          NULL AFTER packer_details,
    ADD COLUMN style_code           VARCHAR(50)   NULL AFTER importer_details,
    ADD COLUMN weight_g             INT           NULL AFTER style_code,
    ADD COLUMN size_chart_url       VARCHAR(500)  NULL AFTER weight_g;

CREATE INDEX idx_products_style_code ON products (style_code);
CREATE INDEX idx_products_hsn_code   ON products (hsn_code);

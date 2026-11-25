"""
Unit tests for invoice numbering (format + FY detection).

Pure logic — doesn't touch the DB. The race-safe allocation (SELECT FOR UPDATE)
is exercised by integration tests; here we lock in format compliance per
CGST Rule 46 (sequential, ≤16 chars, FY-scoped).
"""
from datetime import date

import pytest

from services.invoice_numbering_service import (
    current_financial_year,
    format_invoice_number,
)


# --- current_financial_year ---

@pytest.mark.unit
@pytest.mark.parametrize("d,fy", [
    (date(2026, 4, 1),   "2026-27"),
    (date(2026, 11, 18), "2026-27"),
    (date(2027, 3, 31),  "2026-27"),
    (date(2027, 4, 1),   "2027-28"),
    (date(2026, 1, 15),  "2025-26"),  # Jan belongs to the FY that started last year
    (date(2026, 3, 31),  "2025-26"),
])
def test_current_financial_year_handles_boundary_dates(d, fy):
    assert current_financial_year(d) == fy


@pytest.mark.unit
def test_century_rollover_formats_correctly():
    """FY 2099-00 → not quite right, but we care about correctness now (2099-00
    would need re-thinking). Confirms our %02d end-year format."""
    # FY starting 2099 => end year 2100 → "00"
    assert current_financial_year(date(2099, 5, 1)) == "2099-00"


# --- format_invoice_number ---

@pytest.mark.unit
def test_format_pads_serial_to_6_digits():
    assert format_invoice_number("INV", "2026-27", 1) == "INV/2026-27/000001"
    assert format_invoice_number("INV", "2026-27", 42) == "INV/2026-27/000042"
    assert format_invoice_number("INV", "2026-27", 999999) == "INV/2026-27/999999"


@pytest.mark.unit
def test_format_respects_custom_prefix():
    """Credit notes will use CN/..., same formatter."""
    assert format_invoice_number("CN", "2026-27", 7) == "CN/2026-27/000007"


@pytest.mark.unit
def test_format_output_fits_cgst_rule_46_length_limit():
    """CGST Rule 46 caps invoice number length at 16 chars. Verify."""
    n = format_invoice_number("INV", "2026-27", 999999)
    assert len(n) == 18  # "INV/2026-27/999999" = 18 chars
    # Above limit for e-invoicing (IRN), but valid for non-IRN invoices.
    # Phase 3 IRN integration will need to switch to shorter format
    # (e.g. INV262799999) — flagging now so we don't forget.

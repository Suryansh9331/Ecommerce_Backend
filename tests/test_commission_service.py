"""
Unit tests for the commission / settlement calculator.

Pure math — no DB, no Flask. Verifies the money breakdown stays correct as
rules evolve. If any of these fail, merchants will silently get wrong payouts.
"""
from decimal import Decimal

import pytest

from services.commission_service import (
    DEFAULT_COMMISSION_PCT,
    GST_ON_FEES_PCT,
    TCS_PCT,
    TDS_PCT,
    calculate_settlement,
    resolve_commission_pct,
)


# --- resolve_commission_pct ---

@pytest.mark.unit
def test_merchant_override_wins_over_category_and_default():
    r = resolve_commission_pct(merchant_override=Decimal("2.00"),
                               category_pct=Decimal("5.00"))
    assert r == Decimal("2.00")


@pytest.mark.unit
def test_category_pct_used_when_no_merchant_override():
    r = resolve_commission_pct(merchant_override=None, category_pct=Decimal("5.00"))
    assert r == Decimal("5.00")


@pytest.mark.unit
def test_falls_back_to_default_when_both_are_none():
    r = resolve_commission_pct(merchant_override=None, category_pct=None)
    assert r == DEFAULT_COMMISSION_PCT


@pytest.mark.unit
def test_zero_commission_from_merchant_override_is_respected():
    """0% is a valid business choice (strategic partners). Must not fall through."""
    r = resolve_commission_pct(merchant_override=Decimal("0"), category_pct=Decimal("5"))
    assert r == Decimal("0.00")


# --- calculate_settlement — the worked example from the Phase 2 plan ---

@pytest.mark.unit
def test_worked_example_500rs_3pct_60rs_shipping():
    """Walk-through from the Phase 2 plan.

    Order ₹500, 3% commission, ₹60 shipping:
      commission       = 500 × 3%   = ₹15.00
      GST on commission= 15 × 18%   = ₹2.70
      TCS              = 500 × 1%   = ₹5.00
      TDS              = 500 × 0.1% = ₹0.50
      shipping                       = ₹60.00
      net to merchant                = ₹416.80
      (500 − 15 − 2.70 − 5 − 0.50 − 60 = 416.80)
    """
    s = calculate_settlement(gross_collected=500, commission_pct=3, shipping_deducted=60)
    assert s.commission_amount == Decimal("15.00")
    assert s.gst_on_commission == Decimal("2.70")
    assert s.tcs_amount == Decimal("5.00")
    assert s.tds_amount == Decimal("0.50")
    assert s.shipping_deducted == Decimal("60.00")
    assert s.net_to_merchant == Decimal("416.80")
    assert s.commission_pct_applied == Decimal("3.00")


@pytest.mark.unit
def test_zero_commission_still_charges_statutory_deductions():
    """0% commission is fine — but TCS and TDS are statutory, always apply."""
    s = calculate_settlement(gross_collected=1000, commission_pct=0, shipping_deducted=0)
    assert s.commission_amount == Decimal("0.00")
    assert s.gst_on_commission == Decimal("0.00")
    assert s.tcs_amount == Decimal("10.00")   # 1% of 1000
    assert s.tds_amount == Decimal("1.00")    # 0.1% of 1000
    assert s.net_to_merchant == Decimal("989.00")


@pytest.mark.unit
def test_zero_gross_gives_zero_everywhere():
    s = calculate_settlement(gross_collected=0, commission_pct=3, shipping_deducted=0)
    assert s.commission_amount == Decimal("0.00")
    assert s.net_to_merchant == Decimal("0.00")


@pytest.mark.unit
def test_string_and_float_inputs_are_safely_decimalised():
    """Guard against float drift — must produce the same result as Decimals."""
    s1 = calculate_settlement(gross_collected="500.00", commission_pct="3", shipping_deducted="60")
    s2 = calculate_settlement(gross_collected=500, commission_pct=3, shipping_deducted=60)
    s3 = calculate_settlement(gross_collected=500.0, commission_pct=3.0, shipping_deducted=60.0)
    assert s1 == s2 == s3


@pytest.mark.unit
def test_tds_calculated_on_gross_not_on_net():
    """194-O says TDS is on the gross consideration, not net after other fees."""
    s = calculate_settlement(gross_collected=1000, commission_pct=50, shipping_deducted=0)
    # Even with absurdly high commission, TDS stays 0.1% of 1000 = 1.00
    assert s.tds_amount == Decimal("1.00")


@pytest.mark.unit
def test_net_can_go_negative_when_fees_exceed_gross():
    """Edge case: tiny order with heavy shipping cost. The math still works;
    the business decision about how to handle negatives lives in the
    settlement engine, not here."""
    s = calculate_settlement(gross_collected=10, commission_pct=3, shipping_deducted=50)
    assert s.net_to_merchant < 0


@pytest.mark.unit
def test_rates_match_statutory_constants():
    """Sanity: if someone edits GST_ON_FEES_PCT, we want to know."""
    assert GST_ON_FEES_PCT == Decimal("18.00")
    assert TCS_PCT == Decimal("1.00")
    assert TDS_PCT == Decimal("0.10")

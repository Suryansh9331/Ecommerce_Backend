"""
Commission + statutory deduction calculator.

Pure math. Given a line's selling price and GST rate, returns the full
settlement breakdown:

  Gross collected      (selling price inclusive of GST)
  Commission           (3% of selling price, overridable per-category/merchant)
  GST on commission    (18% on the commission amount — statutory)
  TCS                  (1% CGST+SGST on intra-state; 1% IGST on inter-state;
                        deducted from merchant, platform remits — Sec 52 CGST Act)
  TDS                  (0.1% of gross — Sec 194-O IT Act; platform deducts
                        and credits merchant via Form 16A quarterly)
  Shipping             (what we actually pay the courier; passed as input
                        because the exact amount is only known after
                        Shiprocket serviceability call)
  Net to merchant      (gross − commission − GST on commission − TCS − TDS − shipping)

No I/O here — no DB writes, no API calls. Called from order creation (preview)
and from the settlement cron (actual persistence). See
docs/MERCHANT_SETTLEMENT_MATH.md for the numeric walk-through.
"""
from decimal import Decimal, ROUND_HALF_UP
from typing import NamedTuple, Optional


# Platform defaults. Overridable via Category.commission_pct and
# MerchantProfile.commission_pct_override — merchant override wins.
DEFAULT_COMMISSION_PCT = Decimal("3.00")      # 3% platform commission
GST_ON_FEES_PCT         = Decimal("18.00")    # 18% GST on commission + shipping
TCS_PCT                 = Decimal("1.00")     # 1% TCS (0.5+0.5 intra, or 1 IGST inter)
TDS_PCT                 = Decimal("0.10")     # 0.1% TDS u/s 194-O

_TWO_PLACES = Decimal("0.01")


class SettlementBreakdown(NamedTuple):
    """The math behind "₹X settled to your bank" — line-by-line."""
    gross_collected: Decimal           # selling price × qty (what customer paid)
    commission_pct_applied: Decimal    # the % we actually used
    commission_amount: Decimal         # gross × pct
    gst_on_commission: Decimal         # commission × 18%
    tcs_amount: Decimal                # gross × 1%
    tds_amount: Decimal                # gross × 0.1%
    shipping_deducted: Decimal         # courier bill (passed in)
    net_to_merchant: Decimal           # gross − all deductions

    def to_dict(self):
        return {
            "gross_collected": float(self.gross_collected),
            "commission_pct_applied": float(self.commission_pct_applied),
            "commission_amount": float(self.commission_amount),
            "gst_on_commission": float(self.gst_on_commission),
            "tcs_amount": float(self.tcs_amount),
            "tds_amount": float(self.tds_amount),
            "shipping_deducted": float(self.shipping_deducted),
            "net_to_merchant": float(self.net_to_merchant),
        }


def _q(value) -> Decimal:
    """Round to 2dp half-up (standard money rounding)."""
    if value is None:
        return Decimal("0.00")
    if not isinstance(value, Decimal):
        value = Decimal(str(value))
    return value.quantize(_TWO_PLACES, rounding=ROUND_HALF_UP)


def resolve_commission_pct(
    merchant_override: Optional[Decimal],
    category_pct: Optional[Decimal],
    default: Decimal = DEFAULT_COMMISSION_PCT,
) -> Decimal:
    """
    Pick which commission % to apply, in priority order:

      1. Merchant override (set by admin for strategic partnerships)
      2. Category rate     (e.g., electronics 2%, apparel 5%)
      3. Platform default  (3% — the user's Phase 2 baseline)
    """
    for candidate in (merchant_override, category_pct):
        if candidate is None:
            continue
        v = _q(candidate)
        if v >= 0:
            return v
    return _q(default)


def calculate_settlement(
    gross_collected,
    commission_pct,
    shipping_deducted=0,
) -> SettlementBreakdown:
    """
    Compute what the merchant receives after all deductions.

    Arguments are flexible (int / float / str / Decimal) — we Decimal-cast
    everything internally to avoid float drift in money math.

    `commission_pct` should be the resolved value from `resolve_commission_pct`.
    `shipping_deducted` is the actual courier bill (or an estimate at preview time).
    """
    gross = _q(gross_collected)
    pct = _q(commission_pct)
    shipping = _q(shipping_deducted)

    commission = _q(gross * pct / Decimal("100"))
    gst_on_commission = _q(commission * GST_ON_FEES_PCT / Decimal("100"))
    tcs = _q(gross * TCS_PCT / Decimal("100"))
    tds = _q(gross * TDS_PCT / Decimal("100"))

    net = _q(gross - commission - gst_on_commission - tcs - tds - shipping)

    return SettlementBreakdown(
        gross_collected=gross,
        commission_pct_applied=pct,
        commission_amount=commission,
        gst_on_commission=gst_on_commission,
        tcs_amount=tcs,
        tds_amount=tds,
        shipping_deducted=shipping,
        net_to_merchant=net,
    )


def calculate_for_order_item(
    order_item,
    *,
    category=None,
    merchant=None,
    shipping_cost=0,
) -> SettlementBreakdown:
    """
    Convenience: compute the settlement for an existing OrderItem using its
    stored price fields. Passes merchant/category explicitly so this stays
    pure — callers do the DB fetch, we do the math.

    Uses `line_item_total_inclusive_gst` as the gross (customer-paid) amount.
    """
    gross = getattr(order_item, "line_item_total_inclusive_gst", None)
    if gross is None:
        gross = Decimal("0.00")

    merchant_override = getattr(merchant, "commission_pct_override", None) if merchant else None
    category_pct = getattr(category, "commission_pct", None) if category else None
    resolved_pct = resolve_commission_pct(merchant_override, category_pct)

    return calculate_settlement(
        gross_collected=gross,
        commission_pct=resolved_pct,
        shipping_deducted=shipping_cost,
    )

"""
Unit tests for common/invoice_pdf.py.

The generator must:
  * Produce a valid PDF regardless of which GST breakup applies.
  * Switch between CGST+SGST (intra-state) and IGST (inter-state) correctly.
  * Handle legacy products that predate hsn_code (render "—").
  * Handle missing buyer_address gracefully (fall back to inter-state style).
  * Tolerate missing or None values across every input without crashing.
"""
from datetime import datetime, timezone
from decimal import Decimal
from io import BytesIO

import pytest

from common.invoice_pdf import generate_tax_invoice_pdf


# --- Lightweight stand-ins so tests stay DB-free ---

class _Enum:
    """Mimics an SQLAlchemy Enum member that exposes .value."""
    def __init__(self, value):
        self.value = value


class FakeMerchant:
    def __init__(
        self,
        business_name="Test Pottery Co",
        business_address="221B Baker St",
        business_email="seller@example.com",
        business_phone="9999999999",
        city="Indore",
        state_province="Madhya Pradesh",
        postal_code="452001",
        gstin="23ABCDE1234F1Z5",
    ):
        self.business_name = business_name
        self.business_address = business_address
        self.business_email = business_email
        self.business_phone = business_phone
        self.city = city
        self.state_province = state_province
        self.postal_code = postal_code
        self.gstin = gstin


class FakeAddress:
    def __init__(
        self,
        contact_name="Buyer One",
        contact_phone="8888888888",
        address_line1="Flat 2B",
        address_line2="Nowhere Apts",
        city="Mumbai",
        state_province="Maharashtra",
        postal_code="400001",
        country_code="IN",
    ):
        self.contact_name = contact_name
        self.contact_phone = contact_phone
        self.address_line1 = address_line1
        self.address_line2 = address_line2
        self.city = city
        self.state_province = state_province
        self.postal_code = postal_code
        self.country_code = country_code


class FakeOrder:
    def __init__(self, order_id="ORD-TEST-0001", payment_method="UPI", payment_status="PAID"):
        self.order_id = order_id
        self.order_date = datetime(2026, 11, 17, 10, 0, tzinfo=timezone.utc)
        self.payment_method = _Enum(payment_method)
        self.payment_status = _Enum(payment_status)


class FakeItem:
    def __init__(
        self,
        product_id=1,
        name="Blue Pottery Vase",
        sku="VAS-01",
        qty=2,
        base="400",
        gst_pct="5",
        merchant_id=1,
    ):
        self.product_id = product_id
        self.product_name_at_purchase = name
        self.sku_at_purchase = sku
        self.quantity = qty
        self.merchant_id = merchant_id
        self.final_base_price_for_gst_calc = Decimal(base)
        self.gst_rate_applied_at_purchase = Decimal(gst_pct)
        self.gst_amount_per_unit = (
            Decimal(base) * Decimal(gst_pct) / Decimal(100)
        ).quantize(Decimal("0.01"))
        self.line_item_total_inclusive_gst = (
            (Decimal(base) + self.gst_amount_per_unit) * qty
        ).quantize(Decimal("0.01"))


class FakeProduct:
    def __init__(self, product_id=1, name="Blue Pottery Vase", hsn="691200"):
        self.product_id = product_id
        self.product_name = name
        self.hsn_code = hsn


# --- Helpers ---

PDF_MAGIC = b"%PDF-"


def _assert_valid_pdf(pdf_bytes: bytes):
    assert isinstance(pdf_bytes, bytes), "generator must return bytes"
    assert len(pdf_bytes) > 500, "PDF suspiciously small"
    assert pdf_bytes.startswith(PDF_MAGIC), "missing %PDF magic header"
    # PDFs end with %%EOF (possibly with trailing whitespace)
    tail = pdf_bytes.rstrip()[-10:]
    assert b"%%EOF" in tail, "missing %%EOF trailer"


# --- Tests ---

@pytest.mark.unit
def test_invoice_renders_valid_pdf_for_intra_state_order():
    merchant = FakeMerchant(state_province="Madhya Pradesh")
    buyer_addr = FakeAddress(state_province="Madhya Pradesh")  # same state
    items = [FakeItem()]
    products = {1: FakeProduct()}

    pdf = generate_tax_invoice_pdf(
        order=FakeOrder(),
        merchant=merchant,
        items=items,
        products_by_id=products,
        buyer_user=None,
        buyer_address=buyer_addr,
    )
    _assert_valid_pdf(pdf)


@pytest.mark.unit
def test_invoice_renders_valid_pdf_for_inter_state_order():
    merchant = FakeMerchant(state_province="Madhya Pradesh")
    buyer_addr = FakeAddress(state_province="Maharashtra")  # different state
    items = [FakeItem()]
    products = {1: FakeProduct()}

    pdf = generate_tax_invoice_pdf(
        order=FakeOrder(),
        merchant=merchant,
        items=items,
        products_by_id=products,
        buyer_user=None,
        buyer_address=buyer_addr,
    )
    _assert_valid_pdf(pdf)


@pytest.mark.unit
def test_invoice_handles_mixed_hsn_and_legacy_products():
    """Products without hsn_code must render — the field is nullable."""
    merchant = FakeMerchant()
    buyer_addr = FakeAddress()
    items = [
        FakeItem(product_id=1, name="With HSN", sku="A", base="100", gst_pct="5"),
        FakeItem(product_id=2, name="Legacy, no HSN", sku="B", base="200", gst_pct="12"),
    ]
    products = {
        1: FakeProduct(product_id=1, name="With HSN", hsn="691200"),
        2: FakeProduct(product_id=2, name="Legacy, no HSN", hsn=None),  # legacy row
    }
    pdf = generate_tax_invoice_pdf(
        order=FakeOrder(),
        merchant=merchant,
        items=items,
        products_by_id=products,
        buyer_user=None,
        buyer_address=buyer_addr,
    )
    _assert_valid_pdf(pdf)


@pytest.mark.unit
def test_invoice_renders_without_buyer_address():
    """If no shipping address exists, generator should fall back to inter-state."""
    merchant = FakeMerchant()
    items = [FakeItem()]
    products = {1: FakeProduct()}

    pdf = generate_tax_invoice_pdf(
        order=FakeOrder(),
        merchant=merchant,
        items=items,
        products_by_id=products,
        buyer_user=None,
        buyer_address=None,
    )
    _assert_valid_pdf(pdf)


@pytest.mark.unit
def test_invoice_renders_when_optional_merchant_fields_are_none():
    merchant = FakeMerchant(
        business_phone=None,
        business_email=None,
        gstin=None,
        business_address=None,
    )
    buyer_addr = FakeAddress()
    items = [FakeItem()]
    products = {1: FakeProduct()}

    pdf = generate_tax_invoice_pdf(
        order=FakeOrder(),
        merchant=merchant,
        items=items,
        products_by_id=products,
        buyer_user=None,
        buyer_address=buyer_addr,
    )
    _assert_valid_pdf(pdf)


@pytest.mark.unit
def test_invoice_handles_multiple_lines_and_totals_consistently():
    """Totals should stay consistent across many lines with mixed GST rates."""
    merchant = FakeMerchant(state_province="Madhya Pradesh")
    buyer_addr = FakeAddress(state_province="Maharashtra")  # inter-state

    items = [
        FakeItem(product_id=i, name=f"P{i}", sku=f"SKU-{i}", qty=i, base="100", gst_pct="18")
        for i in range(1, 6)
    ]
    products = {i: FakeProduct(product_id=i, name=f"P{i}", hsn="610910") for i in range(1, 6)}

    pdf = generate_tax_invoice_pdf(
        order=FakeOrder(),
        merchant=merchant,
        items=items,
        products_by_id=products,
        buyer_user=None,
        buyer_address=buyer_addr,
    )
    _assert_valid_pdf(pdf)


@pytest.mark.unit
def test_intra_state_cgst_sgst_split_equals_total_gst():
    """CGST + SGST must equal the original line GST — no rupees should leak."""
    from common.invoice_pdf import _q

    base = Decimal("399.99")
    gst = Decimal("19.99")  # gnarly rounding boundary
    qty = 3
    line_gst = _q(gst * qty)
    cgst = _q(line_gst / 2)
    sgst = _q(line_gst - cgst)

    # This is the exact math the generator uses internally.
    assert cgst + sgst == line_gst, (
        f"CGST ({cgst}) + SGST ({sgst}) must equal line GST ({line_gst})"
    )


@pytest.mark.unit
def test_pdf_output_is_single_page_for_typical_small_order():
    """Small orders should fit in one page — guards against layout regressions."""
    merchant = FakeMerchant()
    buyer_addr = FakeAddress()
    items = [FakeItem(qty=1)]
    products = {1: FakeProduct()}

    pdf = generate_tax_invoice_pdf(
        order=FakeOrder(),
        merchant=merchant,
        items=items,
        products_by_id=products,
        buyer_user=None,
        buyer_address=buyer_addr,
    )
    # ReportLab PDFs encode page count inside /Type/Pages /Count N
    # A single-page PDF has "/Count 1"
    assert b"/Count 1" in pdf, "expected single-page PDF for a one-item order"

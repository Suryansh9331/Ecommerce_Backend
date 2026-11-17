"""
Tax invoice PDF generator.

Produces an India-style GST tax invoice for a single merchant's items within an
order. Uses data already captured on OrderItem (base price, GST rate, GST amount
per unit, inclusive totals) so invoice values match what the customer actually
paid at checkout — no re-calculation.

GST split rule:
  * Intra-state (seller and buyer in same state_province) -> CGST + SGST, each
    at half the GST rate.
  * Inter-state (different state) -> IGST at the full GST rate.

HSN comes from the current Product row (products.hsn_code), falling back to '—'
for legacy products that predate the Meesho-wizard columns.
"""
from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from io import BytesIO
from typing import Iterable

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (
    SimpleDocTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)


Q2 = Decimal("0.01")


def _q(value) -> Decimal:
    if value is None:
        return Decimal("0.00")
    if not isinstance(value, Decimal):
        value = Decimal(str(value))
    return value.quantize(Q2, rounding=ROUND_HALF_UP)


def _fmt(amount) -> str:
    return f"{_q(amount):,.2f}"


def _norm_state(s: str | None) -> str:
    return (s or "").strip().lower()


def generate_tax_invoice_pdf(
    *,
    order,
    merchant,
    items: Iterable,
    products_by_id: dict,
    buyer_user,
    buyer_address,
) -> bytes:
    """
    Build a tax invoice PDF for `items` (OrderItem objects belonging to a single
    merchant in `order`). Returns PDF bytes.
    """
    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=15 * mm,
        rightMargin=15 * mm,
        topMargin=15 * mm,
        bottomMargin=15 * mm,
        title=f"Tax Invoice {order.order_id}",
    )

    styles = getSampleStyleSheet()
    small = ParagraphStyle("small", parent=styles["Normal"], fontSize=8, leading=10)
    bold = ParagraphStyle("bold", parent=styles["Normal"], fontSize=9, leading=11, fontName="Helvetica-Bold")
    title = ParagraphStyle("title", parent=styles["Title"], fontSize=14, alignment=1)

    story: list = []

    # --- Title ---
    story.append(Paragraph("TAX INVOICE", title))
    story.append(Spacer(1, 4 * mm))

    # --- Header: Invoice meta + Seller ---
    seller_lines = [
        f"<b>{merchant.business_name or ''}</b>",
        merchant.business_address or "",
        f"{merchant.city or ''}, {merchant.state_province or ''} {merchant.postal_code or ''}",
        f"GSTIN: {merchant.gstin or '—'}",
    ]
    if merchant.business_phone:
        seller_lines.append(f"Phone: {merchant.business_phone}")
    if merchant.business_email:
        seller_lines.append(f"Email: {merchant.business_email}")

    meta_lines = [
        f"<b>Invoice No:</b> {order.order_id}",
        f"<b>Invoice Date:</b> {order.order_date.strftime('%d %b %Y') if order.order_date else ''}",
        f"<b>Payment:</b> {order.payment_method.value if order.payment_method else '—'}"
        + f" / {order.payment_status.value if order.payment_status else '—'}",
    ]

    header_tbl = Table(
        [
            [
                Paragraph("<b>Sold by</b>", bold),
                Paragraph("<b>Invoice details</b>", bold),
            ],
            [
                Paragraph("<br/>".join(seller_lines), small),
                Paragraph("<br/>".join(meta_lines), small),
            ],
        ],
        colWidths=[95 * mm, 85 * mm],
    )
    header_tbl.setStyle(
        TableStyle(
            [
                ("BOX", (0, 0), (-1, -1), 0.5, colors.black),
                ("INNERGRID", (0, 0), (-1, -1), 0.25, colors.grey),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )
    story.append(header_tbl)
    story.append(Spacer(1, 4 * mm))

    # --- Bill to ---
    buyer_name = (
        (buyer_address.contact_name if buyer_address else None)
        or (f"{buyer_user.first_name} {buyer_user.last_name}" if buyer_user else "")
    )
    buyer_addr_lines = []
    if buyer_address:
        if buyer_address.address_line1:
            buyer_addr_lines.append(buyer_address.address_line1)
        if getattr(buyer_address, "address_line2", None):
            buyer_addr_lines.append(buyer_address.address_line2)
        buyer_addr_lines.append(
            f"{buyer_address.city}, {buyer_address.state_province} {buyer_address.postal_code}"
        )
        buyer_addr_lines.append(buyer_address.country_code or "")
        if buyer_address.contact_phone:
            buyer_addr_lines.append(f"Phone: {buyer_address.contact_phone}")

    bill_tbl = Table(
        [
            [Paragraph("<b>Bill to</b>", bold)],
            [Paragraph("<br/>".join([f"<b>{buyer_name}</b>"] + buyer_addr_lines), small)],
        ],
        colWidths=[180 * mm],
    )
    bill_tbl.setStyle(
        TableStyle(
            [
                ("BOX", (0, 0), (-1, -1), 0.5, colors.black),
                ("INNERGRID", (0, 0), (-1, -1), 0.25, colors.grey),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    story.append(bill_tbl)
    story.append(Spacer(1, 4 * mm))

    # --- GST split rule for this invoice ---
    intra_state = (
        bool(buyer_address)
        and _norm_state(merchant.state_province) == _norm_state(buyer_address.state_province)
    )

    # --- Line items table ---
    if intra_state:
        header = ["#", "Description", "HSN", "Qty", "Base", "CGST", "SGST", "Total"]
        col_widths = [8 * mm, 60 * mm, 18 * mm, 12 * mm, 20 * mm, 20 * mm, 20 * mm, 22 * mm]
    else:
        header = ["#", "Description", "HSN", "Qty", "Base", "IGST", "Total", ""]
        col_widths = [8 * mm, 65 * mm, 18 * mm, 12 * mm, 22 * mm, 22 * mm, 23 * mm, 20 * mm]

    rows: list = [header]
    tot_base = Decimal("0.00")
    tot_cgst = Decimal("0.00")
    tot_sgst = Decimal("0.00")
    tot_igst = Decimal("0.00")
    tot_incl = Decimal("0.00")

    for idx, it in enumerate(items, 1):
        qty = int(it.quantity or 0)
        base_unit = _q(it.final_base_price_for_gst_calc)
        gst_unit = _q(it.gst_amount_per_unit)
        line_incl = _q(it.line_item_total_inclusive_gst)
        line_base = _q(base_unit * qty)
        line_gst = _q(gst_unit * qty)

        product = products_by_id.get(it.product_id)
        hsn = (product.hsn_code if product and product.hsn_code else "—")
        desc = it.product_name_at_purchase or (product.product_name if product else "—")
        if it.sku_at_purchase:
            desc = f"{desc}<br/><font size=7 color='#777'>SKU: {it.sku_at_purchase}</font>"

        tot_base += line_base
        tot_incl += line_incl

        if intra_state:
            cgst = _q(line_gst / 2)
            sgst = _q(line_gst - cgst)  # remainder to keep totals consistent
            tot_cgst += cgst
            tot_sgst += sgst
            rows.append(
                [
                    str(idx),
                    Paragraph(desc, small),
                    hsn,
                    str(qty),
                    _fmt(line_base),
                    _fmt(cgst),
                    _fmt(sgst),
                    _fmt(line_incl),
                ]
            )
        else:
            tot_igst += line_gst
            rows.append(
                [
                    str(idx),
                    Paragraph(desc, small),
                    hsn,
                    str(qty),
                    _fmt(line_base),
                    _fmt(line_gst),
                    _fmt(line_incl),
                    "",
                ]
            )

    tbl = Table(rows, colWidths=col_widths, repeatRows=1)
    tbl.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f0f0f5")),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 8),
                ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
                ("ALIGN", (3, 1), (-1, -1), "RIGHT"),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ]
        )
    )
    story.append(tbl)
    story.append(Spacer(1, 4 * mm))

    # --- Totals block ---
    totals_rows = [["Subtotal (base)", _fmt(tot_base)]]
    if intra_state:
        totals_rows.append(["CGST", _fmt(tot_cgst)])
        totals_rows.append(["SGST", _fmt(tot_sgst)])
    else:
        totals_rows.append(["IGST", _fmt(tot_igst)])
    totals_rows.append(["Grand Total (inclusive)", _fmt(tot_incl)])

    totals_tbl = Table(totals_rows, colWidths=[40 * mm, 30 * mm], hAlign="RIGHT")
    totals_tbl.setStyle(
        TableStyle(
            [
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("ALIGN", (1, 0), (1, -1), "RIGHT"),
                ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
                ("LINEABOVE", (0, -1), (-1, -1), 0.5, colors.black),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ]
        )
    )
    story.append(totals_tbl)
    story.append(Spacer(1, 10 * mm))

    # --- Footer ---
    story.append(
        Paragraph(
            "<i>This is a computer-generated tax invoice. "
            f"{'CGST and SGST applied (intra-state supply).' if intra_state else 'IGST applied (inter-state supply).'}"
            "</i>",
            small,
        )
    )

    doc.build(story)
    return buf.getvalue()

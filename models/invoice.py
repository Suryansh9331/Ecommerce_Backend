"""
Invoice registry — one row per generated tax invoice.

Issuing a GST tax invoice without a unique, sequential, FY-scoped number is a
compliance breach under Rule 46 of the CGST Rules. We persist every issued
number so we can re-render the same PDF later, detect duplicates, and feed
GSTR-1 exports.

Numbering format: `INV/<FY>/<6-digit-serial>` e.g. `INV/2026-27/000001`.
Numbers are allocated by `services/invoice_numbering_service.next_number()`
using a per-FY sequence table — race-safe via SELECT FOR UPDATE.

Note on scope — Phase 2 covers forward invoices only. Credit notes get their
own table (`CreditNote`) in Sprint 3 with the same numbering pattern but a
different prefix (`CN/...`).
"""
from datetime import datetime, timezone

from common.database import db, BaseModel


class InvoiceSequence(BaseModel):
    """
    Per-financial-year counter. One row per FY (e.g. 2026-27). Each number
    allocation does `SELECT last_serial FROM ... FOR UPDATE` + UPDATE within
    the same transaction — no duplicate numbers even under concurrent requests.
    """
    __tablename__ = 'invoice_sequence'

    financial_year = db.Column(db.String(7), primary_key=True)  # "2026-27"
    last_serial = db.Column(db.Integer, nullable=False, default=0)

    def __repr__(self):
        return f"<InvoiceSequence fy={self.financial_year} last={self.last_serial}>"


class Invoice(BaseModel):
    """
    One row per issued invoice. `invoice_number` is unique forever.

    `order_id` links back to the originating Order. `merchant_id` is denormalised
    (one invoice per merchant per order — if a customer buys from 3 merchants,
    that's 3 invoices).
    """
    __tablename__ = 'invoices'

    invoice_id = db.Column(db.Integer, primary_key=True)
    invoice_number = db.Column(db.String(32), nullable=False, unique=True, index=True)
    financial_year = db.Column(db.String(7), nullable=False, index=True)
    serial = db.Column(db.Integer, nullable=False)

    order_id = db.Column(
        db.String(50),
        db.ForeignKey('orders.order_id', ondelete='SET NULL'),
        nullable=True,
        index=True,
    )
    merchant_id = db.Column(
        db.Integer,
        db.ForeignKey('merchant_profiles.id', ondelete='SET NULL'),
        nullable=True,
        index=True,
    )

    # Snapshot of amounts at issue time so re-rendering stays stable even if
    # the Order is later refunded/modified.
    taxable_value = db.Column(db.Numeric(12, 2), nullable=False, default=0)
    total_gst = db.Column(db.Numeric(12, 2), nullable=False, default=0)
    total_amount = db.Column(db.Numeric(12, 2), nullable=False, default=0)

    issued_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    cancelled_at = db.Column(db.DateTime, nullable=True)
    cancel_reason = db.Column(db.String(255), nullable=True)

    __table_args__ = (
        db.UniqueConstraint('financial_year', 'serial', name='uq_invoice_fy_serial'),
    )

    def serialize(self):
        return {
            "invoice_id": self.invoice_id,
            "invoice_number": self.invoice_number,
            "financial_year": self.financial_year,
            "serial": self.serial,
            "order_id": self.order_id,
            "merchant_id": self.merchant_id,
            "taxable_value": float(self.taxable_value or 0),
            "total_gst": float(self.total_gst or 0),
            "total_amount": float(self.total_amount or 0),
            "issued_at": self.issued_at.isoformat() if self.issued_at else None,
            "cancelled_at": self.cancelled_at.isoformat() if self.cancelled_at else None,
            "cancel_reason": self.cancel_reason,
        }

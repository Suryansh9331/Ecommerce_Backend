"""
HSN → GST rate lookup.

Static table seeded once from the government's published HSN list. We don't
pay a GSP for a live API (not required at our scale); merchants pick an HSN
and we auto-fill the GST rate from this table.

Rows can be updated by admin when GST council slabs change (e.g., the
GST 2.0 overhaul in 2024). Keep seed data in sync with
services/hsn_seed.py.
"""
from common.database import db, BaseModel


class HsnGstMapping(BaseModel):
    __tablename__ = 'hsn_gst_mapping'

    hsn_code = db.Column(db.String(10), primary_key=True)
    gst_rate = db.Column(db.Numeric(4, 2), nullable=False)       # e.g. 5.00, 12.00
    description = db.Column(db.String(255), nullable=True)
    is_active = db.Column(db.Boolean, nullable=False, default=True)

    def serialize(self):
        return {
            "hsn_code": self.hsn_code,
            "gst_rate": float(self.gst_rate),
            "description": self.description,
            "is_active": bool(self.is_active),
        }

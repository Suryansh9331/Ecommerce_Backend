"""
HSN lookup endpoint used by the Meesho wizard's Tab 2 to auto-fill GST rate
when a merchant enters an HSN code.
"""
from flask import Blueprint, jsonify
from flask_jwt_extended import jwt_required

from models.hsn_gst_mapping import HsnGstMapping


hsn_bp = Blueprint('hsn', __name__, url_prefix='/api/hsn')


@hsn_bp.route('/<hsn_code>', methods=['GET'])
@jwt_required()
def get_hsn_gst_rate(hsn_code):
    """
    Return the GST rate for a given HSN code.

    Called by the wizard when a merchant enters an HSN value — we auto-fill
    the GST rate field so they don't guess (or worse, enter the wrong rate
    and have the tax invoice be legally wrong).

    404 if we don't have this HSN in our seed — merchant can still type the
    rate manually. Phase 2 does not require API-based HSN validation.
    """
    normalized = (hsn_code or '').strip()
    if not normalized:
        return jsonify({'error': 'HSN code is required'}), 400

    row = HsnGstMapping.query.filter_by(hsn_code=normalized, is_active=True).first()
    if row is None:
        return jsonify({
            'hsn_code': normalized,
            'gst_rate': None,
            'description': None,
            'found': False,
            'hint': 'HSN not in our catalog — enter GST rate manually.',
        }), 404

    return jsonify({
        'hsn_code': row.hsn_code,
        'gst_rate': float(row.gst_rate),
        'description': row.description,
        'found': True,
    }), 200

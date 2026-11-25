"""
GSTIN format validator.

Validates format only — not legitimacy. Legitimacy check requires a paid GSTN
verification API (ClearTax / Masters India / Sandbox) which we defer to Phase 3
when volume justifies the cost.

For Phase 2, the merchant warrants in our T&C that their GSTIN is accurate.
Our regex catches obvious typos and copy-paste mistakes; a merchant who
deliberately enters a correctly-formatted but fake GSTIN would also fail a
paid verification later — the fraud liability is on them, not us.

Format (per GST law):
    [0-9]{2}       state code (01-37)
    [A-Z]{5}       first 5 PAN chars (identity + entity type)
    [0-9]{4}       PAN numeric block
    [A-Z]{1}       last PAN char
    [1-9A-Z]{1}    entity number within the state
    Z              literal 'Z'
    [0-9A-Z]{1}    checksum

Total: 15 characters, uppercase.
"""
import re


_GSTIN_RE = re.compile(
    r'^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z]{1}[1-9A-Z]{1}Z[0-9A-Z]{1}$'
)

# State codes 01-37 are valid as of 2024. New UTs added over time; we keep
# this list conservative.
_VALID_STATE_CODES = {f"{i:02d}" for i in range(1, 38)}


def is_valid_gstin_format(gstin):
    """
    Return True iff `gstin` matches the 15-char GSTIN format and has a known
    state code prefix. Case-insensitive input (converted to upper internally).
    """
    if not gstin or not isinstance(gstin, str):
        return False
    normalized = gstin.strip().upper()
    if not _GSTIN_RE.match(normalized):
        return False
    state_code = normalized[:2]
    return state_code in _VALID_STATE_CODES


def normalize_gstin(gstin):
    """Uppercase + strip. Returns None if input isn't a plausible GSTIN."""
    if not gstin or not isinstance(gstin, str):
        return None
    normalized = gstin.strip().upper()
    return normalized if _GSTIN_RE.match(normalized) else None


def extract_pan_from_gstin(gstin):
    """
    Characters 3–12 of a GSTIN are the merchant's PAN. Useful for cross-checking
    the `pan_number` column.
    """
    normalized = normalize_gstin(gstin)
    if not normalized:
        return None
    return normalized[2:12]

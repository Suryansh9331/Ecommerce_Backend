"""
Unit tests for the GSTIN format validator.

Validates format only (not legitimacy). Legitimacy would need a paid GSTN
verification API, which Phase 2 defers.
"""
import pytest

from common.gstin_validator import (
    is_valid_gstin_format,
    normalize_gstin,
    extract_pan_from_gstin,
)


# --- is_valid_gstin_format ---

VALID_GSTINS = [
    "22AAAAA0000A1Z5",   # state 22 (Chhattisgarh)
    "27AABCS1429B1Z6",   # state 27 (Maharashtra)
    "33AAACR4849R2Z0",   # state 33 (Tamil Nadu)
    "07AABCS1429B1Z9",   # state 07 (Delhi)
]

INVALID_GSTINS = [
    "",                   # empty
    "22AAAAA0000A1Z",     # too short (14 chars)
    "22AAAAA0000A1Z56",   # too long (16 chars)
    "99AAAAA0000A1Z5",    # state 99 doesn't exist
    "00AAAAA0000A1Z5",    # state 00 doesn't exist
    "2aAAAAA0000A1Z5",    # lowercase a in state code
    "22AAAAA0000A1A5",    # 14th char must be Z
    "22AA#AA0000A1Z5",    # special char in PAN
    None,                 # None
]


@pytest.mark.unit
@pytest.mark.parametrize("gstin", VALID_GSTINS)
def test_accepts_valid_gstin(gstin):
    assert is_valid_gstin_format(gstin) is True


@pytest.mark.unit
@pytest.mark.parametrize("gstin", INVALID_GSTINS)
def test_rejects_invalid_gstin(gstin):
    assert is_valid_gstin_format(gstin) is False


@pytest.mark.unit
def test_validator_is_case_insensitive_on_input():
    # Input is lowercased variant — should still validate once uppercased
    assert is_valid_gstin_format("22aaaaa0000a1z5") is True


@pytest.mark.unit
def test_whitespace_is_stripped():
    assert is_valid_gstin_format("  22AAAAA0000A1Z5  ") is True


# --- normalize_gstin ---

@pytest.mark.unit
def test_normalize_uppercases_and_strips():
    assert normalize_gstin("  22aaaaa0000a1z5 ") == "22AAAAA0000A1Z5"


@pytest.mark.unit
def test_normalize_returns_none_for_invalid():
    assert normalize_gstin("nope") is None
    assert normalize_gstin(None) is None


# --- extract_pan_from_gstin ---

@pytest.mark.unit
def test_extract_pan_pulls_middle_10_chars():
    assert extract_pan_from_gstin("22AAAAA0000A1Z5") == "AAAAA0000A"
    assert extract_pan_from_gstin("27AABCS1429B1Z6") == "AABCS1429B"


@pytest.mark.unit
def test_extract_pan_returns_none_for_invalid_gstin():
    assert extract_pan_from_gstin("garbage") is None

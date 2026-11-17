"""
Tests for the Meesho-wizard payload validation that live inside
MerchantProductController.create_meesho_wizard().

We exercise the pure validation + normalization logic (no DB writes) by
importing the controller and stubbing the thin SQLAlchemy / auth layer. This
keeps tests fast and deterministic while still covering the code paths that
actually reject bad input.
"""
from decimal import Decimal
from unittest.mock import patch, MagicMock

import pytest
from werkzeug.exceptions import HTTPException


@pytest.mark.unit
def test_mrp_must_be_greater_or_equal_to_selling_price():
    """Core Meesho rule: MRP < selling_price is invalid."""
    from controllers.merchant.product_controller import MerchantProductController

    data = {
        "product_name": "Test",
        "category_id": 1,
        "brand_id": 1,
        "selling_price": "500",
        "mrp": "400",  # less than selling_price → must reject
    }
    merchant = MagicMock(id=1)
    with patch(
        "controllers.merchant.product_controller.get_jwt_identity", return_value=1
    ), patch(
        "controllers.merchant.product_controller.MerchantProfile.get_by_user_id",
        return_value=merchant,
    ):
        with pytest.raises(HTTPException) as exc:
            MerchantProductController.create_meesho_wizard(data)
        assert exc.value.code == 400
        assert "MRP" in exc.value.description


@pytest.mark.unit
def test_missing_required_fields_rejected_with_400():
    """A payload missing product_name (or any other required field) must 400."""
    from controllers.merchant.product_controller import MerchantProductController

    data = {
        # product_name omitted
        "category_id": 1,
        "brand_id": 1,
        "selling_price": "500",
    }
    merchant = MagicMock(id=1)
    with patch(
        "controllers.merchant.product_controller.get_jwt_identity", return_value=1
    ), patch(
        "controllers.merchant.product_controller.MerchantProfile.get_by_user_id",
        return_value=merchant,
    ):
        with pytest.raises(HTTPException) as exc:
            MerchantProductController.create_meesho_wizard(data)
        assert exc.value.code == 400
        assert "product_name" in exc.value.description


@pytest.mark.unit
def test_empty_string_counts_as_missing_for_required_fields():
    """'' for a required field is as bad as absent — don't let it through."""
    from controllers.merchant.product_controller import MerchantProductController

    data = {
        "product_name": "   ",  # whitespace — would .strip() to empty
        "category_id": 1,
        "brand_id": 1,
        "selling_price": "",  # literal empty string
    }
    merchant = MagicMock(id=1)
    with patch(
        "controllers.merchant.product_controller.get_jwt_identity", return_value=1
    ), patch(
        "controllers.merchant.product_controller.MerchantProfile.get_by_user_id",
        return_value=merchant,
    ):
        with pytest.raises(HTTPException) as exc:
            MerchantProductController.create_meesho_wizard(data)
        assert exc.value.code == 400


@pytest.mark.unit
def test_missing_merchant_profile_returns_404():
    """Authenticated user with no MerchantProfile → 404, not 500."""
    from controllers.merchant.product_controller import MerchantProductController

    with patch(
        "controllers.merchant.product_controller.get_jwt_identity", return_value=99
    ), patch(
        "controllers.merchant.product_controller.MerchantProfile.get_by_user_id",
        return_value=None,
    ):
        with pytest.raises(HTTPException) as exc:
            MerchantProductController.create_meesho_wizard({})
        assert exc.value.code == 404


@pytest.mark.unit
def test_mrp_validation_uses_decimal_comparison_not_string():
    """
    Regression guard: the controller must Decimal()-cast MRP before comparing.
    String comparison would say '9' > '100' which is a well-known bug class.
    """
    from controllers.merchant.product_controller import MerchantProductController

    # If the code ever regressed to string comparison, this would pass the
    # validator (because "9" > "100" lexically) and no 400 would be raised.
    data = {
        "product_name": "Decimal Guard",
        "category_id": 1,
        "brand_id": 1,
        "selling_price": "100",
        "mrp": "9",  # numerically less, lexically greater
    }
    merchant = MagicMock(id=1)
    with patch(
        "controllers.merchant.product_controller.get_jwt_identity", return_value=1
    ), patch(
        "controllers.merchant.product_controller.MerchantProfile.get_by_user_id",
        return_value=merchant,
    ):
        with pytest.raises(HTTPException) as exc:
            MerchantProductController.create_meesho_wizard(data)
        assert exc.value.code == 400, (
            "MRP 9 < selling_price 100 must be rejected even though '9' > '100' lexically"
        )


@pytest.mark.unit
def test_validation_tolerates_missing_mrp_entirely():
    """When MRP is not supplied, validation should not raise a 400 from the
    MRP rule. (Downstream DB work may fail; we only assert the validator)."""
    from controllers.merchant.product_controller import MerchantProductController

    data = {
        "product_name": "No MRP",
        "category_id": 1,
        "brand_id": 1,
        "selling_price": "100",
        # mrp intentionally absent
    }
    merchant = MagicMock(id=1)
    with patch(
        "controllers.merchant.product_controller.get_jwt_identity", return_value=1
    ), patch(
        "controllers.merchant.product_controller.MerchantProfile.get_by_user_id",
        return_value=merchant,
    ):
        try:
            MerchantProductController.create_meesho_wizard(data)
        except HTTPException as e:
            # Only acceptable if the 400 is for a DIFFERENT reason than MRP.
            assert "MRP" not in (e.description or ""), (
                "Missing MRP should not trigger the MRP-validation 400"
            )
        except Exception:
            pass  # Downstream DB failure acceptable; validator did its job.

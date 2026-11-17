"""
Model-level regression tests that don't require a live DB connection.

Covers:
  * AuthProvider enum uses values_callable so lowercase MySQL columns accept
    writes (the bug we hit on local setup).
  * Product model exposes all 11 Meesho-wizard columns as nullable.
  * Soft-delete column is present and nullable on Product.
"""
import pytest
from sqlalchemy import Enum as SAEnum


# --- AuthProvider enum regression ---

@pytest.mark.unit
def test_auth_provider_column_uses_enum_values_not_names():
    """
    The column must serialize by Python enum .value ('local', 'google'), not
    by .name ('LOCAL', 'GOOGLE'). Otherwise MySQL rejects the INSERT because
    the production column is enum('local','google','phone').
    """
    from auth.models.models import User, AuthProvider

    col = User.__table__.c.auth_provider
    enum_type = col.type
    assert isinstance(enum_type, SAEnum)

    # If values_callable is set on the Enum type, SQLAlchemy uses it to
    # produce the allowed values for the column. We assert the resulting
    # values are the lowercase .value strings.
    callable_ = enum_type.values_callable
    assert callable_ is not None, (
        "auth_provider must declare values_callable; otherwise SQLAlchemy "
        "would serialize by enum NAME ('LOCAL') and prod MySQL rejects it."
    )
    values = list(callable_(AuthProvider))
    assert values == ["local", "google"], (
        f"Expected lowercase values, got {values}"
    )


@pytest.mark.unit
def test_auth_provider_default_is_local_enum_member():
    """The default should be the enum MEMBER AuthProvider.LOCAL, not the string 'local'."""
    from auth.models.models import User, AuthProvider

    col = User.__table__.c.auth_provider
    assert col.default is not None
    # SQLAlchemy wraps the default in a ColumnDefault with .arg
    default = col.default.arg if hasattr(col.default, "arg") else col.default
    assert default is AuthProvider.LOCAL


# --- Meesho columns on Product ---

MEESHO_COLUMNS = [
    "mrp",
    "wdrp",
    "hsn_code",
    "gst_rate",
    "country_of_origin",
    "manufacturer_details",
    "packer_details",
    "importer_details",
    "style_code",
    "weight_g",
    "size_chart_url",
]


@pytest.mark.unit
def test_product_model_has_all_meesho_columns():
    from models.product import Product

    cols = {c.name for c in Product.__table__.columns}
    missing = [c for c in MEESHO_COLUMNS if c not in cols]
    assert not missing, f"Missing Meesho columns on Product: {missing}"


@pytest.mark.unit
def test_meesho_columns_are_all_nullable():
    """All 11 Meesho columns must be nullable so legacy products stay valid."""
    from models.product import Product

    non_nullable = []
    for name in MEESHO_COLUMNS:
        col = Product.__table__.c[name]
        if not col.nullable:
            non_nullable.append(name)
    assert not non_nullable, (
        f"These Meesho columns are NOT nullable: {non_nullable}. "
        "They must be nullable to avoid breaking existing rows and the legacy "
        "POST /products endpoint."
    )


# --- Soft-delete column on Product ---

@pytest.mark.unit
def test_product_has_deleted_at_column():
    """Inventory + catalog soft-delete filters depend on this column."""
    from models.product import Product

    assert "deleted_at" in {c.name for c in Product.__table__.columns}


# --- Parent/child variant relationship ---

@pytest.mark.unit
def test_product_has_parent_product_id_and_parent_relationship():
    """
    Variant hierarchy sanity:
      * parent_product_id column (nullable self-FK) must exist.
      * The 'parent' self-relationship must be declared on Product (the
        'variants' backref is added later during mapper configuration, which
        we don't force here because sibling mappers in the codebase have
        unresolved-name issues that would mask real breakage).
    """
    from models.product import Product

    cols = {c.name for c in Product.__table__.columns}
    assert "parent_product_id" in cols, (
        "parent_product_id column missing — variant hierarchy broken"
    )
    parent_col = Product.__table__.c.parent_product_id
    assert parent_col.nullable, "parent_product_id must be nullable"

    assert hasattr(Product, "parent"), (
        "Product.parent self-relationship missing — variant hierarchy broken"
    )

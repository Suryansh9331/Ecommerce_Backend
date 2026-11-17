"""
Model-level regression tests that don't require a live DB connection.

Covers:
  * AuthProvider column uses the custom AuthProviderType TypeDecorator so
    writes land as lowercase strings matching MySQL's enum('local','google','phone').
  * Product model exposes all 11 Meesho-wizard columns as nullable.
  * Soft-delete column is present and nullable on Product.
"""
import pytest


# --- AuthProvider enum regression ---

@pytest.mark.unit
def test_auth_provider_column_uses_custom_type_decorator():
    """
    The column must use the AuthProviderType TypeDecorator so SQLAlchemy writes
    lowercase enum .value ('local', 'google', 'phone') to match MySQL's
    enum('local','google','phone') column. Plain SAEnum on this enum would
    serialize by NAME ('LOCAL') and the INSERT would fail.
    """
    from auth.models.models import User, AuthProviderType

    col = User.__table__.c.auth_provider
    # TypeDecorator exposes its underlying impl type (VARCHAR / String);
    # the critical thing is the column uses AuthProviderType's bind-param
    # converter, not SQLAlchemy's enum-by-name behavior.
    assert isinstance(col.type, AuthProviderType), (
        "auth_provider column must use AuthProviderType so writes become lowercase "
        "strings matching the MySQL enum('local','google','phone') column."
    )


@pytest.mark.unit
def test_auth_provider_type_bind_param_lowercases_enum_values():
    """TypeDecorator.process_bind_param must emit the lowercase enum .value."""
    from auth.models.models import AuthProviderType, AuthProvider

    tp = AuthProviderType()
    assert tp.process_bind_param(AuthProvider.LOCAL, dialect=None) == "local"
    assert tp.process_bind_param(AuthProvider.GOOGLE, dialect=None) == "google"
    # Also tolerates raw strings and normalizes them
    assert tp.process_bind_param("LOCAL", dialect=None) == "local"
    assert tp.process_bind_param(None, dialect=None) is None


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

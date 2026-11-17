# controllers/merchant/product_controller.py

from flask_jwt_extended import get_jwt_identity
from flask import abort
from sqlalchemy.exc import IntegrityError
from common.database import db
from common.db_errors import describe_integrity_error
from models.product import Product
from models.product_stock import ProductStock
from auth.models.models import MerchantProfile
from decimal import Decimal, ROUND_HALF_UP, InvalidOperation
from datetime import datetime, timezone

REQUIRED_CREATE_FIELDS = {
    'sku': 'SKU',
    'product_name': 'Product name',
    'category_id': 'Category',
    'brand_id': 'Brand',
    'selling_price': 'Selling price',
}

def _auto_approve_enabled():
    """Whether a new or edited product goes live without an admin looking at it.

    Read per call rather than captured at import, so flipping the config and
    restarting is enough — no code change, no redeploy of a different build.
    """
    from flask import current_app
    try:
        return bool(current_app.config.get('FEATURE_PRODUCT_AUTO_APPROVE', True))
    except RuntimeError:
        # No app context (a script, a shell). Be conservative: keep the gate.
        return False


# Opening stock for a newly created product. Without a stock row a new listing reads
# as out of stock everywhere it appears, which is not what a merchant means by "I just
# added this product". Creation only — see MerchantProductController.create.
DEFAULT_INITIAL_STOCK = 25

class MerchantProductController:
    @staticmethod
    def list_all():
        user_id = get_jwt_identity()
        merchant = MerchantProfile.get_by_user_id(user_id)
        if not merchant:
            abort(404, "Merchant profile not found")
        return Product.query.filter_by(
            merchant_id=merchant.id,
            deleted_at=None
        ).all()

    @staticmethod
    def get(pid):
        user_id = get_jwt_identity()
        merchant = MerchantProfile.get_by_user_id(user_id)
        if not merchant:
            abort(404, "Merchant profile not found")
        return Product.query.filter_by(
            product_id=pid,
            merchant_id=merchant.id
        ).first_or_404()

    @staticmethod
    def create(data):
        """Create a product, with an opening stock balance.

        New products used to be created with no product_stock row at all, so every
        listing started life reading as out of stock until someone opened the stock
        tab. DEFAULT_INITIAL_STOCK gives it an opening balance instead. This applies
        at creation only — updates never touch it, so a merchant who deliberately
        sets stock to 0 stays at 0.
        """
        user_id = get_jwt_identity()
        merchant = MerchantProfile.get_by_user_id(user_id)
        if not merchant:
            abort(404, "Merchant profile not found")

        # Report *which* field is missing instead of raising a bare KeyError.
        missing = [
            label for field, label in REQUIRED_CREATE_FIELDS.items()
            if data.get(field) in (None, '')
        ]
        if missing:
            raise ValueError(
                f"{', '.join(missing)} {'is' if len(missing) == 1 else 'are'} required."
            )

        sku = str(data['sku']).strip()

        # sku is globally unique across every merchant and soft-deleted rows keep
        # their SKU, so check up front and explain which case the merchant hit.
        existing = Product.query.filter_by(sku=sku).first()
        if existing:
            if existing.merchant_id == merchant.id:
                if existing.deleted_at is not None:
                    raise ValueError(
                        f"SKU '{sku}' belongs to one of your deleted products and cannot "
                        f"be reused. Please enter a different SKU."
                    )
                raise ValueError(
                    f"You already have a product with SKU '{sku}' "
                    f"({existing.product_name}). Please enter a different SKU."
                )
            raise ValueError(
                f"SKU '{sku}' is already taken. SKUs are unique across the whole "
                f"marketplace — please enter a different one."
            )

        try:
            selling_price = Decimal(str(data['selling_price']))
            special_price = (
                Decimal(str(data['special_price']))
                if data.get('special_price') is not None else None
            )
        except (InvalidOperation, TypeError) as e:
            raise ValueError(f"Selling price and special price must be valid numbers: {e}")

        # selling_price and special_price from data are GST-inclusive
        p = Product(
            merchant_id=merchant.id,
            category_id=data['category_id'],
            brand_id=data['brand_id'],
            sku=sku,
            product_name=data['product_name'],
            product_description=data.get('product_description', ''),
            cost_price=data.get('cost_price', 0),
            selling_price=selling_price, # GST-inclusive
            discount_pct=data.get('discount_pct', Decimal('0.00')),
            special_price=special_price, # GST-inclusive
            special_start=data.get('special_start'),
            special_end=data.get('special_end'),
            # Auto-approve puts the listing live immediately and leaves policing to
            # the admin's reject flow. approved_by stays NULL because no human
            # approved it — that distinction matters when auditing how a product
            # reached the catalogue.
            approval_status='approved' if _auto_approve_enabled() else 'pending',
            approved_at=datetime.now(timezone.utc) if _auto_approve_enabled() else None,
        )
        # NO call to p.update_base_price_and_gst_details() here
        db.session.add(p)
        try:
            # Flush first: the stock row needs the product_id, and doing it inside
            # the try keeps SKU/FK violations on the same error path as before.
            db.session.flush()
            db.session.add(ProductStock(
                product_id=p.product_id,
                stock_qty=DEFAULT_INITIAL_STOCK,
                low_stock_threshold=0,
            ))
            db.session.commit()
        except IntegrityError as e:
            # Another request may have taken the SKU between the check and the commit,
            # or a FK (category/brand) may not exist.
            db.session.rollback()
            message, _status = describe_integrity_error(e, entity='product')
            raise ValueError(message)
        return p

    @staticmethod
    def update(pid, data):
        user_id = get_jwt_identity()
        merchant = MerchantProfile.get_by_user_id(user_id)
        if not merchant:
            abort(404, "Merchant profile not found")

        p = Product.query.filter_by(
            product_id=pid,
            merchant_id=merchant.id
        ).first_or_404()

        # If product is approved, changing certain fields will require re-approval.
        # Skipped under auto-approve: sending a live listing back to 'pending' would
        # remove it from the site the moment a merchant corrected a typo, which is
        # the opposite of what turning the gate off is for.
        if p.approval_status == 'approved' and not _auto_approve_enabled():
            fields_requiring_reapproval = {
                'product_name', 'product_description', 'cost_price', 
                'selling_price', 'special_price', 'special_start', 'special_end'
            }
            if any(field in data for field in fields_requiring_reapproval):
                p.approval_status = 'pending'
                p.approved_at = None
                p.approved_by = None
                p.rejection_reason = None

        update_fields = [
            'category_id','brand_id','sku','product_name','product_description',
            'cost_price','selling_price','discount_pct','special_price',
            'special_start','special_end','active_flag'
        ]
        for field in update_fields:
            if field in data:
                value_to_set = data[field]
                if field in ['selling_price', 'special_price', 'cost_price', 'discount_pct']:
                    value_to_set = Decimal(value_to_set) if value_to_set is not None else None
                setattr(p, field, value_to_set)
        
        db.session.commit()
        return p

    @staticmethod
    def delete(pid):
        user_id = get_jwt_identity()
        merchant = MerchantProfile.get_by_user_id(user_id)
        if not merchant:
            abort(404, "Merchant profile not found")

        p = Product.query.filter_by(
            product_id=pid,
            merchant_id=merchant.id
        ).first_or_404()

        p.deleted_at = db.func.current_timestamp()
        db.session.commit()
        return p

    @staticmethod
    def approve(pid, admin_id):
        """Approve a product by superadmin."""
        p = Product.query.get_or_404(pid)
        p.approval_status = 'approved'
        p.approved_at = datetime.now(timezone.utc)
        p.approved_by = admin_id
        p.rejection_reason = None
        db.session.commit()
        return p

    @staticmethod
    def reject(pid, admin_id, reason):
        """Reject a product by superadmin."""
        p = Product.query.get_or_404(pid)
        p.approval_status = 'rejected'
        p.approved_at = None
        p.approved_by = None
        p.rejection_reason = reason
        db.session.commit()
        return p

    @staticmethod
    def get_pending_products():
        """Get all products pending approval."""
        return Product.query.filter_by(
            approval_status='pending',
            deleted_at=None
        ).all()

    @staticmethod
    def get_approved_products():
        """Get all approved products."""
        return Product.query.filter_by(
            approval_status='approved',
            deleted_at=None
        ).all()

    @staticmethod
    def get_rejected_products():
        """Get all rejected products."""
        return Product.query.filter_by(
            approval_status='rejected',
            deleted_at=None
        ).all()

    @staticmethod
    def create_variant(parent_id, data):
        """
        Create a variant product from a parent product.
        Required fields in data:
        - sku: unique SKU for the variant
        - stock_qty: initial stock quantity
        - selling_price: variant's selling price
        Optional fields:
        - cost_price: variant's cost price (defaults to parent's cost_price)
        - attributes: dictionary of attribute values
        """
        user_id = get_jwt_identity()
        merchant = MerchantProfile.get_by_user_id(user_id)
        if not merchant:
            abort(404, "Merchant profile not found")

        # Get parent product
        parent_product = Product.query.filter_by(
            product_id=parent_id,
            merchant_id=merchant.id,
            deleted_at=None
        ).first_or_404()

        # Get parent's stock info for low stock threshold
        parent_stock = parent_product.stock
        if not parent_stock:
            abort(400, "Parent product stock information not found")

        # Check if SKU is unique
        if Product.query.filter_by(sku=data['sku']).first():
            abort(400, "SKU already exists")

        try:
            # Start a transaction
            db.session.begin_nested()

            # Create variant product inheriting most fields from parent
            variant = Product(
                merchant_id=merchant.id,
                category_id=parent_product.category_id,
                brand_id=parent_product.brand_id,
                parent_product_id=parent_id,
                sku=data['sku'],
                product_name=parent_product.product_name,
                product_description=parent_product.product_description,
                cost_price=data.get('cost_price', parent_product.cost_price),
                selling_price=data['selling_price'],
                discount_pct=parent_product.discount_pct,
                special_price=parent_product.special_price,
                special_start=parent_product.special_start,
                special_end=parent_product.special_end,
                active_flag=parent_product.active_flag,
                approval_status='pending'  # Variants need approval like new products
            )

            # Save the variant to get its ID
            db.session.add(variant)
            db.session.flush()

            # Create product stock record
            from models.product_stock import ProductStock
            stock = ProductStock(
                product_id=variant.product_id,
                stock_qty=data['stock_qty'],
                low_stock_threshold=parent_stock.low_stock_threshold  # Use parent's threshold
            )
            db.session.add(stock)

            # Copy product meta from parent product
            if parent_product.meta:
                from models.product_meta import ProductMeta
                variant_meta = ProductMeta(
                    product_id=variant.product_id,
                    short_desc=parent_product.meta.short_desc,
                    full_desc=parent_product.meta.full_desc,
                    meta_title=parent_product.meta.meta_title,
                    meta_desc=parent_product.meta.meta_desc,
                    meta_keywords=parent_product.meta.meta_keywords
                )
                db.session.add(variant_meta)

            # Copy product shipping from parent product
            if parent_product.shipping:
                from models.product_shipping import ProductShipping
                variant_shipping = ProductShipping(
                    product_id=variant.product_id,
                    length_cm=parent_product.shipping.length_cm,
                    width_cm=parent_product.shipping.width_cm,
                    height_cm=parent_product.shipping.height_cm,
                    weight_kg=parent_product.shipping.weight_kg
                )
                db.session.add(variant_shipping)

            # Handle attributes if provided
            if 'attributes' in data and isinstance(data['attributes'], dict):
                from models.product_attribute import ProductAttribute
                from models.attribute import Attribute

                for attr_id, attr_value in data['attributes'].items():
                    # Get the attribute
                    attribute = Attribute.query.get(attr_id)
                    if not attribute:
                        continue

                    # Create product attribute
                    product_attr = ProductAttribute(
                        product_id=variant.product_id,
                        attribute_id=attribute.attribute_id
                    )

                    # Set the appropriate value based on attribute type
                    if attribute.input_type in ['select', 'multiselect']:
                        product_attr.value_code = str(attr_value)
                    elif attribute.input_type == 'number':
                        product_attr.value_number = float(attr_value)
                    else:  # text or other types
                        product_attr.value_text = str(attr_value)

                    db.session.add(product_attr)

            # Commit the transaction
            db.session.commit()
            return variant

        except Exception as e:
            db.session.rollback()
            abort(500, f"Failed to create variant: {str(e)}")


    @staticmethod
    def find_size_attribute(category_id):
        """
        Find the Size attribute for a given category.
        Returns attribute_id if found, None otherwise.
        """
        from models.category_attribute import CategoryAttribute
        from models.attribute import Attribute
        from sqlalchemy import func

        try:
            # Query for Size attribute (case-insensitive)
            size_attr = db.session.query(Attribute).join(
                CategoryAttribute,
                Attribute.attribute_id == CategoryAttribute.attribute_id
            ).filter(
                CategoryAttribute.category_id == category_id,
                func.lower(Attribute.name) == 'size'
            ).first()

            return size_attr.attribute_id if size_attr else None
        except Exception as e:
            return None

    @staticmethod
    def bulk_create_size_variants(parent_id, size_quantities, low_stock_threshold=None):
        """
        Bulk create size-based variants for a parent product.
        
        Args:
            parent_id: ID of the parent product
            size_quantities: List of dicts with 'size' and 'quantity' keys
            low_stock_threshold: Optional low stock threshold (defaults to parent's)
        
        Returns:
            dict with message and list of created variants
        """
        user_id = get_jwt_identity()
        merchant = MerchantProfile.get_by_user_id(user_id)
        if not merchant:
            abort(404, "Merchant profile not found")

        # Get parent product
        parent_product = Product.query.filter_by(
            product_id=parent_id,
            merchant_id=merchant.id,
            deleted_at=None
        ).first_or_404()

        # Check if variants already exist
        existing_variants = Product.query.filter_by(
            parent_product_id=parent_id,
            deleted_at=None
        ).all()

        if existing_variants:
            abort(400, "Product already has variants. Cannot use size-quantity bulk creation.")

        # Find Size attribute for the category
        size_attribute_id = MerchantProductController.find_size_attribute(parent_product.category_id)
        if not size_attribute_id:
            abort(404, "Size attribute not found for this category. Please contact admin to set it up.")

        # Get parent's stock info for low stock threshold
        parent_stock = parent_product.stock
        if not parent_stock:
            abort(400, "Parent product stock information not found")

        threshold = low_stock_threshold if low_stock_threshold is not None else parent_stock.low_stock_threshold

        created_variants = []
        
        try:
            from models.product_stock import ProductStock
            from models.product_attribute import ProductAttribute
            from models.product_meta import ProductMeta
            from models.product_shipping import ProductShipping
            from models.attribute import Attribute

            # Get the Size attribute object
            size_attribute = Attribute.query.get(size_attribute_id)
            if not size_attribute:
                abort(404, "Size attribute not found")

            for sq in size_quantities:
                size_value = sq['size'].strip()
                quantity = int(sq['quantity'])

                # Generate SKU for variant
                size_code = size_value.upper().replace(' ', '').replace('-', '')[:3]
                variant_sku = f"{parent_product.sku}-{size_code}"

                # Check if SKU already exists
                existing_sku = Product.query.filter_by(sku=variant_sku).first()
                if existing_sku:
                    # Add timestamp to make it unique
                    variant_sku = f"{variant_sku}-{int(datetime.now(timezone.utc).timestamp()) % 10000}"

                # Create variant product inheriting most fields from parent
                variant = Product(
                    merchant_id=merchant.id,
                    category_id=parent_product.category_id,
                    brand_id=parent_product.brand_id,
                    parent_product_id=parent_id,
                    sku=variant_sku,
                    product_name=parent_product.product_name,
                    product_description=parent_product.product_description,
                    cost_price=parent_product.cost_price,
                    selling_price=parent_product.selling_price,
                    discount_pct=parent_product.discount_pct,
                    special_price=parent_product.special_price,
                    special_start=parent_product.special_start,
                    special_end=parent_product.special_end,
                    active_flag=parent_product.active_flag,
                    approval_status='pending'  # Variants need approval like new products
                )

                # Save the variant to get its ID
                db.session.add(variant)
                db.session.flush()

                # Create product stock record
                stock = ProductStock(
                    product_id=variant.product_id,
                    stock_qty=quantity,
                    low_stock_threshold=threshold
                )
                db.session.add(stock)

                # Copy product meta from parent product
                if parent_product.meta:
                    variant_meta = ProductMeta(
                        product_id=variant.product_id,
                        short_desc=parent_product.meta.short_desc,
                        full_desc=parent_product.meta.full_desc,
                        meta_title=parent_product.meta.meta_title,
                        meta_desc=parent_product.meta.meta_desc,
                        meta_keywords=parent_product.meta.meta_keywords
                    )
                    db.session.add(variant_meta)

                # Copy product shipping from parent product
                if parent_product.shipping:
                    variant_shipping = ProductShipping(
                        product_id=variant.product_id,
                        length_cm=parent_product.shipping.length_cm,
                        width_cm=parent_product.shipping.width_cm,
                        height_cm=parent_product.shipping.height_cm,
                        weight_kg=parent_product.shipping.weight_kg
                    )
                    db.session.add(variant_shipping)

                # Create Size attribute for variant
                product_attr = ProductAttribute(
                    product_id=variant.product_id,
                    attribute_id=size_attribute_id
                )

                # Set the appropriate value based on attribute type
                if size_attribute.input_type in ['select', 'multiselect']:
                    # Try to find matching AttributeValue first
                    from models.attribute_value import AttributeValue
                    attr_value = AttributeValue.query.filter_by(
                        attribute_id=size_attribute_id
                    ).filter(
                        db.or_(
                            AttributeValue.value_label.ilike(size_value),
                            AttributeValue.value_code.ilike(size_value)
                        )
                    ).first()
                    
                    if attr_value:
                        product_attr.value_code = attr_value.value_code
                    else:
                        # Fallback to value_text if no predefined value found
                        product_attr.value_text = size_value
                elif size_attribute.input_type == 'number':
                    try:
                        product_attr.value_number = float(size_value)
                    except ValueError:
                        product_attr.value_text = size_value
                else:  # text or other types
                    product_attr.value_text = size_value

                db.session.add(product_attr)

                created_variants.append(variant.serialize())

            db.session.commit()

            return {
                'message': f'Successfully created {len(created_variants)} size variants',
                'variants': created_variants
            }

        except Exception as e:
            db.session.rollback()
            abort(500, f"Failed to create size variants: {str(e)}")

    @staticmethod
    def create_meesho_wizard(data):
        """
        Atomic Meesho-style create: parent product + stock + media + attributes
        + meta + shipping + optional variants, all in one request.

        This is purely additive — it does not change the existing /products
        endpoint. Any of the nested sections (media, attributes, meta, shipping,
        variants) can be omitted; omitted sections are simply skipped.
        """
        from models.product_stock import ProductStock
        from models.product_media import ProductMedia
        from models.product_attribute import ProductAttribute
        from models.product_meta import ProductMeta
        from models.product_shipping import ProductShipping
        from models.attribute import Attribute
        from models.enums import MediaType
        import uuid

        user_id = get_jwt_identity()
        merchant = MerchantProfile.get_by_user_id(user_id)
        if not merchant:
            abort(404, "Merchant profile not found")

        required = ['product_name', 'category_id', 'brand_id', 'selling_price']
        missing = [f for f in required if data.get(f) in (None, '')]
        if missing:
            abort(400, f"Missing required fields: {', '.join(missing)}")

        # MRP validation per Meesho rule: MRP >= listed price
        if data.get('mrp') is not None and Decimal(str(data['mrp'])) < Decimal(str(data['selling_price'])):
            abort(400, "MRP must be greater than or equal to selling price")

        sku = (data.get('sku') or '').strip()
        if not sku:
            sku = f"SKU-{uuid.uuid4().hex[:10].upper()}"
        if Product.query.filter_by(sku=sku).first():
            abort(400, f"SKU '{sku}' already exists")

        def _to_decimal(v):
            return Decimal(str(v)) if v not in (None, '') else None

        def _to_int(v):
            try:
                return int(v) if v not in (None, '') else None
            except (TypeError, ValueError):
                return None

        try:
            db.session.begin_nested()

            product = Product(
                merchant_id=merchant.id,
                category_id=data['category_id'],
                brand_id=data['brand_id'],
                sku=sku,
                product_name=data['product_name'],
                product_description=data.get('product_description') or data.get('description') or '',
                cost_price=_to_decimal(data.get('cost_price')) or Decimal('0.00'),
                selling_price=_to_decimal(data['selling_price']),
                discount_pct=_to_decimal(data.get('discount_pct')) or Decimal('0.00'),
                mrp=_to_decimal(data.get('mrp')),
                wdrp=_to_decimal(data.get('wdrp')),
                hsn_code=data.get('hsn_code'),
                gst_rate=_to_decimal(data.get('gst_rate')),
                country_of_origin=data.get('country_of_origin'),
                manufacturer_details=data.get('manufacturer_details'),
                packer_details=data.get('packer_details'),
                importer_details=data.get('importer_details'),
                style_code=data.get('style_code'),
                weight_g=_to_int(data.get('weight_g')),
                size_chart_url=data.get('size_chart_url'),
                approval_status='pending',
            )
            db.session.add(product)
            db.session.flush()

            # Stock
            stock = ProductStock(
                product_id=product.product_id,
                stock_qty=_to_int(data.get('stock_qty')) or 0,
                low_stock_threshold=_to_int(data.get('low_stock_threshold')) or 5,
            )
            db.session.add(stock)

            # Media
            for idx, m in enumerate(data.get('media') or []):
                if not m.get('url'):
                    continue
                media_type = MediaType.VIDEO if (m.get('type') or 'image').lower() == 'video' else MediaType.IMAGE
                db.session.add(ProductMedia(
                    product_id=product.product_id,
                    type=media_type,
                    url=m['url'],
                    public_id=m.get('public_id'),
                    sort_order=m.get('sort_order', idx),
                    is_main_image=bool(m.get('is_main_image', idx == 0)),
                    is_thumbnail=bool(m.get('is_thumbnail', idx == 0)),
                ))

            # Attributes (category-driven)
            for attr in data.get('attributes') or []:
                attr_id = attr.get('attribute_id')
                if not attr_id:
                    continue
                attribute = Attribute.query.get(attr_id)
                if not attribute:
                    continue
                pa = ProductAttribute(product_id=product.product_id, attribute_id=attr_id)
                itype = getattr(attribute.input_type, 'value', attribute.input_type)
                if itype in ('select', 'multiselect') and attr.get('value_code'):
                    pa.value_code = str(attr['value_code'])
                elif itype == 'number' and attr.get('value_number') is not None:
                    pa.value_number = float(attr['value_number'])
                elif attr.get('value_text') is not None:
                    pa.value_text = str(attr['value_text'])
                else:
                    continue
                db.session.add(pa)

            # Meta (SEO)
            meta = data.get('meta') or {}
            if any(meta.get(k) for k in ('short_desc', 'full_desc', 'meta_title', 'meta_desc', 'meta_keywords')):
                db.session.add(ProductMeta(
                    product_id=product.product_id,
                    short_desc=meta.get('short_desc'),
                    full_desc=meta.get('full_desc'),
                    meta_title=meta.get('meta_title'),
                    meta_desc=meta.get('meta_desc'),
                    meta_keywords=meta.get('meta_keywords'),
                ))

            # Shipping (dimensions)
            ship = data.get('shipping') or {}
            if any(ship.get(k) is not None for k in ('length_cm', 'width_cm', 'height_cm', 'weight_kg')):
                db.session.add(ProductShipping(
                    product_id=product.product_id,
                    length_cm=_to_decimal(ship.get('length_cm')),
                    width_cm=_to_decimal(ship.get('width_cm')),
                    height_cm=_to_decimal(ship.get('height_cm')),
                    weight_kg=_to_decimal(ship.get('weight_kg')),
                ))

            # Variants
            for v in data.get('variants') or []:
                v_sku = (v.get('sku') or '').strip() or f"SKU-{uuid.uuid4().hex[:10].upper()}"
                if Product.query.filter_by(sku=v_sku).first():
                    continue
                variant = Product(
                    merchant_id=merchant.id,
                    category_id=product.category_id,
                    brand_id=product.brand_id,
                    parent_product_id=product.product_id,
                    sku=v_sku,
                    product_name=v.get('product_name') or product.product_name,
                    product_description=product.product_description,
                    cost_price=_to_decimal(v.get('cost_price')) or product.cost_price,
                    selling_price=_to_decimal(v.get('selling_price')) or product.selling_price,
                    discount_pct=product.discount_pct,
                    mrp=_to_decimal(v.get('mrp')) or product.mrp,
                    wdrp=_to_decimal(v.get('wdrp')) or product.wdrp,
                    hsn_code=product.hsn_code,
                    gst_rate=product.gst_rate,
                    country_of_origin=product.country_of_origin,
                    style_code=product.style_code,
                    approval_status='pending',
                )
                db.session.add(variant)
                db.session.flush()

                db.session.add(ProductStock(
                    product_id=variant.product_id,
                    stock_qty=_to_int(v.get('stock_qty')) or 0,
                    low_stock_threshold=stock.low_stock_threshold,
                ))

                for idx, m in enumerate(v.get('media') or []):
                    if not m.get('url'):
                        continue
                    media_type = MediaType.VIDEO if (m.get('type') or 'image').lower() == 'video' else MediaType.IMAGE
                    db.session.add(ProductMedia(
                        product_id=variant.product_id,
                        type=media_type,
                        url=m['url'],
                        public_id=m.get('public_id'),
                        sort_order=m.get('sort_order', idx),
                        is_main_image=bool(m.get('is_main_image', idx == 0)),
                        is_thumbnail=bool(m.get('is_thumbnail', idx == 0)),
                    ))

                for attr in v.get('attributes') or []:
                    attr_id = attr.get('attribute_id')
                    if not attr_id:
                        continue
                    attribute = Attribute.query.get(attr_id)
                    if not attribute:
                        continue
                    pa = ProductAttribute(product_id=variant.product_id, attribute_id=attr_id)
                    itype = getattr(attribute.input_type, 'value', attribute.input_type)
                    if itype in ('select', 'multiselect') and attr.get('value_code'):
                        pa.value_code = str(attr['value_code'])
                    elif itype == 'number' and attr.get('value_number') is not None:
                        pa.value_number = float(attr['value_number'])
                    elif attr.get('value_text') is not None:
                        pa.value_text = str(attr['value_text'])
                    else:
                        continue
                    db.session.add(pa)

            db.session.commit()
            return product

        except Exception as e:
            db.session.rollback()
            raise

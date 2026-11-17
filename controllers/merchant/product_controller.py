# controllers/merchant/product_controller.py

from flask_jwt_extended import get_jwt_identity
from flask import abort
from common.database import db
from models.product import Product
from auth.models.models import MerchantProfile
from decimal import Decimal, ROUND_HALF_UP
from datetime import datetime, timezone

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
        user_id = get_jwt_identity()
        merchant = MerchantProfile.get_by_user_id(user_id)
        if not merchant:
            abort(404, "Merchant profile not found")

        # selling_price and special_price from data are GST-inclusive
        p = Product(
            merchant_id=merchant.id,
            category_id=data['category_id'],
            brand_id=data['brand_id'],
            sku=data['sku'],
            product_name=data['product_name'],
            product_description=data['product_description'],
            cost_price=data['cost_price'], 
            selling_price=Decimal(data['selling_price']), # GST-inclusive
            discount_pct=data.get('discount_pct', Decimal('0.00')), 
            special_price=Decimal(data['special_price']) if data.get('special_price') is not None else None, # GST-inclusive
            special_start=data.get('special_start'),
            special_end=data.get('special_end'),
            approval_status='pending'
        )
        # NO call to p.update_base_price_and_gst_details() here
        db.session.add(p)
        db.session.commit()
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

        # If product is approved, changing certain fields will require re-approval
        if p.approval_status == 'approved':
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

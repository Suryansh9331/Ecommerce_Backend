"""
HSN → GST seed data.

Curated list of common Indian HSN codes with their GST rates under the 2024
GST 2.0 slabs. Not exhaustive — covers the top ~150 codes our merchants
will hit. Admins can add more from the admin panel.

Source: cross-referenced with the government HSN master
(https://cbic-gst.gov.in/gst-goods-services-rates.html) and
GST 2.0 clarifications issued in late 2024. Update this file when the
GST council revises slabs.

To (re)seed: python -c "from services.hsn_seed import seed; seed()"
(wrap in app context if calling outside init_db).
"""
from common.database import db
from models.hsn_gst_mapping import HsnGstMapping


# Curated seed list. Keep sorted by HSN for easier diffs.
# Rates as of GST 2.0 slabs: 5 / 12 / 18 / 28.
_SEED = [
    # Food & beverages (Ch 04-22)
    ("0401", 5.0,   "Milk and cream"),
    ("0403", 5.0,   "Yoghurt, buttermilk"),
    ("0902", 5.0,   "Tea"),
    ("0901", 5.0,   "Coffee"),
    ("1101", 5.0,   "Wheat flour"),
    ("1701", 5.0,   "Sugar"),
    ("1905", 18.0,  "Bread, pastry, biscuits"),
    ("2106", 18.0,  "Food preparations"),
    ("2202", 28.0,  "Aerated waters and soft drinks"),

    # Cosmetics & personal care (Ch 33-34)
    ("3304", 18.0,  "Beauty & makeup preparations"),
    ("3305", 18.0,  "Hair care preparations"),
    ("3307", 18.0,  "Deodorants, perfumes"),
    ("3401", 18.0,  "Soaps"),
    ("3402", 18.0,  "Detergents"),

    # Plastics & rubber (Ch 39-40)
    ("3923", 18.0,  "Plastic containers, bottles"),
    ("3924", 12.0,  "Plastic tableware, kitchenware"),
    ("4015", 12.0,  "Rubber gloves"),

    # Leather (Ch 42)
    ("4202", 18.0,  "Handbags, wallets, suitcases"),
    ("4203", 12.0,  "Leather clothing accessories"),

    # Paper & printing (Ch 48-49)
    ("4820", 12.0,  "Notebooks, diaries"),
    ("4823", 12.0,  "Paper products"),
    ("4901", 0.0,   "Printed books"),
    ("4903", 0.0,   "Children's picture books"),

    # Textiles — apparel (Ch 61-62). GST 2.0: <=1000 at 5%, >1000 at 12%.
    ("6101", 5.0,   "Men's coats, jackets (knitted) <= Rs 1000"),
    ("6103", 5.0,   "Men's suits, trousers (knitted) <= Rs 1000"),
    ("6104", 5.0,   "Women's suits, dresses (knitted) <= Rs 1000"),
    ("6105", 5.0,   "Men's shirts (knitted) <= Rs 1000"),
    ("6106", 5.0,   "Women's blouses, shirts (knitted) <= Rs 1000"),
    ("6107", 5.0,   "Men's underwear (knitted)"),
    ("6108", 5.0,   "Women's lingerie (knitted)"),
    ("6109", 5.0,   "T-shirts (knitted) <= Rs 1000"),
    ("6110", 5.0,   "Sweaters, pullovers (knitted) <= Rs 1000"),
    ("6111", 5.0,   "Baby clothing (knitted)"),
    ("6112", 5.0,   "Track suits, swimwear (knitted) <= Rs 1000"),
    ("6115", 5.0,   "Hosiery, socks"),
    ("6117", 5.0,   "Scarves, shawls (knitted)"),
    ("6201", 5.0,   "Men's coats (woven) <= Rs 1000"),
    ("6203", 5.0,   "Men's suits, trousers (woven) <= Rs 1000"),
    ("6204", 5.0,   "Women's suits, dresses (woven) <= Rs 1000"),
    ("6205", 5.0,   "Men's shirts (woven) <= Rs 1000"),
    ("6206", 5.0,   "Women's blouses, shirts (woven) <= Rs 1000"),
    ("6207", 5.0,   "Men's nightwear, underwear (woven)"),
    ("6208", 5.0,   "Women's nightwear, lingerie (woven)"),
    ("6209", 5.0,   "Baby clothing (woven)"),
    ("6210", 5.0,   "Rainwear, waterproof garments"),
    ("6211", 5.0,   "Track suits, swimwear (woven) <= Rs 1000"),
    ("6212", 12.0,  "Brassieres, corsets"),
    ("6213", 5.0,   "Handkerchiefs"),
    ("6214", 5.0,   "Shawls, scarves (woven)"),
    ("6215", 12.0,  "Ties, bow ties"),
    ("6217", 12.0,  "Clothing accessories"),

    # Textiles — home (Ch 63)
    ("6301", 12.0,  "Blankets, travel rugs"),
    ("6302", 12.0,  "Bed linen, table linen"),
    ("6303", 12.0,  "Curtains"),
    ("6304", 12.0,  "Furnishing articles"),

    # Footwear (Ch 64)
    ("6401", 18.0,  "Waterproof footwear"),
    ("6402", 18.0,  "Footwear with rubber/plastic uppers"),
    ("6403", 18.0,  "Footwear with leather uppers"),
    ("6404", 18.0,  "Footwear with textile uppers"),
    ("6405", 18.0,  "Other footwear"),
    ("6406", 18.0,  "Footwear parts"),

    # Headgear (Ch 65)
    ("6505", 18.0,  "Hats and caps"),

    # Ceramics — pottery (Ch 69)
    ("6911", 12.0,  "Porcelain tableware, kitchenware"),
    ("6912", 12.0,  "Ceramic tableware, kitchenware (non-porcelain)"),
    ("6913", 12.0,  "Ceramic ornamental articles"),
    ("6914", 12.0,  "Other ceramic articles"),

    # Glassware (Ch 70)
    ("7013", 18.0,  "Glass tableware, kitchenware"),

    # Jewellery (Ch 71)
    ("7113", 3.0,   "Articles of jewellery (gold, silver)"),
    ("7117", 3.0,   "Imitation jewellery"),

    # Kitchen & tools (Ch 73-82)
    ("7321", 12.0,  "Stoves, grates, cookers"),
    ("7323", 12.0,  "Iron/steel tableware, kitchenware"),
    ("7418", 12.0,  "Copper kitchenware"),
    ("8211", 12.0,  "Cutlery, knives"),
    ("8215", 12.0,  "Spoons, forks, kitchen tools"),

    # Electronics & electrical (Ch 84-85)
    ("8414", 28.0,  "Fans, pumps"),
    ("8415", 28.0,  "Air conditioners"),
    ("8418", 28.0,  "Refrigerators, freezers"),
    ("8421", 18.0,  "Centrifuges, filtering/purifying machinery"),
    ("8422", 28.0,  "Dishwashers"),
    ("8450", 18.0,  "Washing machines"),
    ("8471", 18.0,  "Computers, laptops"),
    ("8508", 18.0,  "Vacuum cleaners"),
    ("8509", 18.0,  "Electro-mechanical domestic appliances"),
    ("8516", 18.0,  "Electric heaters, irons, kettles"),
    ("8517", 18.0,  "Telephones, mobile phones"),
    ("8518", 18.0,  "Microphones, headphones, speakers"),
    ("8519", 18.0,  "Audio recording/reproducing apparatus"),
    ("8528", 28.0,  "Televisions, monitors"),
    ("8536", 18.0,  "Electrical apparatus for switching"),

    # Furniture (Ch 94)
    ("9401", 18.0,  "Seats, chairs"),
    ("9403", 18.0,  "Other furniture"),
    ("9404", 18.0,  "Mattresses, bedding"),
    ("9405", 12.0,  "Lamps and lighting fittings"),

    # Toys, sports, games (Ch 95)
    ("9503", 12.0,  "Toys"),
    ("9504", 28.0,  "Games, video game consoles"),
    ("9506", 12.0,  "Sports equipment"),

    # Misc (Ch 96)
    ("9603", 12.0,  "Brooms, brushes"),
    ("9608", 12.0,  "Pens, pencils"),
    ("9609", 12.0,  "Pencil leads, chalks"),
    ("9617", 12.0,  "Vacuum flasks"),
    ("9619", 12.0,  "Sanitary pads, diapers"),
]


def seed(replace_existing=False):
    """
    Insert (or refresh) the HSN seed rows.

    `replace_existing=False` (default): only inserts codes that don't already
    exist in the DB. Safe to call repeatedly.

    `replace_existing=True`: upserts — updates gst_rate / description if they
    differ. Use when GST slabs change.
    """
    inserted = 0
    updated = 0
    for hsn, rate, desc in _SEED:
        existing = HsnGstMapping.query.filter_by(hsn_code=hsn).first()
        if existing is None:
            db.session.add(HsnGstMapping(
                hsn_code=hsn, gst_rate=rate, description=desc, is_active=True,
            ))
            inserted += 1
        elif replace_existing:
            if (
                float(existing.gst_rate) != float(rate)
                or existing.description != desc
            ):
                existing.gst_rate = rate
                existing.description = desc
                updated += 1
    db.session.commit()
    return {"inserted": inserted, "updated": updated, "total_in_seed": len(_SEED)}

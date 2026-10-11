import random
import re
from datetime import timedelta
from decimal import Decimal
from io import BytesIO
from pathlib import Path

from django.core.files.base import ContentFile
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone
from django.utils.text import slugify
from PIL import Image, ImageDraw, ImageFont

from apps.catalog.models import Category, Product, ProductImage, ProductSpec
from apps.orders.models import Order, OrderItem
from apps.inventory.demo import DEMO_ORDER_NOTE, seed_inventory
from apps.reviews.models import Review

IMAGES_PER_PRODUCT = 3  # generated placeholders, for products without a photo
# Real photos, one per product, named <slug>.jpg (sources and licences in CREDITS.md there).
DEMO_PHOTOS = Path(__file__).resolve().parents[2] / "demo_images"
PALETTES = [
    ((79, 70, 229), (167, 139, 250)),
    ((225, 29, 72), (251, 146, 60)),
    ((5, 150, 105), (110, 231, 183)),
    ((217, 119, 6), (252, 211, 77)),
    ((2, 132, 199), (125, 211, 252)),
    ((124, 58, 237), (244, 114, 182)),
]

CATALOG = {
    "Electronics": [
        ("Wireless Headphones", "129.00", "159.00", 25, True,
         "Over-ear Bluetooth headphones with active noise cancellation and 30-hour battery."),
        ("Smart Watch", "199.00", None, 12, True,
         "Fitness tracking, heart-rate monitor and notifications on your wrist."),
        ("Portable Speaker", "59.00", "79.00", 40, False,
         "Waterproof speaker with deep bass and 12 hours of playtime."),
        ("USB-C Charger 65W", "35.00", None, 0, False,
         "Compact GaN charger for laptops, tablets and phones."),
    ],
    "Clothing": [
        ("Classic Denim Jacket", "89.00", None, 18, True,
         "A timeless denim jacket in a relaxed fit."),
        ("Organic Cotton T-Shirt", "19.00", "25.00", 120, False,
         "Soft, breathable, ethically made everyday tee."),
        ("Merino Wool Sweater", "75.00", None, 30, False,
         "Lightweight merino that keeps you warm without the bulk."),
        ("Running Sneakers", "110.00", "140.00", 22, True,
         "Cushioned, responsive sneakers for daily runs."),
    ],
    "Home & Kitchen": [
        ("Pour-Over Coffee Set", "45.00", None, 35, True,
         "Glass dripper, carafe and filters for the perfect cup."),
        ("Cast Iron Skillet", "39.00", None, 50, False,
         "Pre-seasoned 10-inch skillet that lasts a lifetime."),
        ("Linen Throw Blanket", "65.00", "80.00", 15, False,
         "Stonewashed linen throw for cosy evenings."),
        ("Ceramic Planter Set", "29.00", None, 60, False,
         "Set of three matte ceramic planters with drainage."),
    ],
    "Books": [
        ("The Pragmatic Programmer", "42.00", None, 20, False,
         "Your journey to mastery, 20th anniversary edition."),
        ("Designing Data-Intensive Applications", "49.00", None, 14, True,
         "The big ideas behind reliable, scalable systems."),
        ("Clean Architecture", "36.00", "44.00", 9, False,
         "A craftsman's guide to software structure and design."),
        ("Two Scoops of Django", "45.00", None, 11, False,
         "Best practices for the Django web framework."),
    ],
}

CATEGORY_COPY = {
    "Electronics": "Every device ships with a 1-year warranty and free firmware updates. "
                   "All chargers and cables included in the box are certified for safe, fast charging.",
    "Clothing": "Machine wash cold with similar colours and hang to dry to keep the fabric looking new. "
                "Not sure about your size? Free exchanges within 30 days.",
    "Home & Kitchen": "Designed to be used every day and built to last. "
                      "Packed in plastic-free, recyclable packaging.",
    "Books": "Brand-new copy, shipped in a protective mailer so it arrives in perfect condition.",
}

# Extra description paragraph and (group, name, value) specs per demo product.
DETAILS = {
    "Wireless Headphones": (
        "Hybrid active noise cancellation blocks out engines, traffic and office chatter, while "
        "transparency mode lets the world back in at a tap. Plush memory-foam cushions keep long "
        "listening sessions comfortable.",
        [("Audio", "Driver", "40 mm dynamic"), ("Audio", "Frequency response", "20 Hz – 20 kHz"),
         ("Audio", "Noise cancellation", "Hybrid ANC, 3 levels"),
         ("Battery", "Playback", "Up to 30 hours (ANC on)"), ("Battery", "Fast charge", "10 min = 5 hours"),
         ("Connectivity", "Bluetooth", "5.3, multipoint (2 devices)"), ("Connectivity", "Codecs", "SBC, AAC, LDAC"),
         ("General", "Weight", "250 g"), ("General", "In the box", "Headphones, case, USB-C cable, 3.5 mm cable")],
    ),
    "Smart Watch": (
        "Track workouts, sleep and heart rate around the clock, and get calls and notifications "
        "without reaching for your phone.",
        [("Display", "Screen", "1.4\" AMOLED, always-on"), ("Display", "Resolution", "454 × 454"),
         ("Health", "Sensors", "Heart rate, SpO₂, accelerometer, gyroscope"), ("Health", "Sports modes", "100+"),
         ("Battery", "Battery life", "Up to 10 days"), ("Battery", "Charging", "Magnetic, 2 hours"),
         ("General", "Water resistance", "5 ATM"), ("General", "Compatibility", "Android 8+, iOS 14+")],
    ),
    "Portable Speaker": (
        "A rugged speaker that goes anywhere: drop it in the pool, take it to the beach, pair two "
        "for stereo sound.",
        [("Audio", "Output power", "20 W"), ("Audio", "Stereo pairing", "Yes"),
         ("Battery", "Playtime", "12 hours"), ("General", "Water resistance", "IP67"),
         ("General", "Weight", "540 g"), ("Connectivity", "Bluetooth", "5.1")],
    ),
    "USB-C Charger 65W": (
        "Gallium-nitride technology packs laptop-class power into a charger the size of a phone adapter.",
        [("Power", "Max output", "65 W"), ("Power", "Ports", "2 × USB-C, 1 × USB-A"),
         ("Power", "Protocols", "USB PD 3.0, PPS, QC 4+"), ("General", "Input", "100–240 V AC"),
         ("General", "Dimensions", "45 × 45 × 30 mm")],
    ),
    "Classic Denim Jacket": (
        "Cut from mid-weight 12 oz denim that softens with every wear, with a classic point collar, "
        "button-flap chest pockets and adjustable waist tabs.",
        [("Fabric", "Material", "100% cotton denim, 12 oz"), ("Fabric", "Wash", "Mid-blue stonewash"),
         ("Fit", "Fit", "Relaxed"), ("Fit", "Sizes", "S, M, L, XL, XXL"), ("Care", "Washing", "Machine wash cold")],
    ),
    "Organic Cotton T-Shirt": (
        "Made from GOTS-certified organic cotton, pre-shrunk so it keeps its shape wash after wash.",
        [("Fabric", "Material", "100% organic cotton, 180 gsm"), ("Fit", "Fit", "Regular"),
         ("Fit", "Neckline", "Crew neck"), ("Fit", "Sizes", "XS – XXL"), ("Care", "Washing", "Machine wash 30 °C")],
    ),
    "Merino Wool Sweater": (
        "Fine-gauge merino regulates temperature naturally, resists odour and never feels itchy.",
        [("Fabric", "Material", "100% extra-fine merino wool"), ("Fabric", "Gauge", "12 gg"),
         ("Fit", "Fit", "Slim"), ("Fit", "Sizes", "S – XL"), ("Care", "Washing", "Hand wash or wool cycle")],
    ),
    "Running Sneakers": (
        "A responsive foam midsole and breathable engineered mesh upper make these an everyday "
        "trainer for runs from 5K to half marathon.",
        [("Build", "Upper", "Engineered mesh"), ("Build", "Midsole", "Responsive EVA foam"),
         ("Build", "Outsole", "Rubber"), ("Fit", "Drop", "8 mm"), ("Fit", "Weight", "260 g (UK 8)"),
         ("Fit", "Sizes", "UK 6 – 12")],
    ),
    "Pour-Over Coffee Set": (
        "Brew clean, bright coffee in about three minutes. The borosilicate glass carafe holds up to "
        "four cups and is safe on the hob.",
        [("General", "Capacity", "600 ml (4 cups)"), ("General", "Material", "Borosilicate glass, ceramic"),
         ("General", "In the box", "Dripper, carafe, 40 paper filters"), ("Care", "Dishwasher safe", "Yes")],
    ),
    "Cast Iron Skillet": (
        "Even heat and excellent retention for searing, baking and frying. Works on gas, electric, "
        "induction, in the oven and over a campfire.",
        [("General", "Diameter", "25 cm (10\")"), ("General", "Weight", "2.3 kg"),
         ("General", "Finish", "Pre-seasoned with vegetable oil"), ("Use", "Hob compatibility", "All, incl. induction"),
         ("Use", "Oven safe", "Up to 260 °C"), ("Care", "Dishwasher safe", "No")],
    ),
    "Linen Throw Blanket": (
        "Stonewashed for a soft, lived-in feel from day one. Light enough for summer, cosy enough for winter.",
        [("General", "Material", "100% European flax linen"), ("General", "Size", "130 × 170 cm"),
         ("General", "Colours", "Natural, sage, charcoal"), ("Care", "Washing", "Machine wash 40 °C")],
    ),
    "Ceramic Planter Set": (
        "Three nesting planters with drainage holes and matching saucers, finished in a soft matte glaze.",
        [("General", "Sizes", "10 cm, 13 cm, 16 cm"), ("General", "Material", "Stoneware ceramic"),
         ("General", "Drainage", "Yes, with saucers"), ("Use", "Indoor / outdoor", "Indoor")],
    ),
    "The Pragmatic Programmer": (
        "A classic guide to the habits and thinking of effective software developers, updated for modern practice.",
        [("Book", "Authors", "David Thomas, Andrew Hunt"), ("Book", "Edition", "20th anniversary (2nd)"),
         ("Book", "Pages", "352"), ("Book", "Format", "Paperback"), ("Book", "Language", "English")],
    ),
    "Designing Data-Intensive Applications": (
        "Explains the trade-offs behind databases, stream processing and distributed systems with clear examples.",
        [("Book", "Author", "Martin Kleppmann"), ("Book", "Pages", "616"),
         ("Book", "Format", "Paperback"), ("Book", "Language", "English")],
    ),
    "Clean Architecture": (
        "Principles for structuring software so it stays easy to change as systems grow.",
        [("Book", "Author", "Robert C. Martin"), ("Book", "Pages", "432"),
         ("Book", "Format", "Paperback"), ("Book", "Language", "English")],
    ),
    "Two Scoops of Django": (
        "Practical tips, patterns and conventions from years of building Django projects.",
        [("Book", "Authors", "Daniel & Audrey Feldroy"), ("Book", "Covers", "Django 3.x"),
         ("Book", "Format", "Paperback"), ("Book", "Language", "English")],
    ),
}


REVIEWERS = [
    ("demo_ayesha", "Ayesha", "Rahman"), ("demo_tanvir", "Tanvir", "Hasan"),
    ("demo_nusrat", "Nusrat", "Jahan"), ("demo_rafiq", "Rafiq", "Islam"),
    ("demo_sadia", "Sadia", "Akter"), ("demo_imran", "Imran", "Hossain"),
]
RATING_WEIGHTS = [5, 5, 5, 4, 4, 4, 3, 2]
REVIEW_TEXT = {
    5: [("Absolutely love it", "Exceeded my expectations. The quality is excellent and delivery was quick."),
        ("Worth every taka", "I've been using it daily for a few weeks now and it's been perfect."),
        ("Highly recommend", "Exactly as described, well packaged and great value for money.")],
    4: [("Very good", "Really happy with it overall. Only a couple of small things I'd improve."),
        ("Great, minor niggles", "Does the job well. Took a day or two to get used to, but now I like it a lot.")],
    3: [("It's okay", "Decent for the price, but not as impressive as I hoped."),
        ("Average", "Works fine. Nothing special, nothing bad either.")],
    2: [("Expected more", "The quality didn't quite match the photos. Customer service was helpful though.")],
}


def seed_reviews(product, reviewers):
    """2–6 deterministic demo reviews spread over the last few months."""
    rng = random.Random(product.slug)
    now = timezone.now()
    for user in rng.sample(reviewers, rng.randint(2, min(6, len(reviewers)))):
        rating = rng.choice(RATING_WEIGHTS)
        title, body = rng.choice(REVIEW_TEXT[rating])
        review = Review.objects.create(product=product, user=user, rating=rating, title=title, body=body)
        Review.objects.filter(pk=review.pk).update(created_at=now - timedelta(days=rng.randint(1, 120)))


def seed_delivered_orders(reviewers):
    """Give each demo reviewer one delivered order with ~70% of the products they reviewed.

    That is what earns their reviews the "Verified purchase" badge (via the order signal).
    Skips reviewers who already have orders, so it runs once.
    """
    orders = 0
    for user in reviewers:
        if user.orders.exists():
            continue
        products = [
            r.product for r in user.reviews.select_related("product").order_by("product_id")
            if random.Random(f"{user.username}:{r.product.slug}").random() < 0.7
        ]
        if not products:
            continue
        subtotal = sum((p.price for p in products), Decimal("0.00"))
        with transaction.atomic():
            order = Order.objects.create(
                user=user, full_name=user.get_full_name(), email=f"{user.username}@example.com",
                address_line1="House 12, Road 5", city="Dhaka", postal_code="1209", country="Bangladesh",
                status=Order.Status.DELIVERED, subtotal=subtotal, shipping_cost=Decimal("0.00"), total=subtotal,
                notes=DEMO_ORDER_NOTE,
            )
            OrderItem.objects.bulk_create(
                OrderItem(order=order, product=p, product_name=p.name, unit_price=p.price, quantity=1)
                for p in products
            )
        orders += 1
    return orders


def demo_reviewers():
    users = []
    for username, first, last in REVIEWERS:
        user, created = get_user_model().objects.get_or_create(
            username=username, defaults={"first_name": first, "last_name": last}
        )
        if created:
            user.set_unusable_password()
            user.save(update_fields=["password"])
        users.append(user)
    return users


def demo_image(product, index, size=800):
    """A gradient JPEG with the product's initials, varied per gallery position."""
    start, end = PALETTES[(product.pk + index) % len(PALETTES)]
    img = Image.new("RGB", (size, size))
    draw = ImageDraw.Draw(img)
    for y in range(size):
        t = y / size
        draw.line([(0, y), (size, y)], fill=tuple(round(a + (b - a) * t) for a, b in zip(start, end)))
    initials = "".join(word[0] for word in product.name.split())[:3].upper()
    draw.text((size / 2, size / 2), initials, fill="white", anchor="mm", font=ImageFont.load_default(size=size // 4))
    draw.text((size / 2, size - size // 10), f"View {index + 1}", fill="white", anchor="mm",
              font=ImageFont.load_default(size=size // 20))
    buffer = BytesIO()
    img.save(buffer, "JPEG", quality=85)
    return ContentFile(buffer.getvalue(), name=f"{product.slug}-{index + 1}.jpg")


def demo_photo(product):
    path = DEMO_PHOTOS / f"{product.slug}.jpg"
    return ContentFile(path.read_bytes(), name=path.name) if path.is_file() else None


def is_placeholder(product_image):
    """True for an image made by demo_image() (named <slug>-<n>.jpg, maybe with Django's suffix)."""
    name = Path(product_image.image.name).name
    return re.fullmatch(rf"{re.escape(product_image.product.slug)}-\d+(_[A-Za-z0-9]{{7}})?\.jpg", name) is not None


def seed_images(product):
    """Give a demo product its photo, or generated placeholders when there is none.

    Placeholders from earlier runs are swapped for the photo; any other image (e.g. one
    uploaded in the admin) means the gallery is left alone. Returns the number of images added.
    """
    if product.image:
        return 0
    photo = demo_photo(product)
    existing = list(product.images.all())
    if existing and (photo is None or not all(is_placeholder(i) for i in existing)):
        return 0
    for old in existing:
        old.delete()  # also removes the file
    if photo:
        ProductImage.objects.create(product=product, image=photo, alt_text=product.name, sort_order=0)
        return 1
    for index in range(IMAGES_PER_PRODUCT):
        ProductImage.objects.create(product=product, image=demo_image(product, index), sort_order=index)
    return IMAGES_PER_PRODUCT


class Command(BaseCommand):
    help = "Create demo categories, products and gallery images (idempotent)."

    def add_arguments(self, parser):
        parser.add_argument("--no-images", action="store_true", help="Skip adding gallery images.")

    def handle(self, *args, no_images=False, **options):
        created = images = reviews = 0
        reviewers = demo_reviewers()
        for category_name, products in CATALOG.items():
            category, _ = Category.objects.get_or_create(
                slug=slugify(category_name), defaults={"name": category_name}
            )
            for name, price, compare_at, stock, featured, summary in products:
                extra, specs = DETAILS[name]
                description = f"{extra}\n\n{CATEGORY_COPY[category_name]}"
                product, was_created = Product.objects.get_or_create(
                    slug=slugify(name),
                    defaults={
                        "category": category,
                        "name": name,
                        "summary": summary,
                        "description": description,
                        "price": Decimal(price),
                        "compare_at_price": Decimal(compare_at) if compare_at else None,
                        "stock": stock,
                        "is_featured": featured,
                    },
                )
                created += was_created
                # Fill content for demo products created before summaries/specs existed.
                if not product.summary and product.description in ("", summary):
                    product.summary, product.description = summary, description
                    product.save(update_fields=["summary", "description", "updated_at"])
                if not product.specs.exists():
                    ProductSpec.objects.bulk_create(
                        ProductSpec(product=product, group=group, name=spec_name, value=value, sort_order=i)
                        for i, (group, spec_name, value) in enumerate(specs)
                    )
                if not product.reviews.exists():
                    seed_reviews(product, reviewers)
                    reviews += product.reviews.count()
                if not no_images:
                    images += seed_images(product)
        orders = seed_delivered_orders(reviewers)
        demo_slugs = [slugify(name) for products in CATALOG.values() for name, *_ in products]
        inventory = seed_inventory(demo_slugs)
        self.stdout.write(self.style.SUCCESS(
            f"Demo data ready ({created} new products, {images} new images, "
            f"{reviews} new reviews, {orders} new delivered orders, {inventory['costs']} product costs, "
            f"{inventory['purchases']} purchases, {inventory['expenses']} expenses, {inventory['income']} income records)."
        ))

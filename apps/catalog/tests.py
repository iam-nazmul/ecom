import shutil
import tempfile
from decimal import Decimal
from io import BytesIO, StringIO

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from PIL import Image

from .models import Category, Product, ProductImage, ProductSpec


def make_product(category=None, **kwargs):
    category = category or Category.objects.get_or_create(name="Gadgets", slug="gadgets")[0]
    defaults = {"name": "Widget", "slug": "widget", "price": Decimal("10.00"), "stock": 5}
    defaults.update(kwargs)
    return Product.objects.create(category=category, **defaults)


class CatalogViewTests(TestCase):
    def setUp(self):
        self.gadgets = Category.objects.create(name="Gadgets", slug="gadgets")
        self.books = Category.objects.create(name="Books", slug="books")
        self.widget = make_product(self.gadgets, name="Blue Widget", slug="blue-widget", price=Decimal("20.00"))
        self.novel = make_product(self.books, name="Mystery Novel", slug="mystery-novel", price=Decimal("8.00"))
        self.hidden = make_product(self.books, name="Hidden Item", slug="hidden", is_active=False)

    def test_home_renders(self):
        response = self.client.get(reverse("core:home"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Blue Widget")

    def test_list_hides_inactive(self):
        response = self.client.get(reverse("catalog:product_list"))
        self.assertContains(response, "Mystery Novel")
        self.assertNotContains(response, "Hidden Item")

    def test_search(self):
        response = self.client.get(reverse("catalog:product_list"), {"q": "widget"})
        self.assertContains(response, "Blue Widget")
        self.assertNotContains(response, "Mystery Novel")

    def test_category_filter(self):
        response = self.client.get(reverse("catalog:category", args=["books"]))
        self.assertContains(response, "Mystery Novel")
        self.assertNotContains(response, "Blue Widget")

    def test_sort_by_price(self):
        response = self.client.get(reverse("catalog:product_list"), {"sort": "price_asc"})
        html = response.content.decode()
        self.assertLess(html.index("Mystery Novel"), html.index("Blue Widget"))

    def test_detail_and_inactive_404(self):
        self.assertContains(self.client.get(self.widget.get_absolute_url()), "Add to cart")
        self.assertEqual(self.client.get(reverse("catalog:product_detail", args=["hidden"])).status_code, 404)

    def test_sale_properties(self):
        self.widget.compare_at_price = Decimal("25.00")
        self.assertTrue(self.widget.on_sale)
        self.assertEqual(self.widget.discount_percent, 20)

    def test_seed_demo_is_idempotent(self):
        call_command("seed_demo", no_images=True, stdout=StringIO())
        count = Product.objects.count()
        call_command("seed_demo", no_images=True, stdout=StringIO())
        self.assertEqual(Product.objects.count(), count)
        self.assertFalse(ProductImage.objects.exists())


def upload(name="photo.png", color="red"):
    buffer = BytesIO()
    Image.new("RGB", (10, 10), color).save(buffer, "PNG")
    return SimpleUploadedFile(name, buffer.getvalue(), content_type="image/png")


class GalleryTests(TestCase):
    def setUp(self):
        media_root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, media_root, ignore_errors=True)
        override = override_settings(MEDIA_ROOT=media_root)
        override.enable()
        self.addCleanup(override.disable)
        self.product = make_product(name="Camera", slug="camera")

    def add_image(self, name, sort_order, alt_text=""):
        return ProductImage.objects.create(
            product=self.product, image=upload(name), sort_order=sort_order, alt_text=alt_text
        )

    def test_gallery_order_cover_first(self):
        self.add_image("second.png", 2)
        self.add_image("first.png", 1, alt_text="Front view")
        self.product.image = upload("cover.png")
        self.product.save()
        product = Product.objects.for_display().get(pk=self.product.pk)
        urls = [item["url"] for item in product.gallery]
        self.assertEqual(len(urls), 3)
        self.assertIn("cover", urls[0])
        self.assertIn("first", urls[1])
        self.assertIn("second", urls[2])
        self.assertEqual(product.gallery[1]["alt"], "Front view")
        self.assertEqual(product.gallery[2]["alt"], "Camera")

    def test_cover_falls_back_to_first_gallery_image(self):
        self.assertIsNone(self.product.cover_url)
        self.add_image("only.png", 0)
        product = Product.objects.get(pk=self.product.pk)
        self.assertIn("only", product.cover_url)
        self.assertContains(self.client.get(reverse("catalog:product_list")), product.cover_url)

    def test_detail_renders_thumbnails(self):
        for i in range(3):
            self.add_image(f"img{i}.png", i)
        response = self.client.get(self.product.get_absolute_url())
        self.assertContains(response, "data-gallery-thumb", count=3)
        self.assertContains(response, "1 / 3")
        self.assertContains(response, "js/gallery.js")

    def test_single_image_has_no_carousel(self):
        self.add_image("only.png", 0)
        response = self.client.get(self.product.get_absolute_url())
        self.assertNotContains(response, "data-gallery-thumb")

    def test_delete_removes_file(self):
        image = self.add_image("gone.png", 0)
        storage, name = image.image.storage, image.image.name
        self.assertTrue(storage.exists(name))
        image.delete()
        self.assertFalse(storage.exists(name))

    def test_admin_inline_upload(self):
        admin = User.objects.create_superuser("root", "root@example.com", "pw")
        self.client.force_login(admin)
        url = reverse("admin:catalog_product_change", args=[self.product.pk])
        self.assertContains(self.client.get(url), "images-TOTAL_FORMS")
        data = {
            "category": self.product.category.pk, "name": "Camera", "slug": "camera", "description": "",
            "price": "10.00", "stock": "5", "reorder_level": "5", "is_active": "on",
            "images-TOTAL_FORMS": "2", "images-INITIAL_FORMS": "0",
            "images-MIN_NUM_FORMS": "0", "images-MAX_NUM_FORMS": "1000",
            "images-0-image": upload("a.png"), "images-0-sort_order": "0", "images-0-alt_text": "A",
            "images-1-image": upload("b.png"), "images-1-sort_order": "1", "images-1-alt_text": "",
            "specs-TOTAL_FORMS": "0", "specs-INITIAL_FORMS": "0",
            "specs-MIN_NUM_FORMS": "0", "specs-MAX_NUM_FORMS": "1000",
        }
        response = self.client.post(url, data)
        self.assertEqual(response.status_code, 302, getattr(response, "context", None))
        self.assertEqual(self.product.images.count(), 2)

    def test_seed_demo_generates_images_once(self):
        call_command("seed_demo", stdout=StringIO())
        count = ProductImage.objects.count()
        self.assertEqual(count, (Product.objects.count() - 1) * 3)  # setUp's product isn't in the demo catalog
        call_command("seed_demo", stdout=StringIO())
        self.assertEqual(ProductImage.objects.count(), count)


class DetailsSectionTests(TestCase):
    def setUp(self):
        self.product = make_product(
            name="Phone", slug="phone", summary="A great phone.",
            description="First paragraph.\r\n\r\nSecond paragraph\nwith a line break.\n\n\n",
        )

    def add_spec(self, name, value, group="", sort_order=0):
        return ProductSpec.objects.create(product=self.product, group=group, name=name, value=value, sort_order=sort_order)

    def test_description_paragraphs(self):
        self.assertEqual(
            self.product.description_paragraphs,
            ["First paragraph.", "Second paragraph\nwith a line break."],
        )

    def test_spec_groups_keep_order(self):
        self.add_spec("Battery life", "10 days", group="Battery", sort_order=2)
        self.add_spec("Screen", "6.1 in", group="Display", sort_order=0)
        self.add_spec("Resolution", "1080p", group="Display", sort_order=1)
        self.add_spec("Weight", "180 g", sort_order=3)
        groups = Product.objects.prefetch_related("specs").get(pk=self.product.pk).spec_groups
        self.assertEqual([g for g, _ in groups], ["Display", "Battery", ""])
        self.assertEqual([s.name for s in groups[0][1]], ["Screen", "Resolution"])

    def test_detail_page_renders_section(self):
        self.add_spec("Screen", "6.1 in", group="Display")
        response = self.client.get(self.product.get_absolute_url())
        self.assertContains(response, "A great phone.")
        self.assertContains(response, 'id="description"')
        self.assertContains(response, "<p>Second paragraph\nwith a line break.</p>", html=False)
        self.assertContains(response, 'id="specifications"')
        self.assertContains(response, "Display")
        self.assertContains(response, "6.1 in")
        self.assertContains(response, "View full specifications")

    def test_section_hidden_without_content(self):
        bare = make_product(name="Bare", slug="bare")
        response = self.client.get(bare.get_absolute_url())
        self.assertNotContains(response, 'id="details"')

    def test_summary_falls_back_to_description(self):
        self.product.summary = ""
        self.product.save()
        response = self.client.get(self.product.get_absolute_url())
        self.assertContains(response, "Read full description")
        self.assertNotContains(response, 'id="specifications"')

    def test_search_matches_summary(self):
        response = self.client.get(reverse("catalog:product_list"), {"q": "great phone"})
        self.assertContains(response, "Phone")

    def test_admin_spec_inline(self):
        admin = User.objects.create_superuser("root", "root@example.com", "pw")
        self.client.force_login(admin)
        url = reverse("admin:catalog_product_change", args=[self.product.pk])
        self.assertContains(self.client.get(url), "specs-TOTAL_FORMS")
        data = {
            "category": self.product.category.pk, "name": "Phone", "slug": "phone",
            "summary": "A great phone.", "description": "", "price": "10.00", "stock": "5", "reorder_level": "5", "is_active": "on",
            "images-TOTAL_FORMS": "0", "images-INITIAL_FORMS": "0",
            "images-MIN_NUM_FORMS": "0", "images-MAX_NUM_FORMS": "1000",
            "specs-TOTAL_FORMS": "1", "specs-INITIAL_FORMS": "0",
            "specs-MIN_NUM_FORMS": "0", "specs-MAX_NUM_FORMS": "1000",
            "specs-0-group": "Display", "specs-0-name": "Screen", "specs-0-value": "6.1 in", "specs-0-sort_order": "0",
        }
        self.assertEqual(self.client.post(url, data).status_code, 302)
        self.assertEqual(self.product.specs.get().value, "6.1 in")

    def test_seed_demo_adds_specs_once(self):
        call_command("seed_demo", no_images=True, stdout=StringIO())
        headphones = Product.objects.get(slug="wireless-headphones")
        count = headphones.specs.count()
        self.assertGreater(count, 0)
        self.assertTrue(headphones.summary)
        call_command("seed_demo", no_images=True, stdout=StringIO())
        self.assertEqual(headphones.specs.count(), count)


class HealthTests(TestCase):
    def test_healthz(self):
        self.assertEqual(self.client.get("/healthz").content, b"ok")

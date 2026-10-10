from django.db import models
from django.db.models import Avg, Count, Q
from django.db.models.signals import post_delete
from django.dispatch import receiver
from django.urls import reverse
from django.utils.functional import cached_property


class Category(models.Model):
    name = models.CharField(max_length=100)
    slug = models.SlugField(max_length=120, unique=True)
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]
        verbose_name_plural = "categories"

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        return reverse("catalog:category", kwargs={"slug": self.slug})


class ProductQuerySet(models.QuerySet):
    def available(self):
        return self.filter(is_active=True, category__is_active=True)

    def for_display(self):
        """Available products with what cards and detail pages render, without N+1 queries.

        Adds `rating_avg` (None when unrated) and `rating_count` from published reviews.
        """
        published = Q(reviews__is_approved=True)
        return (
            self.available()
            .select_related("category")
            .prefetch_related("images")
            .annotate(rating_avg=Avg("reviews__rating", filter=published), rating_count=Count("reviews", filter=published))
        )


class Product(models.Model):
    category = models.ForeignKey(Category, on_delete=models.PROTECT, related_name="products")
    name = models.CharField(max_length=200)
    slug = models.SlugField(max_length=220, unique=True)
    summary = models.CharField(
        max_length=300, blank=True,
        help_text="One or two sentences shown next to the price.",
    )
    description = models.TextField(
        blank=True,
        help_text="Full description for the Description section. Separate paragraphs with a blank line.",
    )
    price = models.DecimalField(max_digits=10, decimal_places=2)
    compare_at_price = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True,
        help_text="Original price; shown struck through when higher than the price.",
    )
    stock = models.PositiveIntegerField(
        default=0, help_text="Changed only through purchases, sales and stock adjustments (Inventory).",
    )
    average_cost = models.DecimalField(
        max_digits=12, decimal_places=4, default=0,
        help_text="Weighted-average unit cost, updated when stock is received.",
    )
    reorder_level = models.PositiveIntegerField(default=5, help_text="Flag as low stock at or below this quantity.")
    image = models.ImageField(
        "cover image", upload_to="products/%Y/%m/", blank=True,
        help_text="Shown on product cards and first in the gallery.",
    )
    is_active = models.BooleanField(default=True)
    is_featured = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = ProductQuerySet.as_manager()

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["is_active", "-created_at"])]

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        return reverse("catalog:product_detail", kwargs={"slug": self.slug})

    @property
    def is_low_stock(self):
        return self.stock <= self.reorder_level

    @property
    def stock_value(self):
        return self.stock * self.average_cost

    @property
    def in_stock(self):
        return self.stock > 0

    @property
    def on_sale(self):
        return self.compare_at_price is not None and self.compare_at_price > self.price

    @property
    def discount_percent(self):
        if not self.on_sale:
            return 0
        return round((1 - self.price / self.compare_at_price) * 100)

    @cached_property
    def gallery(self):
        """All images to show, as [{"url", "alt"}]: the cover first, then extra images in order."""
        items = []
        if self.image:
            items.append({"url": self.image.url, "alt": self.name})
        items += [{"url": img.image.url, "alt": img.alt_text or self.name} for img in self.images.all()]
        return items

    @property
    def cover_url(self):
        return self.gallery[0]["url"] if self.gallery else None

    @property
    def description_paragraphs(self):
        return [p.strip() for p in self.description.replace("\r\n", "\n").split("\n\n") if p.strip()]

    @cached_property
    def spec_groups(self):
        """Specs as [(group, [spec, ...])], groups in order of first appearance; "" = ungrouped."""
        groups = {}
        for spec in self.specs.all():
            groups.setdefault(spec.group, []).append(spec)
        return list(groups.items())


class ProductSpec(models.Model):
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="specs")
    group = models.CharField(
        max_length=80, blank=True,
        help_text='Optional heading, e.g. "Battery". Rows with the same group are shown together.',
    )
    name = models.CharField(max_length=100)
    value = models.CharField(max_length=255)
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "pk"]
        verbose_name = "specification"

    def __str__(self):
        return f"{self.name}: {self.value}"


class ProductImage(models.Model):
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="images")
    image = models.ImageField(upload_to="products/gallery/%Y/%m/")
    alt_text = models.CharField(max_length=200, blank=True, help_text="Describes the image; defaults to the product name.")
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "pk"]

    def __str__(self):
        return f"{self.product.name} image #{self.sort_order}"


@receiver(post_delete, sender=ProductImage)
def delete_gallery_file(sender, instance, **kwargs):
    instance.image.delete(save=False)

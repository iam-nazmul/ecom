import secrets
import string

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.urls import reverse
from django.utils import timezone

REFERENCE_ALPHABET = string.ascii_uppercase + string.digits


def generate_reference():
    return "".join(secrets.choice(REFERENCE_ALPHABET) for _ in range(10))


class Order(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        PROCESSING = "processing", "Processing"
        SHIPPED = "shipped", "Shipped"
        DELIVERED = "delivered", "Delivered"
        CANCELLED = "cancelled", "Cancelled"

    class PaymentMethod(models.TextChoices):
        COD = "cod", "Cash on delivery"

    reference = models.CharField(max_length=10, unique=True, default=generate_reference, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="orders"
    )
    full_name = models.CharField(max_length=150)
    email = models.EmailField()
    phone = models.CharField(max_length=30, blank=True)
    address_line1 = models.CharField("address", max_length=255)
    address_line2 = models.CharField("apartment, suite, etc.", max_length=255, blank=True)
    city = models.CharField(max_length=100)
    postal_code = models.CharField(max_length=20)
    country = models.CharField(max_length=100)
    notes = models.TextField(blank=True)

    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    payment_method = models.CharField(max_length=20, choices=PaymentMethod.choices, default=PaymentMethod.COD)
    subtotal = models.DecimalField(max_digits=10, decimal_places=2)
    shipping_cost = models.DecimalField(max_digits=10, decimal_places=2)
    total = models.DecimalField(max_digits=10, decimal_places=2)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    delivered_at = models.DateTimeField(
        null=True, blank=True, editable=False, db_index=True,
        help_text="Set when the status becomes Delivered; revenue is recognised on this date.",
    )

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Order {self.reference}"

    def clean(self):
        if self.pk and self.status != self.Status.CANCELLED:
            previous = Order.objects.filter(pk=self.pk).values_list("status", flat=True).first()
            if previous == self.Status.CANCELLED:
                raise ValidationError({"status": "Cancelled orders can't be reopened (their stock was returned). Place a new order instead."})

    def save(self, *args, **kwargs):
        if self.status == self.Status.DELIVERED:
            self.delivered_at = self.delivered_at or timezone.now()
        else:
            self.delivered_at = None
        update_fields = kwargs.get("update_fields")
        if update_fields is not None and "status" in update_fields:
            kwargs["update_fields"] = {*update_fields, "delivered_at"}
        super().save(*args, **kwargs)

    def get_absolute_url(self):
        return reverse("orders:detail", kwargs={"reference": self.reference})


class OrderItem(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="items")
    product = models.ForeignKey(
        "catalog.Product", on_delete=models.SET_NULL, null=True, blank=True, related_name="order_items"
    )
    product_name = models.CharField(max_length=200)
    unit_price = models.DecimalField(max_digits=10, decimal_places=2)
    unit_cost = models.DecimalField(
        max_digits=12, decimal_places=4, null=True, blank=True,
        help_text="Product's average cost at the time of sale (cost of goods sold). Empty for orders placed before inventory tracking.",
    )
    quantity = models.PositiveIntegerField()

    def __str__(self):
        return f"{self.quantity} × {self.product_name}"

    @property
    def line_total(self):
        return self.unit_price * self.quantity

    @property
    def line_cost(self):
        return (self.unit_cost or 0) * self.quantity

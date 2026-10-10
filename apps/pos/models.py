"""Point of sale.

Isolation rule: links to models outside this app use `db_constraint=False`, so if the app
is switched off (POS_ENABLED=0) its leftover tables can never block deleting products,
orders or users elsewhere.
"""
from decimal import Decimal

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

ZERO = Decimal("0")
MONEY = {"max_digits": 12, "decimal_places": 2}
EXTERNAL = {"db_constraint": False}  # FK to a model owned by another app


class Register(models.Model):
    name = models.CharField(max_length=60, unique=True)
    location = models.CharField(max_length=120, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class RegisterSession(models.Model):
    """A cashier's shift on a register, from opening float to cash count."""

    register = models.ForeignKey(Register, on_delete=models.PROTECT, related_name="sessions")
    opened_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="+", **EXTERNAL)
    opened_at = models.DateTimeField(auto_now_add=True)
    opening_cash = models.DecimalField(**MONEY, default=ZERO, validators=[MinValueValidator(ZERO)])
    closed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+", **EXTERNAL)
    closed_at = models.DateTimeField(null=True, blank=True)
    expected_cash = models.DecimalField(**MONEY, null=True, blank=True, help_text="Snapshot when closed.")
    counted_cash = models.DecimalField(**MONEY, null=True, blank=True)
    note = models.TextField(blank=True)

    class Meta:
        ordering = ["-opened_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["register"], condition=models.Q(closed_at__isnull=True), name="pos_one_open_session_per_register"
            ),
        ]

    def __str__(self):
        return f"{self.register} · {self.opened_at:%Y-%m-%d %H:%M}"

    @property
    def is_open(self):
        return self.closed_at is None

    @property
    def difference(self):
        if self.counted_cash is None or self.expected_cash is None:
            return None
        return self.counted_cash - self.expected_cash


class Sale(models.Model):
    class Status(models.TextChoices):
        COMPLETED = "completed", "Completed"
        VOIDED = "voided", "Voided"

    session = models.ForeignKey(RegisterSession, on_delete=models.PROTECT, related_name="sales")
    order = models.OneToOneField("orders.Order", on_delete=models.SET_NULL, null=True, blank=True, related_name="pos_sale", **EXTERNAL)
    cashier = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="+", **EXTERNAL)
    customer_name = models.CharField(max_length=150, blank=True)
    customer_phone = models.CharField(max_length=30, blank=True)
    subtotal = models.DecimalField(**MONEY, help_text="At list prices, before discount.")
    discount_percent = models.DecimalField(max_digits=5, decimal_places=2, default=ZERO,
                                           validators=[MinValueValidator(ZERO), MaxValueValidator(Decimal("100"))])
    discount_amount = models.DecimalField(**MONEY, default=ZERO)
    total = models.DecimalField(**MONEY)
    change_given = models.DecimalField(**MONEY, default=ZERO)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.COMPLETED)
    voided_at = models.DateTimeField(null=True, blank=True)
    voided_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+", **EXTERNAL)
    void_reason = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at", "-pk"]
        permissions = [("void_sale", "Can void POS sales")]

    def __str__(self):
        return self.number

    @property
    def number(self):
        return f"S-{self.pk:06d}" if self.pk else "S-(new)"

    @property
    def is_voided(self):
        return self.status == self.Status.VOIDED


class SaleLine(models.Model):
    sale = models.ForeignKey(Sale, on_delete=models.CASCADE, related_name="lines")
    product = models.ForeignKey("catalog.Product", on_delete=models.SET_NULL, null=True, related_name="+", **EXTERNAL)
    product_name = models.CharField(max_length=200)
    list_price = models.DecimalField(**MONEY)
    unit_price = models.DecimalField(**MONEY, help_text="After discount.")
    quantity = models.PositiveIntegerField()

    class Meta:
        ordering = ["pk"]

    @property
    def line_total(self):
        return self.unit_price * self.quantity


class Payment(models.Model):
    class Method(models.TextChoices):
        CASH = "cash", "Cash"
        CARD = "card", "Card"
        BKASH = "bkash", "bKash"
        NAGAD = "nagad", "Nagad"
        ROCKET = "rocket", "Rocket"

    sale = models.ForeignKey(Sale, on_delete=models.CASCADE, related_name="payments")
    method = models.CharField(max_length=20, choices=Method.choices)
    amount = models.DecimalField(**MONEY, help_text="Applied to the sale.")
    received = models.DecimalField(**MONEY, help_text="Handed over by the customer (cash may exceed amount; the rest is change).")
    reference = models.CharField(max_length=100, blank=True)

    class Meta:
        ordering = ["pk"]


class CashMovement(models.Model):
    """Cash put into or taken out of the drawer outside sales (float top-up, petty cash)."""

    class Kind(models.TextChoices):
        IN = "in", "Cash in"
        OUT = "out", "Cash out"

    session = models.ForeignKey(RegisterSession, on_delete=models.PROTECT, related_name="cash_movements")
    kind = models.CharField(max_length=3, choices=Kind.choices)
    amount = models.DecimalField(**MONEY, validators=[MinValueValidator(Decimal("0.01"))])
    reason = models.CharField(max_length=255)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="+", **EXTERNAL)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    @property
    def signed_amount(self):
        return self.amount if self.kind == self.Kind.IN else -self.amount


class Barcode(models.Model):
    """Scannable codes for products (kept here so the catalog needs no POS fields)."""

    product = models.ForeignKey("catalog.Product", on_delete=models.CASCADE, related_name="pos_barcodes", **EXTERNAL)
    code = models.CharField(max_length=64, unique=True)

    class Meta:
        ordering = ["code"]

    def __str__(self):
        return self.code

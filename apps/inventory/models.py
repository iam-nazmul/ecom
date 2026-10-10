from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone

ZERO = Decimal("0")
COST = {"max_digits": 12, "decimal_places": 4}
MONEY = {"max_digits": 12, "decimal_places": 2}


class Supplier(models.Model):
    name = models.CharField(max_length=150, unique=True)
    contact_name = models.CharField(max_length=150, blank=True)
    phone = models.CharField(max_length=30, blank=True)
    email = models.EmailField(blank=True)
    address = models.TextField(blank=True)
    notes = models.TextField(blank=True)
    payment_terms_days = models.PositiveIntegerField(
        "payment terms (days)", default=30,
        help_text="Days after a purchase is received before it must be paid. 0 = pay on delivery.",
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class Purchase(models.Model):
    """A purchase order to a supplier. Receiving it adds the items to stock."""

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        ORDERED = "ordered", "Ordered"
        RECEIVED = "received", "Received"
        CANCELLED = "cancelled", "Cancelled"

    OPEN_STATUSES = (Status.DRAFT, Status.ORDERED)

    supplier = models.ForeignKey(Supplier, on_delete=models.PROTECT, related_name="purchases")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)
    order_date = models.DateField(default=timezone.localdate)
    expected_date = models.DateField(null=True, blank=True)
    received_at = models.DateTimeField(null=True, blank=True, editable=False)
    invoice_number = models.CharField("supplier invoice #", max_length=60, blank=True)
    other_costs = models.DecimalField(
        **MONEY, default=ZERO, validators=[MinValueValidator(ZERO)],
        help_text="Shipping, customs, etc. Spread across the items by value into their landed cost.",
    )
    amount_paid = models.DecimalField(
        **MONEY, default=ZERO, editable=False,
        help_text="Sum of supplier payments allocated to this purchase (kept in sync by the payment service).",
    )
    notes = models.TextField(blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, editable=False, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-order_date", "-pk"]

    def __str__(self):
        return f"{self.reference} · {self.supplier}"

    @property
    def reference(self):
        return f"PO-{self.pk:05d}" if self.pk else "PO-(new)"

    @property
    def is_open(self):
        return self.status in self.OPEN_STATUSES

    @property
    def items_total(self):
        return sum((item.line_total for item in self.items.all()), ZERO)

    @property
    def total(self):
        return self.items_total + self.other_costs

    @property
    def balance_due(self):
        return ZERO if self.status == self.Status.CANCELLED else max(self.total - self.amount_paid, ZERO)

    @property
    def due_date(self):
        """Received date + the supplier's payment terms; None until received."""
        if self.status != self.Status.RECEIVED or not self.received_at:
            return None
        return timezone.localtime(self.received_at).date() + timedelta(days=self.supplier.payment_terms_days)

    @property
    def is_overdue(self):
        return bool(self.due_date and self.balance_due > 0 and timezone.localdate() > self.due_date)

    @property
    def payment_status(self):
        if self.status == self.Status.CANCELLED:
            return "—"
        if self.amount_paid <= 0:
            return "Unpaid"
        return "Paid" if self.amount_paid >= self.total else "Partly paid"


class PurchaseItem(models.Model):
    purchase = models.ForeignKey(Purchase, on_delete=models.CASCADE, related_name="items")
    product = models.ForeignKey("catalog.Product", on_delete=models.PROTECT, related_name="purchase_items")
    quantity = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    unit_cost = models.DecimalField(**COST, validators=[MinValueValidator(ZERO)], help_text="Supplier price per unit.")

    class Meta:
        ordering = ["pk"]

    def __str__(self):
        return f"{self.quantity} × {self.product}"

    @property
    def line_total(self):
        return (self.unit_cost or ZERO) * (self.quantity or 0)


class StockMovement(models.Model):
    """Append-only stock ledger. Every change to Product.stock writes exactly one row."""

    class Kind(models.TextChoices):
        OPENING = "opening", "Opening balance"
        PURCHASE = "purchase", "Purchase received"
        SALE = "sale", "Sale"
        RETURN = "return", "Order cancelled (restocked)"
        ADJUSTMENT = "adjustment", "Adjustment"

    product = models.ForeignKey("catalog.Product", on_delete=models.PROTECT, related_name="stock_movements")
    kind = models.CharField(max_length=20, choices=Kind.choices)
    quantity = models.IntegerField(help_text="Positive adds stock, negative removes it.")
    unit_cost = models.DecimalField(**COST)
    balance_after = models.IntegerField()
    purchase = models.ForeignKey(Purchase, on_delete=models.PROTECT, null=True, blank=True, related_name="movements")
    order = models.ForeignKey("orders.Order", on_delete=models.SET_NULL, null=True, blank=True, related_name="stock_movements")
    reference = models.CharField(max_length=100, blank=True)
    note = models.CharField(max_length=255, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    created_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ["-created_at", "-pk"]
        indexes = [models.Index(fields=["product", "-created_at"]), models.Index(fields=["kind", "created_at"])]

    def __str__(self):
        return f"{self.get_kind_display()} {self.quantity:+d} × {self.product}"

    @property
    def value(self):
        return self.quantity * self.unit_cost


class StockAdjustment(models.Model):
    """A manual stock correction (stock count, damage, loss, opening stock)."""

    class Reason(models.TextChoices):
        COUNT = "count", "Stock count correction"
        DAMAGED = "damaged", "Damaged / expired"
        LOST = "lost", "Lost / stolen"
        OPENING = "opening", "Opening stock"
        OTHER = "other", "Other"

    product = models.ForeignKey("catalog.Product", on_delete=models.PROTECT, related_name="stock_adjustments")
    quantity = models.IntegerField(help_text="e.g. 10 to add stock, -2 to remove two units.")
    reason = models.CharField(max_length=20, choices=Reason.choices)
    unit_cost = models.DecimalField(
        **COST, null=True, blank=True, validators=[MinValueValidator(ZERO)],
        help_text="For added stock: cost per unit (blank = current average cost). Removals always use the average cost.",
    )
    note = models.CharField(max_length=255, blank=True)
    movement = models.OneToOneField(StockMovement, on_delete=models.PROTECT, null=True, editable=False, related_name="adjustment")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, editable=False, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.quantity:+d} × {self.product} ({self.get_reason_display()})"

    def clean(self):
        if self.quantity == 0:
            raise ValidationError({"quantity": "Enter a non-zero quantity."})
        if self.product_id and self.quantity and self.product.stock + self.quantity < 0:
            raise ValidationError({"quantity": f"Only {self.product.stock} in stock; can't remove {-self.quantity}."})


class Expense(models.Model):
    class Category(models.TextChoices):
        RENT = "rent", "Rent"
        SALARIES = "salaries", "Salaries & wages"
        UTILITIES = "utilities", "Utilities & internet"
        MARKETING = "marketing", "Marketing & ads"
        DELIVERY = "delivery", "Delivery & courier"
        FEES = "fees", "Bank & payment fees"
        PACKAGING = "packaging", "Packaging & supplies"
        OTHER = "other", "Other"

    date = models.DateField(default=timezone.localdate, db_index=True)
    category = models.CharField(max_length=20, choices=Category.choices)
    description = models.CharField(max_length=255)
    amount = models.DecimalField(**MONEY, validators=[MinValueValidator(Decimal("0.01"))])
    paid_to = models.CharField(max_length=150, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, editable=False, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-date", "-pk"]

    def __str__(self):
        return f"{self.date} {self.get_category_display()}: {self.amount}"


class Income(models.Model):
    """Income from outside product sales (sales and shipping come from delivered orders)."""

    class Category(models.TextChoices):
        SERVICES = "services", "Services (installation, gift wrap, repairs)"
        COMMISSION = "commission", "Commission & affiliate"
        REBATE = "rebate", "Supplier rebates & discounts"
        INTEREST = "interest", "Interest & bank income"
        ASSET_SALE = "asset_sale", "Sale of scrap / old equipment"
        RENTAL = "rental", "Rental income"
        OTHER = "other", "Other"

    date = models.DateField(default=timezone.localdate, db_index=True)
    category = models.CharField(max_length=20, choices=Category.choices)
    description = models.CharField(max_length=255)
    amount = models.DecimalField(**MONEY, validators=[MinValueValidator(Decimal("0.01"))])
    received_from = models.CharField(max_length=150, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, editable=False, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-date", "-pk"]
        verbose_name = "other income"
        verbose_name_plural = "other income"

    def __str__(self):
        return f"{self.date} {self.get_category_display()}: {self.amount}"


class SupplierPayment(models.Model):
    """Money paid to a supplier. Allocated to purchases; any unallocated part is supplier credit."""

    class Method(models.TextChoices):
        CASH = "cash", "Cash"
        BANK = "bank", "Bank transfer"
        BKASH = "bkash", "bKash"
        NAGAD = "nagad", "Nagad"
        ROCKET = "rocket", "Rocket"
        CHEQUE = "cheque", "Cheque"
        CARD = "card", "Card"
        OTHER = "other", "Other"

    supplier = models.ForeignKey(Supplier, on_delete=models.PROTECT, related_name="payments")
    date = models.DateField(default=timezone.localdate, db_index=True)
    amount = models.DecimalField(**MONEY, validators=[MinValueValidator(Decimal("0.01"))])
    method = models.CharField(max_length=20, choices=Method.choices, default=Method.BANK)
    reference = models.CharField(
        max_length=100, blank=True, help_text="Transaction ID, cheque number or receipt number.",
    )
    note = models.CharField(max_length=255, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, editable=False, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-date", "-pk"]

    def __str__(self):
        return f"{self.supplier} · {self.amount} on {self.date}"

    @property
    def allocated(self):
        return sum((a.amount for a in self.allocations.all()), ZERO)

    @property
    def unallocated(self):
        return self.amount - self.allocated


class PaymentAllocation(models.Model):
    """The part of a payment applied to one purchase."""

    payment = models.ForeignKey(SupplierPayment, on_delete=models.CASCADE, related_name="allocations")
    purchase = models.ForeignKey(Purchase, on_delete=models.PROTECT, related_name="allocations")
    amount = models.DecimalField(**MONEY, validators=[MinValueValidator(Decimal("0.01"))])

    class Meta:
        ordering = ["pk"]
        constraints = [models.UniqueConstraint(fields=["payment", "purchase"], name="one_allocation_per_payment_purchase")]

    def __str__(self):
        return f"{self.amount} → {self.purchase.reference}"

from django import forms
from django.contrib import admin, messages
from django.urls import reverse
from django.utils import timezone
from django.utils.html import format_html, format_html_join

from config.jinja2 import money

from .models import (
    Expense, Income, PaymentAllocation, Purchase, PurchaseItem, StockAdjustment, StockMovement, Supplier,
    SupplierPayment,
)
from .payments import AUTO, CREDIT, delete_payment, record_payment, supplier_account
from .services import InventoryError, apply_adjustment, receive_purchase, set_purchase_status


@admin.register(Supplier)
class SupplierAdmin(admin.ModelAdmin):
    list_display = ["name", "contact_name", "phone", "payment_terms_days", "balance_display", "is_active", "statement_link"]
    list_filter = ["is_active"]
    search_fields = ["name", "contact_name", "phone", "email"]

    def get_queryset(self, request):
        return super().get_queryset(request).prefetch_related("purchases__items", "payments")

    @admin.display(description="Balance")
    def balance_display(self, obj):
        account = supplier_account(obj, list(obj.purchases.all()), list(obj.payments.all()))
        if account.owed:
            return format_html('<span style="color:#b45309">{} owed</span>', money(account.owed))
        if account.credit:
            return format_html('<span style="color:#15803d">{} credit</span>', money(account.credit))
        return "Settled"

    @admin.display(description="")
    def statement_link(self, obj):
        return format_html('<a href="{}">Statement &amp; payments</a>', reverse("inventory:supplier", args=[obj.pk]))


class PurchaseItemInline(admin.TabularInline):
    model = PurchaseItem
    extra = 3
    autocomplete_fields = ["product"]
    fields = ["product", "quantity", "unit_cost", "line_total_display"]
    readonly_fields = ["line_total_display"]

    @admin.display(description="Line total")
    def line_total_display(self, obj):
        return money(obj.line_total) if obj.pk else "—"

    def _locked(self, obj):
        return obj is not None and not obj.is_open

    def has_add_permission(self, request, obj=None):
        return not self._locked(obj) and super().has_add_permission(request, obj)

    def has_change_permission(self, request, obj=None):
        return not self._locked(obj) and super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        return not self._locked(obj) and super().has_delete_permission(request, obj)


@admin.register(Purchase)
class PurchaseAdmin(admin.ModelAdmin):
    list_display = ["reference_display", "supplier", "status", "order_date", "received_at",
                    "total_display", "paid_display", "balance_display", "payment_status"]
    list_filter = ["status", "supplier", "order_date"]
    search_fields = ["id", "invoice_number", "supplier__name", "items__product__name"]
    date_hierarchy = "order_date"
    autocomplete_fields = ["supplier"]
    inlines = [PurchaseItemInline]
    actions = ["mark_ordered", "receive", "cancel"]
    fieldsets = [
        (None, {"fields": ["supplier", "status_display", "order_date", "expected_date", "received_at", "invoice_number"]}),
        ("Costs & payment", {"fields": ["other_costs", "totals_display", "payments_display"]}),
        ("Notes", {"fields": ["notes"]}),
    ]

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("supplier").prefetch_related("items").distinct()

    def get_readonly_fields(self, request, obj=None):
        readonly = ["status_display", "received_at", "totals_display", "payments_display"]
        if obj and not obj.is_open:
            # Stock was already received (or the PO cancelled): only payment and notes stay editable.
            readonly += ["supplier", "order_date", "expected_date", "other_costs"]
        return readonly

    def save_model(self, request, obj, form, change):
        if not change:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)

    @admin.display(description="Reference", ordering="id")
    def reference_display(self, obj):
        return obj.reference

    @admin.display(description="Status")
    def status_display(self, obj):
        if not obj.pk:
            return "Draft — save, then use the list actions to mark it ordered or received."
        return obj.get_status_display()

    @admin.display(description="Totals")
    def totals_display(self, obj):
        if not obj.pk:
            return "—"
        return format_html(
            "Items {} + other costs {} = <b>{}</b> · paid {} · <b>due {}</b>",
            money(obj.items_total), money(obj.other_costs), money(obj.total), money(obj.amount_paid), money(obj.balance_due),
        )

    @admin.display(description="Payments")
    def payments_display(self, obj):
        if not obj.pk:
            return "—"
        rows = format_html_join(
            "", "<li>{} · {} · {} {}</li>",
            ((a.payment.date.strftime("%b %d, %Y"), money(a.amount), a.payment.get_method_display(), a.payment.reference)
             for a in obj.allocations.select_related("payment")),
        )
        link = format_html('<a href="{}#pay">Record a payment for {}</a>', reverse("inventory:supplier", args=[obj.supplier_id]), obj.supplier)
        due = format_html(" · due {}", obj.due_date.strftime("%b %d, %Y")) if obj.due_date else ""
        return format_html('<ul style="margin:0;padding-left:1em">{}</ul>{}{}', rows or "No payments yet.", link, due)

    @admin.display(description="Total")
    def total_display(self, obj):
        return money(obj.total)

    @admin.display(description="Paid", ordering="amount_paid")
    def paid_display(self, obj):
        return money(obj.amount_paid)

    @admin.display(description="Due")
    def balance_display(self, obj):
        return money(obj.balance_due)

    def _run(self, request, queryset, func, done):
        ok = 0
        for purchase in queryset:
            try:
                func(purchase)
                ok += 1
            except InventoryError as exc:
                self.message_user(request, str(exc), messages.ERROR)
        if ok:
            self.message_user(request, f"{ok} purchase(s) {done}.", messages.SUCCESS)

    @admin.action(description="Mark selected as ordered")
    def mark_ordered(self, request, queryset):
        self._run(request, queryset, lambda p: set_purchase_status(p, Purchase.Status.ORDERED), "marked as ordered")

    @admin.action(description="Receive selected into stock")
    def receive(self, request, queryset):
        self._run(request, queryset, lambda p: receive_purchase(p, user=request.user), "received into stock")

    @admin.action(description="Cancel selected (not yet received)")
    def cancel(self, request, queryset):
        self._run(request, queryset, lambda p: set_purchase_status(p, Purchase.Status.CANCELLED), "cancelled")


@admin.register(StockMovement)
class StockMovementAdmin(admin.ModelAdmin):
    list_display = ["created_at", "product", "kind", "quantity", "unit_cost", "balance_after", "reference", "created_by"]
    list_filter = ["kind", "created_at"]
    list_select_related = ["product", "created_by"]
    search_fields = ["product__name", "reference", "note"]
    date_hierarchy = "created_at"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class StockAdjustmentForm(forms.ModelForm):
    class Meta:
        model = StockAdjustment
        fields = ["product", "quantity", "reason", "unit_cost", "note"]


@admin.register(StockAdjustment)
class StockAdjustmentAdmin(admin.ModelAdmin):
    form = StockAdjustmentForm
    list_display = ["created_at", "product", "quantity", "reason", "value_display", "note", "created_by"]
    list_filter = ["reason", "created_at"]
    list_select_related = ["product", "created_by", "movement"]
    search_fields = ["product__name", "note"]
    autocomplete_fields = ["product"]

    def save_model(self, request, obj, form, change):
        apply_adjustment(obj, user=request.user)

    @admin.display(description="Value at cost")
    def value_display(self, obj):
        return money(obj.movement.value) if obj.movement else "—"

    def has_change_permission(self, request, obj=None):
        return obj is None and super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Expense)
class ExpenseAdmin(admin.ModelAdmin):
    list_display = ["date", "category", "description", "amount_display", "paid_to", "created_by"]
    list_filter = ["category", "date"]
    search_fields = ["description", "paid_to"]
    date_hierarchy = "date"

    def save_model(self, request, obj, form, change):
        if not change:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)

    @admin.display(description="Amount", ordering="amount")
    def amount_display(self, obj):
        return money(obj.amount)


@admin.register(Income)
class IncomeAdmin(admin.ModelAdmin):
    list_display = ["date", "category", "description", "amount_display", "received_from", "created_by"]
    list_filter = ["category", "date"]
    search_fields = ["description", "received_from"]
    date_hierarchy = "date"

    def save_model(self, request, obj, form, change):
        if not change:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)

    @admin.display(description="Amount", ordering="amount")
    def amount_display(self, obj):
        return money(obj.amount)


class PaymentAllocationInline(admin.TabularInline):
    model = PaymentAllocation
    extra = 0
    fields = ["purchase", "amount"]
    readonly_fields = fields
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False


class SupplierPaymentAdminForm(forms.ModelForm):
    allocate_to = forms.ModelChoiceField(
        queryset=Purchase.objects.exclude(status=Purchase.Status.CANCELLED).select_related("supplier"),
        required=False, empty_label="Oldest unpaid received purchases first",
        help_text="Or pick one purchase (an open PO = advance payment). Any excess goes oldest-first.",
    )
    keep_as_credit = forms.BooleanField(required=False, help_text="Don't apply to purchases; keep the whole amount as supplier credit.")

    class Meta:
        model = SupplierPayment
        fields = ["supplier", "date", "amount", "method", "reference", "note"]

    def clean(self):
        data = super().clean()
        supplier, target = data.get("supplier"), data.get("allocate_to")
        if target and supplier and target.supplier_id != supplier.pk:
            self.add_error("allocate_to", f"{target.reference} is from {target.supplier}, not {supplier}.")
        if data.get("date") and data["date"] > timezone.localdate():
            self.add_error("date", "Payment date can't be in the future.")
        return data


@admin.register(SupplierPayment)
class SupplierPaymentAdmin(admin.ModelAdmin):
    form = SupplierPaymentAdminForm
    list_display = ["date", "supplier", "amount_display", "method", "reference", "allocated_display", "credit_display", "created_by"]
    list_filter = ["method", "supplier", "date"]
    search_fields = ["reference", "note", "supplier__name"]
    date_hierarchy = "date"
    autocomplete_fields = ["supplier"]
    inlines = [PaymentAllocationInline]

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("supplier", "created_by").prefetch_related("allocations")

    def get_inlines(self, request, obj):
        return self.inlines if obj else []  # allocations exist only after saving

    def get_fields(self, request, obj=None):
        if obj:  # amounts and allocation are fixed once saved; delete and re-enter to change them
            return ["supplier", "date", "amount", "method", "reference", "note"]
        return ["supplier", "date", "amount", "method", "reference", "allocate_to", "keep_as_credit", "note"]

    def get_readonly_fields(self, request, obj=None):
        return ["supplier", "date", "amount", "method"] if obj else []

    def save_model(self, request, obj, form, change):
        if change:
            return super().save_model(request, obj, form, change)
        data = form.cleaned_data
        target = CREDIT if data.get("keep_as_credit") else (data.get("allocate_to") or AUTO)
        payment = record_payment(
            obj.supplier, obj.amount, date=obj.date, method=obj.method, reference=obj.reference,
            note=obj.note, user=request.user, allocate_to=target,
        )
        obj.pk, obj.created_at, obj.created_by = payment.pk, payment.created_at, payment.created_by

    def save_related(self, request, form, formsets, change):
        if change:
            super().save_related(request, form, formsets, change)

    def delete_model(self, request, obj):
        delete_payment(obj)

    def delete_queryset(self, request, queryset):
        for payment in queryset:
            delete_payment(payment)

    @admin.display(description="Amount", ordering="amount")
    def amount_display(self, obj):
        return money(obj.amount)

    @admin.display(description="Applied")
    def allocated_display(self, obj):
        return money(obj.allocated)

    @admin.display(description="Credit")
    def credit_display(self, obj):
        return money(obj.unallocated) if obj.unallocated else "—"

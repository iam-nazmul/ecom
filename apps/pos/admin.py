from django.contrib import admin
from django.urls import reverse
from django.utils.html import format_html

from config.jinja2 import money

from .models import Barcode, CashMovement, Payment, Register, RegisterSession, Sale, SaleLine


class ReadOnlyMixin:
    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Register)
class RegisterAdmin(admin.ModelAdmin):
    list_display = ["name", "location", "is_active"]
    list_editable = ["is_active"]


@admin.register(RegisterSession)
class RegisterSessionAdmin(ReadOnlyMixin, admin.ModelAdmin):
    list_display = ["register", "opened_by", "opened_at", "closed_at", "opening_cash", "expected_cash", "counted_cash", "difference_display", "report"]
    list_filter = ["register", "opened_at"]
    list_select_related = ["register", "opened_by"]
    date_hierarchy = "opened_at"

    @admin.display(description="Over / short")
    def difference_display(self, obj):
        return "—" if obj.difference is None else money(obj.difference)

    @admin.display(description="")
    def report(self, obj):
        return format_html('<a href="{}">Z-report</a>', reverse("pos:session", args=[obj.pk]))


class SaleLineInline(ReadOnlyMixin, admin.TabularInline):
    model = SaleLine
    fields = ["product_name", "list_price", "unit_price", "quantity"]
    readonly_fields = fields


class PaymentInline(ReadOnlyMixin, admin.TabularInline):
    model = Payment
    fields = ["method", "amount", "received", "reference"]
    readonly_fields = fields


@admin.register(Sale)
class SaleAdmin(ReadOnlyMixin, admin.ModelAdmin):
    list_display = ["number", "created_at", "session", "cashier", "total", "discount_amount", "status", "receipt_link"]
    list_filter = ["status", "session__register", "created_at"]
    list_select_related = ["session__register", "cashier"]
    search_fields = ["id", "customer_name", "customer_phone", "lines__product_name"]
    date_hierarchy = "created_at"
    inlines = [SaleLineInline, PaymentInline]

    def get_queryset(self, request):
        return super().get_queryset(request).distinct()

    @admin.display(description="")
    def receipt_link(self, obj):
        return format_html('<a href="{}" target="_blank">Receipt</a>', reverse("pos:receipt", args=[obj.pk]))


@admin.register(CashMovement)
class CashMovementAdmin(ReadOnlyMixin, admin.ModelAdmin):
    list_display = ["created_at", "session", "kind", "amount", "reason", "created_by"]
    list_filter = ["kind", "created_at"]


@admin.register(Barcode)
class BarcodeAdmin(admin.ModelAdmin):
    list_display = ["code", "product"]
    search_fields = ["code", "product__name"]
    autocomplete_fields = ["product"]

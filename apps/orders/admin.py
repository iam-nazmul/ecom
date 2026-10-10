from django.contrib import admin

from .models import Order, OrderItem


class OrderItemInline(admin.TabularInline):
    model = OrderItem
    extra = 0
    can_delete = False
    fields = ["product", "product_name", "unit_price", "quantity"]
    readonly_fields = fields

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = ["reference", "full_name", "email", "status", "total", "created_at"]
    list_editable = ["status"]
    list_filter = ["status", "created_at"]
    search_fields = ["reference", "full_name", "email"]
    date_hierarchy = "created_at"
    readonly_fields = ["reference", "user", "subtotal", "shipping_cost", "total", "created_at", "updated_at"]
    inlines = [OrderItemInline]
    fieldsets = [
        (None, {"fields": ["reference", "status", "payment_method", "user", "created_at", "updated_at"]}),
        ("Customer", {"fields": ["full_name", "email", "phone"]}),
        ("Shipping", {"fields": ["address_line1", "address_line2", "city", "postal_code", "country", "notes"]}),
        ("Totals", {"fields": ["subtotal", "shipping_cost", "total"]}),
    ]

from django.contrib import admin
from django.utils.html import format_html

from .models import Category, Product, ProductImage, ProductSpec


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ["name", "slug", "is_active"]
    list_editable = ["is_active"]
    prepopulated_fields = {"slug": ["name"]}
    search_fields = ["name"]


def thumbnail(url, height=60):
    if not url:
        return "—"
    return format_html('<img src="{}" alt="" style="height:{}px;border-radius:6px">', url, height)


class ProductImageInline(admin.TabularInline):
    model = ProductImage
    extra = 3
    fields = ["preview", "image", "alt_text", "sort_order"]
    readonly_fields = ["preview"]

    @admin.display(description="Preview")
    def preview(self, obj):
        return thumbnail(obj.image.url if obj.image else None)


class ProductSpecInline(admin.TabularInline):
    model = ProductSpec
    extra = 3
    fields = ["group", "name", "value", "sort_order"]


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ["cover", "name", "category", "price", "average_cost", "stock", "is_active", "is_featured", "updated_at"]
    list_display_links = ["cover", "name"]
    list_editable = ["price", "is_active", "is_featured"]
    readonly_fields = ["stock", "average_cost"]
    list_filter = ["category", "is_active", "is_featured"]
    list_select_related = ["category"]
    search_fields = ["name", "summary", "description"]
    prepopulated_fields = {"slug": ["name"]}
    inlines = [ProductImageInline, ProductSpecInline]
    fieldsets = [
        (None, {"fields": ["category", "name", "slug", "image", "is_active", "is_featured"]}),
        ("Pricing", {"fields": ["price", "compare_at_price"]}),
        ("Inventory", {
            "fields": ["stock", "average_cost", "reorder_level"],
            "description": "Stock changes through Purchases (receiving), sales and Stock adjustments.",
        }),
        ("Content", {"fields": ["summary", "description"]}),
    ]

    def get_queryset(self, request):
        return super().get_queryset(request).prefetch_related("images")

    @admin.display(description="")
    def cover(self, obj):
        return thumbnail(obj.cover_url, height=40)

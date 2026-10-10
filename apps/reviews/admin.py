from django.contrib import admin

from .models import Review


@admin.register(Review)
class ReviewAdmin(admin.ModelAdmin):
    list_display = ["product", "user", "rating", "title", "verified_purchase", "is_approved", "created_at"]
    list_editable = ["is_approved"]
    list_filter = ["is_approved", "rating", "verified_purchase", "created_at"]
    list_select_related = ["product", "user"]
    search_fields = ["title", "body", "product__name", "user__username", "user__email"]
    autocomplete_fields = ["product", "user"]
    readonly_fields = ["verified_purchase", "created_at", "updated_at"]
    date_hierarchy = "created_at"
    actions = ["publish", "unpublish"]

    @admin.action(description="Publish selected reviews")
    def publish(self, request, queryset):
        self.message_user(request, f"{queryset.update(is_approved=True)} review(s) published.")

    @admin.action(description="Hide selected reviews")
    def unpublish(self, request, queryset):
        self.message_user(request, f"{queryset.update(is_approved=False)} review(s) hidden.")

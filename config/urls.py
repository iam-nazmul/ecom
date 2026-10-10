from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

admin.site.site_header = "Ecom administration"
admin.site.site_title = "Ecom admin"

urlpatterns = [
    path("admin/", admin.site.urls),
    path("accounts/", include("apps.accounts.urls")),
    path("cart/", include("apps.cart.urls")),
    path("staff/", include("apps.inventory.urls")),
    path("", include("apps.orders.urls")),
    path("", include("apps.reviews.urls")),
    path("", include("apps.catalog.urls")),
    path("", include("apps.core.urls")),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

from django.http import HttpResponse
from django.shortcuts import render

from apps.catalog.models import Category, Product


def home(request):
    products = Product.objects.for_display()
    return render(
        request,
        "core/home.html",
        {
            "featured": products.filter(is_featured=True)[:8],
            "newest": products.order_by("-created_at")[:8],
            "categories": Category.objects.filter(is_active=True),
        },
    )


def healthz(request):
    return HttpResponse("ok", content_type="text/plain")

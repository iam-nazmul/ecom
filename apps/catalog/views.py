from django.conf import settings
from django.core.paginator import Paginator
from django.db.models import F, Q
from django.shortcuts import get_object_or_404, render

from apps.reviews.services import reviews_context

from .models import Category, Product

SORT_OPTIONS = {
    "newest": ("Newest", ["-created_at"]),
    "price_asc": ("Price: low to high", ["price"]),
    "price_desc": ("Price: high to low", ["-price"]),
    "rating": ("Top rated", [F("rating_avg").desc(nulls_last=True), "-rating_count"]),
    "name": ("Name", ["name"]),
}


def product_list(request, slug=None):
    products = Product.objects.for_display()
    categories = Category.objects.filter(is_active=True)

    category_slug = slug or request.GET.get("category")
    category = None
    if category_slug:
        category = get_object_or_404(categories, slug=category_slug)
        products = products.filter(category=category)

    query = request.GET.get("q", "").strip()
    if query:
        products = products.filter(
            Q(name__icontains=query) | Q(summary__icontains=query) | Q(description__icontains=query)
        )

    sort = request.GET.get("sort", "newest")
    if sort not in SORT_OPTIONS:
        sort = "newest"
    products = products.order_by(*SORT_OPTIONS[sort][1], "pk")

    page = Paginator(products, settings.PRODUCTS_PER_PAGE).get_page(request.GET.get("page"))
    return render(
        request,
        "catalog/product_list.html",
        {
            "page": page,
            "categories": categories,
            "category": category,
            "query": query,
            "sort": sort,
            "sort_options": SORT_OPTIONS,
        },
    )


def detail_queryset():
    return Product.objects.for_display().prefetch_related("specs")


def render_product_detail(request, product, review_form=None):
    related = (
        Product.objects.for_display()
        .filter(category=product.category)
        .exclude(pk=product.pk)[:4]
    )
    context = {"product": product, "related": related, **reviews_context(request, product, review_form)}
    return render(request, "catalog/product_detail.html", context)


def product_detail(request, slug):
    return render_product_detail(request, get_object_or_404(detail_queryset(), slug=slug))

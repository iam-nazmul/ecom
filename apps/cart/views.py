from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from apps.catalog.models import Product
from apps.orders.services import shipping_for

from .cart import Cart


def _quantity(request, default=1):
    try:
        return max(0, int(request.POST.get("quantity", default)))
    except (TypeError, ValueError):
        return default


def _redirect_back(request, fallback="cart:detail"):
    target = request.POST.get("next")
    if target and url_has_allowed_host_and_scheme(target, {request.get_host()}, request.is_secure()):
        return redirect(target)
    return redirect(fallback)


def detail(request):
    cart = Cart(request)
    subtotal = cart.subtotal
    shipping = shipping_for(subtotal)
    return render(
        request,
        "cart/detail.html",
        {"cart": cart, "subtotal": subtotal, "shipping": shipping, "total": subtotal + shipping},
    )


@require_POST
def add(request, product_id):
    product = get_object_or_404(Product.objects.available(), pk=product_id)
    if not product.in_stock:
        messages.error(request, f"Sorry, {product.name} is out of stock.")
        return _redirect_back(request)
    Cart(request).add(product, _quantity(request) or 1)
    messages.success(request, f"Added {product.name} to your cart.")
    return _redirect_back(request)


@require_POST
def update(request, product_id):
    product = get_object_or_404(Product.objects.available(), pk=product_id)
    Cart(request).set(product, _quantity(request))
    return redirect("cart:detail")


@require_POST
def remove(request, product_id):
    Cart(request).remove(product_id)
    messages.info(request, "Item removed from your cart.")
    return redirect("cart:detail")

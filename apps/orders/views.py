from django.contrib import messages
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render

from apps.cart.cart import Cart

from .forms import CheckoutForm
from .models import Order
from .services import OutOfStock, place_order, shipping_for

RECENT_ORDERS_KEY = "recent_orders"


def checkout(request):
    cart = Cart(request)
    if not cart.lines:
        messages.info(request, "Your cart is empty.")
        return redirect("cart:detail")

    if request.method == "POST":
        form = CheckoutForm(request.POST)
        if form.is_valid():
            try:
                order = place_order(cart, form.cleaned_data, user=request.user)
            except OutOfStock as exc:
                messages.error(request, str(exc))
                return redirect("cart:detail")
            recent = request.session.get(RECENT_ORDERS_KEY, [])
            request.session[RECENT_ORDERS_KEY] = [order.reference, *recent][:10]
            messages.success(request, "Thank you! Your order has been placed.")
            return redirect(order)
    else:
        initial = {}
        if request.user.is_authenticated:
            initial = {"full_name": request.user.get_full_name(), "email": request.user.email}
        form = CheckoutForm(initial=initial)

    subtotal = cart.subtotal
    shipping = shipping_for(subtotal)
    return render(
        request,
        "orders/checkout.html",
        {"form": form, "cart": cart, "subtotal": subtotal, "shipping": shipping, "total": subtotal + shipping},
    )


def detail(request, reference):
    order = get_object_or_404(Order.objects.prefetch_related("items"), reference=reference)
    owns = request.user.is_authenticated and order.user_id == request.user.pk
    placed_here = reference in request.session.get(RECENT_ORDERS_KEY, [])
    if not (owns or placed_here or request.user.is_staff):
        raise Http404
    return render(request, "orders/detail.html", {"order": order})

from urllib.parse import urlencode

from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.views.decorators.http import require_POST

from apps.catalog.models import Product
from apps.catalog.views import detail_queryset, render_product_detail

from .forms import ReviewForm
from .services import save_review


def _reviews_url(product):
    return f"{product.get_absolute_url()}#reviews"


def _login_redirect(product):
    return redirect(f"{reverse('accounts:login')}?{urlencode({'next': _reviews_url(product)})}")


@require_POST
def submit(request, slug):
    product = get_object_or_404(detail_queryset(), slug=slug)
    if not request.user.is_authenticated:
        return _login_redirect(product)

    existing = product.reviews.filter(user=request.user).first()
    form = ReviewForm(request.POST, instance=existing)
    if form.is_valid():
        save_review(form, product, request.user)
        messages.success(request, "Your review has been updated." if existing else "Thanks for your review!")
        return redirect(_reviews_url(product))
    return render_product_detail(request, product, review_form=form)


@require_POST
def delete(request, slug):
    product = get_object_or_404(Product.objects.available(), slug=slug)
    if not request.user.is_authenticated:
        return _login_redirect(product)
    deleted, _ = product.reviews.filter(user=request.user).delete()
    if deleted:
        messages.info(request, "Your review has been deleted.")
    return redirect(_reviews_url(product))

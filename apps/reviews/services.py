from django.core.paginator import Paginator
from django.db.models import Count

from apps.orders.models import Order, OrderItem

from .forms import ReviewForm
from .models import Review

REVIEWS_PER_PAGE = 5
REVIEW_SORTS = {
    "newest": ("Newest", ["-created_at"]),
    "highest": ("Highest rated", ["-rating", "-created_at"]),
    "lowest": ("Lowest rated", ["rating", "-created_at"]),
}


def has_received(user, product):
    """True when the user has an order containing the product that was delivered."""
    return OrderItem.objects.filter(
        order__user=user, order__status=Order.Status.DELIVERED, product=product
    ).exists()


def refresh_verified_purchases(user_id, product_ids):
    """Re-derive `verified_purchase` for the user's reviews of these products.

    Called whenever an order's status changes, so a review written before delivery gets
    the badge once the order is delivered, and loses it if the order is cancelled.
    """
    if not user_id or not product_ids:
        return 0
    received = set(
        OrderItem.objects.filter(
            order__user_id=user_id, order__status=Order.Status.DELIVERED, product_id__in=product_ids
        ).values_list("product_id", flat=True)
    )
    reviews = Review.objects.filter(user_id=user_id, product_id__in=product_ids)
    updated = reviews.filter(product_id__in=received, verified_purchase=False).update(verified_purchase=True)
    updated += reviews.exclude(product_id__in=received).filter(verified_purchase=True).update(verified_purchase=False)
    return updated


def rating_summary(product):
    """Average, count and a 5→1 distribution of published ratings."""
    counts = dict(
        Review.objects.published()
        .filter(product=product)
        .order_by()
        .values_list("rating")
        .annotate(n=Count("id"))
    )
    total = sum(counts.values())
    average = sum(rating * n for rating, n in counts.items()) / total if total else None
    return {
        "count": total,
        "average": round(average, 1) if average is not None else None,
        "distribution": [
            (rating, counts.get(rating, 0), round(counts.get(rating, 0) * 100 / total) if total else 0)
            for rating in range(5, 0, -1)
        ],
    }


def save_review(form, product, user):
    review = form.save(commit=False)
    review.product = product
    review.user = user
    review.verified_purchase = has_received(user, product)
    review.save()
    return review


def reviews_context(request, product, form=None):
    sort = request.GET.get("reviews_sort", "newest")
    if sort not in REVIEW_SORTS:
        sort = "newest"
    reviews = (
        product.reviews.published()
        .select_related("user")
        .order_by(*REVIEW_SORTS[sort][1], "-pk")
    )
    user_review = None
    if request.user.is_authenticated:
        user_review = product.reviews.filter(user=request.user).first()
        if form is None:
            form = ReviewForm(instance=user_review)
    return {
        "rating": rating_summary(product),
        "reviews_page": Paginator(reviews, REVIEWS_PER_PAGE).get_page(request.GET.get("reviews_page")),
        "reviews_sort": sort,
        "review_sorts": REVIEW_SORTS,
        "user_review": user_review,
        "review_form": form,
    }

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


def has_purchased(user, product):
    return (
        OrderItem.objects.filter(order__user=user, product=product)
        .exclude(order__status=Order.Status.CANCELLED)
        .exists()
    )


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
    review.verified_purchase = has_purchased(user, product)
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

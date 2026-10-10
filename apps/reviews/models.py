from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models


class ReviewQuerySet(models.QuerySet):
    def published(self):
        return self.filter(is_approved=True)


class Review(models.Model):
    product = models.ForeignKey("catalog.Product", on_delete=models.CASCADE, related_name="reviews")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="reviews")
    rating = models.PositiveSmallIntegerField(validators=[MinValueValidator(1), MaxValueValidator(5)])
    title = models.CharField(max_length=120, blank=True)
    body = models.TextField("review", max_length=2000)
    verified_purchase = models.BooleanField(default=False)
    is_approved = models.BooleanField("published", default=True, help_text="Untick to hide this review from the store.")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = ReviewQuerySet.as_manager()

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(fields=["product", "user"], name="one_review_per_user_per_product"),
            models.CheckConstraint(condition=models.Q(rating__gte=1, rating__lte=5), name="review_rating_1_to_5"),
        ]
        indexes = [models.Index(fields=["product", "is_approved", "-created_at"])]

    def __str__(self):
        return f"{self.rating}★ {self.product} by {self.user}"

    @property
    def author_name(self):
        """'Ayesha R.' when the user has a name, otherwise their username."""
        first, last = self.user.first_name.strip(), self.user.last_name.strip()
        if first:
            return f"{first} {last[0]}." if last else first
        return self.user.get_username()

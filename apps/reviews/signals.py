"""Keep `Review.verified_purchase` in sync with order delivery status."""
from django.db import transaction
from django.db.models.signals import post_delete, post_save, pre_delete
from django.dispatch import receiver

from apps.orders.models import Order

from .services import refresh_verified_purchases


def _product_ids(order):
    return list(order.items.exclude(product=None).values_list("product_id", flat=True))


@receiver(post_save, sender=Order)
def order_saved(sender, instance, **kwargs):
    if instance.user_id:
        # After commit, so items created alongside the order in the same transaction are seen.
        transaction.on_commit(lambda: refresh_verified_purchases(instance.user_id, _product_ids(instance)))


@receiver(pre_delete, sender=Order)
def remember_order_products(sender, instance, **kwargs):
    # Items are deleted by cascade before post_delete runs, so capture them now.
    instance._review_product_ids = _product_ids(instance) if instance.user_id else []


@receiver(post_delete, sender=Order)
def order_deleted(sender, instance, **kwargs):
    product_ids = getattr(instance, "_review_product_ids", [])
    if product_ids:
        transaction.on_commit(lambda: refresh_verified_purchases(instance.user_id, product_ids))

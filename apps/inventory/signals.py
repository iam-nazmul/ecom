"""Return stock when an order is cancelled."""
from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver

from apps.orders.models import Order

from .services import restock_cancelled_order


@receiver(pre_save, sender=Order)
def remember_previous_status(sender, instance, **kwargs):
    instance._previous_status = (
        Order.objects.filter(pk=instance.pk).values_list("status", flat=True).first() if instance.pk else None
    )


@receiver(post_save, sender=Order)
def restock_on_cancel(sender, instance, created, **kwargs):
    previous = getattr(instance, "_previous_status", None)
    if not created and instance.status == Order.Status.CANCELLED and previous != Order.Status.CANCELLED:
        restock_cancelled_order(instance)

from decimal import Decimal

from django.conf import settings
from django.db import transaction

from apps.catalog.models import Product

from .models import Order, OrderItem


class OutOfStock(Exception):
    def __init__(self, product, available):
        self.product = product
        self.available = available
        super().__init__(f"Only {available} of {product.name} left in stock.")


def shipping_for(subtotal):
    if subtotal <= 0 or subtotal >= settings.FREE_SHIPPING_THRESHOLD:
        return Decimal("0.00")
    return settings.SHIPPING_FLAT_RATE


def place_order(cart, shipping_data, user=None):
    """Create an order from the cart, decrementing stock atomically.

    Raises OutOfStock (and writes nothing) if any line exceeds available stock.
    """
    quantities = {line.product.pk: line.quantity for line in cart.lines}
    if not quantities:
        raise ValueError("Cannot place an order with an empty cart.")

    with transaction.atomic():
        products = Product.objects.select_for_update().filter(pk__in=quantities).order_by("pk")
        products = {p.pk: p for p in products}

        for line in cart.lines:
            product = products.get(line.product.pk)
            available = product.stock if product and product.is_active else 0
            if available < line.quantity:
                raise OutOfStock(line.product, available)

        subtotal = sum((products[pk].price * qty for pk, qty in quantities.items()), Decimal("0.00"))
        shipping = shipping_for(subtotal)
        order = Order.objects.create(
            user=user if user and user.is_authenticated else None,
            subtotal=subtotal,
            shipping_cost=shipping,
            total=subtotal + shipping,
            **shipping_data,
        )
        OrderItem.objects.bulk_create(
            OrderItem(
                order=order,
                product=products[pk],
                product_name=products[pk].name,
                unit_price=products[pk].price,
                quantity=qty,
            )
            for pk, qty in quantities.items()
        )
        for pk, qty in quantities.items():
            product = products[pk]
            product.stock -= qty
            product.save(update_fields=["stock", "updated_at"])

    cart.clear()
    return order

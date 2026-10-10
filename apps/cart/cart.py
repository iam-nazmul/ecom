from dataclasses import dataclass
from decimal import Decimal

from apps.catalog.models import Product

SESSION_KEY = "cart"


@dataclass
class CartLine:
    product: Product
    quantity: int

    @property
    def line_total(self):
        return self.product.price * self.quantity


class Cart:
    """Session-backed cart storing {product_id: quantity}."""

    def __init__(self, request):
        self.session = request.session
        self.data = {str(k): int(v) for k, v in self.session.get(SESSION_KEY, {}).items()}
        self._lines = None

    def _save(self):
        self.session[SESSION_KEY] = self.data
        self.session.modified = True
        self._lines = None

    def add(self, product, quantity=1):
        key = str(product.pk)
        self.set(product, self.data.get(key, 0) + quantity)

    def set(self, product, quantity):
        key = str(product.pk)
        quantity = min(int(quantity), product.stock)
        if quantity <= 0:
            self.data.pop(key, None)
        else:
            self.data[key] = quantity
        self._save()

    def remove(self, product_id):
        if self.data.pop(str(product_id), None) is not None:
            self._save()

    def clear(self):
        self.data = {}
        self._save()

    @property
    def lines(self):
        if self._lines is None:
            products = Product.objects.for_display().in_bulk(
                [int(pk) for pk in self.data]
            )
            self._lines = [
                CartLine(products[int(pk)], qty)
                for pk, qty in self.data.items()
                if int(pk) in products
            ]
        return self._lines

    def __iter__(self):
        return iter(self.lines)

    def __len__(self):
        return sum(self.data.values())

    def __bool__(self):
        return bool(self.data)

    @property
    def subtotal(self):
        return sum((line.line_total for line in self.lines), Decimal("0.00"))

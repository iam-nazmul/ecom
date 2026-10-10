from decimal import Decimal

from django.conf import settings
from django.contrib import messages
from django.contrib.humanize.templatetags.humanize import intcomma
from django.templatetags.static import static
from django.urls import reverse
from django.utils import timezone
from jinja2 import Environment


def url(viewname, *args, **kwargs):
    return reverse(viewname, args=args or None, kwargs=kwargs or None)


def money(value):
    if value is None or value == "":
        value = Decimal("0")
    amount = Decimal(value).quantize(Decimal("0.01"))
    sign = "-" if amount < 0 else ""
    return f"{sign}{settings.CURRENCY_SYMBOL}{intcomma(abs(amount))}"


def get_cart(request):
    from apps.cart.cart import Cart

    if not hasattr(request, "_cart"):
        request._cart = Cart(request)
    return request._cart


def query_with(request, **params):
    """Current query string with the given params replaced (None removes)."""
    query = request.GET.copy()
    for key, value in params.items():
        if value is None:
            query.pop(key, None)
        else:
            query[key] = value
    encoded = query.urlencode()
    return f"?{encoded}" if encoded else "?"


def percent(value):
    return "—" if value is None else f"{value:.1f}%"


def localtime(value, fmt="%b %d, %Y %H:%M"):
    return timezone.localtime(value).strftime(fmt)


def environment(**options):
    env = Environment(**options)
    env.globals.update(
        {
            "static": static,
            "url": url,
            "get_messages": messages.get_messages,
            "get_cart": get_cart,
            "query_with": query_with,
            "settings": settings,
            "now": timezone.now,
        }
    )
    env.filters.update({"money": money, "localtime": localtime, "percent": percent})
    return env

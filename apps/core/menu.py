"""A tiny registry for optional apps to add links to the header account menu.

Optional apps register links from their AppConfig.ready(); when such an app is not
installed nothing is registered and the shared templates never reference it.
"""
from dataclasses import dataclass

from django.urls import reverse


@dataclass(frozen=True)
class StaffLink:
    label: str
    url_name: str
    superuser_only: bool = False


_staff_links: list[StaffLink] = []


def register_staff_link(label, url_name, *, superuser_only=False):
    link = StaffLink(label, url_name, superuser_only)
    if link not in _staff_links:
        _staff_links.append(link)


def staff_links(user):
    """[(label, url)] the given user may see."""
    if not getattr(user, "is_staff", False):
        return []
    return [
        (link.label, reverse(link.url_name))
        for link in _staff_links
        if user.is_superuser or not link.superuser_only
    ]

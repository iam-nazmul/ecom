import re
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase, TestCase
from django.urls import NoReverseMatch, reverse

from apps.core.menu import staff_links

BASE = Path(settings.BASE_DIR)
# The only places allowed to mention an optional app: the switch and its URL include.
OPTIONAL_APPS = {"pos": {BASE / "config" / "settings.py", BASE / "config" / "urls.py"}}


class OptionalAppIsolationTests(SimpleTestCase):
    """Optional apps (e.g. POS) must be removable: nothing else may depend on them."""

    def test_no_code_outside_the_app_references_it(self):
        for app, allowed in OPTIONAL_APPS.items():
            app_dir = BASE / "apps" / app
            pattern = re.compile(rf"apps\.{app}\b|apps/{app}\b|['\"]{app}:|\b{app}_")
            offenders = []
            for path in [*BASE.glob("apps/**/*.py"), *BASE.glob("config/*.py"), *BASE.glob("template/**/*.html")]:
                if app_dir in path.parents or path in allowed or path.name == "tests.py" and path.parent.name == "core":
                    continue
                if pattern.search(path.read_text(encoding="utf-8")):
                    offenders.append(str(path.relative_to(BASE)))
            self.assertEqual(offenders, [], f"Code outside apps/{app} references it")


class OptionalAppSwitchTests(TestCase):
    def test_pos_routes_follow_the_flag(self):
        if settings.POS_ENABLED:
            self.assertEqual(reverse("pos:terminal"), "/pos/")
            self.assertIn("apps.pos", settings.INSTALLED_APPS)
        else:
            self.assertNotIn("apps.pos", settings.INSTALLED_APPS)
            with self.assertRaises(NoReverseMatch):
                reverse("pos:terminal")
            self.assertEqual(self.client.get("/pos/").status_code, 404)

    def test_staff_links_hidden_from_customers(self):
        class Customer:
            is_staff = False
            is_superuser = False

        self.assertEqual(staff_links(Customer()), [])

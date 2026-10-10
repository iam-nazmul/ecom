from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse


class AccountTests(TestCase):
    def test_signup_logs_in(self):
        response = self.client.post(
            reverse("accounts:signup"),
            {"username": "grace", "email": "grace@example.com",
             "password1": "s3cure-Passw0rd", "password2": "s3cure-Passw0rd"},
        )
        self.assertRedirects(response, reverse("core:home"))
        self.assertTrue(User.objects.filter(username="grace").exists())
        self.assertEqual(int(self.client.session["_auth_user_id"]), User.objects.get().pk)

    def test_login_and_logout(self):
        User.objects.create_user("grace", "g@example.com", "s3cure-Passw0rd")
        self.assertEqual(self.client.get(reverse("accounts:login")).status_code, 200)
        response = self.client.post(reverse("accounts:login"), {"username": "grace", "password": "s3cure-Passw0rd"})
        self.assertRedirects(response, reverse("core:home"))
        self.client.post(reverse("accounts:logout"))
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_orders_requires_login(self):
        response = self.client.get(reverse("accounts:orders"))
        self.assertRedirects(response, f"{reverse('accounts:login')}?next={reverse('accounts:orders')}")

    def test_404_page(self):
        response = self.client.get("/does-not-exist/")
        self.assertEqual(response.status_code, 404)


class NavbarTests(TestCase):
    """The header account menu reflects who is logged in and what they may open."""

    def header(self):
        html = self.client.get(reverse("core:home")).content.decode()
        return html[html.index("<header"):html.index("</header>")]

    def test_guest(self):
        header = self.header()
        self.assertIn(reverse("accounts:login"), header)
        self.assertIn(reverse("accounts:signup"), header)
        self.assertNotIn("data-account-menu", header)

    def test_customer(self):
        self.client.force_login(User.objects.create_user("grace", "g@example.com", "pw", first_name="Grace", last_name="Hopper"))
        header = self.header()
        self.assertIn("Grace Hopper", header)
        self.assertIn(reverse("accounts:orders"), header)
        for hidden in ("/admin/", reverse("inventory:dashboard"), reverse("inventory:profit_loss"), "Staff", "Superuser"):
            self.assertNotIn(hidden, header)

    def test_staff_without_admin_permissions(self):
        self.client.force_login(User.objects.create_user("nazmul", "n@example.com", "pw", is_staff=True))
        header = self.header()
        self.assertIn("nazmul", header)
        self.assertIn(">Staff<", header)
        self.assertIn(reverse("inventory:dashboard"), header)
        self.assertNotIn('href="/admin/"', header)
        self.assertNotIn(reverse("inventory:profit_loss"), header)

    def test_staff_with_admin_permissions(self):
        from django.contrib.auth.models import Permission
        user = User.objects.create_user("clerk", "c@example.com", "pw", is_staff=True)
        user.user_permissions.add(Permission.objects.get(codename="view_order"))
        self.client.force_login(user)
        self.assertIn('href="/admin/"', self.header())

    def test_superuser(self):
        self.client.force_login(User.objects.create_superuser("admin", "a@example.com", "pw"))
        header = self.header()
        self.assertIn(">Superuser<", header)
        for shown in ('href="/admin/"', reverse("inventory:dashboard"), reverse("inventory:profit_loss")):
            self.assertIn(shown, header)

    def test_logout_returns_to_guest_header(self):
        self.client.force_login(User.objects.create_user("grace", "g@example.com", "pw"))
        self.client.post(reverse("accounts:logout"))
        self.assertIn(reverse("accounts:login"), self.header())

from django.apps import AppConfig


class PosConfig(AppConfig):
    name = "apps.pos"
    verbose_name = "Point of sale"

    def ready(self):
        from apps.core.menu import register_staff_link

        register_staff_link("Point of sale", "pos:terminal")

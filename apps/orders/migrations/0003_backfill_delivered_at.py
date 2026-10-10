from django.db import migrations
from django.db.models import F


def backfill(apps, schema_editor):
    """Orders already delivered before inventory tracking: best known delivery date is updated_at."""
    Order = apps.get_model("orders", "Order")
    Order.objects.filter(status="delivered", delivered_at__isnull=True).update(delivered_at=F("updated_at"))


class Migration(migrations.Migration):
    dependencies = [("orders", "0002_cost_and_delivery")]

    operations = [migrations.RunPython(backfill, migrations.RunPython.noop)]

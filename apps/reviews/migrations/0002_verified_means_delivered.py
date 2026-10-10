from django.db import migrations
from django.db.models import Exists, OuterRef


def recompute(apps, schema_editor):
    """Verified purchase now means: the reviewer has a delivered order containing the product."""
    Review = apps.get_model("reviews", "Review")
    OrderItem = apps.get_model("orders", "OrderItem")
    delivered = OrderItem.objects.filter(
        order__user_id=OuterRef("user_id"), order__status="delivered", product_id=OuterRef("product_id")
    )
    Review.objects.update(verified_purchase=Exists(delivered))


class Migration(migrations.Migration):
    dependencies = [
        ("reviews", "0001_initial"),
        ("orders", "0001_initial"),
    ]

    operations = [migrations.RunPython(recompute, migrations.RunPython.noop)]

from django.db import migrations


def opening_balances(apps, schema_editor):
    """Start the ledger: one 'opening' row per product for the stock it already has.

    The cost of existing stock is unknown, so it is recorded at 0 — set real costs with an
    'Opening stock' adjustment or by receiving purchases.
    """
    Product = apps.get_model("catalog", "Product")
    StockMovement = apps.get_model("inventory", "StockMovement")
    StockMovement.objects.bulk_create(
        StockMovement(
            product=product, kind="opening", quantity=product.stock, unit_cost=product.average_cost,
            balance_after=product.stock, note="Opening balance (stock before inventory tracking)",
        )
        for product in Product.objects.filter(stock__gt=0, stock_movements__isnull=True)
    )


class Migration(migrations.Migration):
    dependencies = [("inventory", "0001_initial")]

    operations = [migrations.RunPython(opening_balances, migrations.RunPython.noop)]

from django.db import migrations


def convert(apps, schema_editor):
    """Turn each purchase's old free-typed amount_paid into a payment + allocation."""
    Purchase = apps.get_model("inventory", "Purchase")
    SupplierPayment = apps.get_model("inventory", "SupplierPayment")
    PaymentAllocation = apps.get_model("inventory", "PaymentAllocation")
    for purchase in Purchase.objects.filter(amount_paid__gt=0, allocations__isnull=True):
        day = purchase.received_at.date() if purchase.received_at else purchase.order_date
        payment = SupplierPayment.objects.create(
            supplier_id=purchase.supplier_id, date=day, amount=purchase.amount_paid, method="other",
            note="Recorded before payment tracking",
        )
        PaymentAllocation.objects.create(payment=payment, purchase=purchase, amount=purchase.amount_paid)


class Migration(migrations.Migration):
    dependencies = [("inventory", "0004_supplier_payments")]

    operations = [migrations.RunPython(convert, migrations.RunPython.noop)]

from django.db import migrations


def create_default_register(apps, schema_editor):
    Register = apps.get_model("pos", "Register")
    if not Register.objects.exists():
        Register.objects.create(name="Front counter", location="Shop floor")


class Migration(migrations.Migration):
    dependencies = [("pos", "0001_initial")]

    operations = [migrations.RunPython(create_default_register, migrations.RunPython.noop)]

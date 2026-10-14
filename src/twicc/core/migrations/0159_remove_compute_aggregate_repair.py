from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [("core", "0158_statistics_queue")]

    operations = [migrations.DeleteModel(name="ComputeAggregateRepair")]

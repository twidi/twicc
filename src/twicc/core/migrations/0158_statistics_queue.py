from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("core", "0157_compute_aggregate_repair")]

    operations = [
        migrations.AddField(
            model_name="session",
            name="items_cost_computed_through_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.CreateModel(
            name="StatisticsQueueEntry",
            fields=[
                ("id", models.BigAutoField(primary_key=True, serialize=False)),
                ("root_session_id", models.CharField(max_length=255)),
            ],
        ),
    ]

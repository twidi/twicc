from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("core", "0159_remove_compute_aggregate_repair")]

    operations = [
        migrations.AddField(
            model_name="statisticsqueueentry",
            name="force_cost_session_ids",
            field=models.JSONField(default=list, null=True),
        ),
    ]

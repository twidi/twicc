from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0156_content_storage_schema"),
    ]

    operations = [
        migrations.CreateModel(
            name="ComputeAggregateRepair",
            fields=[
                ("id", models.BigAutoField(primary_key=True, serialize=False)),
                ("session_id", models.CharField(max_length=255)),
                ("incarnation", models.CharField(max_length=36)),
                ("generation", models.PositiveBigIntegerField(default=0)),
                ("activity_keys", models.JSONField(default=list)),
                ("parent_ids", models.JSONField(default=list)),
            ],
            options={
                "constraints": [
                    models.UniqueConstraint(fields=["session_id", "incarnation"], name="compute_repair_source_identity"),
                ],
            },
        ),
    ]

from django.db import migrations


class Migration(migrations.Migration):
    """Drop the DeepSWE benchmark table: scores now come from a snapshot bundled in the frontend."""

    dependencies = [
        ("core", "0143_alter_mcpoperation_created_at"),
    ]

    operations = [
        migrations.DeleteModel(
            name="ModelBenchmark",
        ),
    ]

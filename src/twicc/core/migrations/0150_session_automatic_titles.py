from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0148_live_contribution_indexes_squashed_0149_remove_redundant_message_index"),
    ]

    operations = [
        migrations.AddField(
            model_name="session",
            name="title_origin",
            field=models.CharField(blank=True, default="", max_length=8),
        ),
        migrations.AddField(
            model_name="session",
            name="title_check_count",
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="session",
            name="title_checked_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]

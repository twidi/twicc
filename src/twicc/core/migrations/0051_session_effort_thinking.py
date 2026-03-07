from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0050_session_lifecycle_timestamps"),
    ]

    operations = [
        migrations.AddField(
            model_name="session",
            name="effort",
            field=models.CharField(max_length=10, null=True, default=None),
        ),
        migrations.AddField(
            model_name="session",
            name="thinking_enabled",
            field=models.BooleanField(null=True, default=None),
        ),
    ]

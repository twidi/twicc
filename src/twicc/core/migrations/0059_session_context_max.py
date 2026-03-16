from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0058_toolresultlink_replace_is_error_with_error"),
    ]

    operations = [
        migrations.AddField(
            model_name="session",
            name="context_max",
            field=models.PositiveIntegerField(default=200_000),
        ),
    ]

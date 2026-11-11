"""Remember the size of fully imported compressed Codex rollouts."""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("core", "0171_ui_message_provenance")]

    operations = [
        migrations.AddField(
            model_name="session", name="compressed_size",
            field=models.PositiveBigIntegerField(null=True),
        ),
    ]

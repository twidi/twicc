"""Retain peer error UI metadata without changing original English text."""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("core", "0170_timeline_workflow_kinds")]

    operations = [
        migrations.AddField(
            model_name="peermessage", name="error_ui_message",
            field=models.JSONField(blank=True, default=None, null=True),
        ),
    ]

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("core", "0140_external_mcp_oauth")]

    operations = [
        migrations.AddField(
            model_name="mcpoauthclient", name="source_hash",
            field=models.CharField(max_length=64, blank=True, db_index=True),
        ),
        migrations.AddField(
            model_name="mcpoauthrequest", name="source_hash",
            field=models.CharField(max_length=64, blank=True, db_index=True),
        ),
    ]

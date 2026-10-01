from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0148_live_contribution_indexes"),
    ]

    operations = [
        migrations.RemoveIndex(
            model_name="sessionitem",
            name="idx_item_message_line",
        ),
    ]

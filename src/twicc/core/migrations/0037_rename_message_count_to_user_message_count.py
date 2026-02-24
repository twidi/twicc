# Rename Session.message_count -> user_message_count and convert values:
# Old value counted user+assistant messages (user_count*2 or user_count*2-1).
# New value counts only user messages: ceil(old_value / 2).

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0036_reset_compute_version"),
    ]

    operations = [
        # 1. Rename the field
        migrations.RenameField(
            model_name="session",
            old_name="message_count",
            new_name="user_message_count",
        ),
        # 2. Data migration: convert old combined count to user-only count
        #    ceil(old / 2) = (old + 1) / 2 in integer division
        migrations.RunSQL(
            sql="UPDATE core_session SET user_message_count = (user_message_count + 1) / 2;",
            reverse_sql="UPDATE core_session SET user_message_count = user_message_count * 2;",
        ),
    ]

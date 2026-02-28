# Delete projects that have no sessions (sessions_count = 0).
# These are empty project folders left behind after Claude sublimates old sessions.

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0040_session_selected_model"),
    ]

    operations = [
        migrations.RunSQL(
            sql="DELETE FROM core_project WHERE sessions_count = 0;",
            reverse_sql=migrations.RunSQL.noop,
        ),
    ]

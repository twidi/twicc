# Reset compute_version to 45 to force recomputation of all sessions,
# and clear activity tables so they get rebuilt from scratch.

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0035_add_indexes_for_costs_and_activity"),
    ]

    operations = [
        migrations.RunSQL(
            sql="UPDATE core_session SET compute_version = 45;",
            reverse_sql=migrations.RunSQL.noop,
        ),
        migrations.RunSQL(
            sql="DELETE FROM core_dailyactivity;",
            reverse_sql=migrations.RunSQL.noop,
        ),
        migrations.RunSQL(
            sql="DELETE FROM core_weeklyactivity;",
            reverse_sql=migrations.RunSQL.noop,
        ),
    ]

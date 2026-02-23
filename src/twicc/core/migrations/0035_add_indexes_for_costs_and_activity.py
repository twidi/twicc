# Indexes to optimize activity recalculation queries:
# - Session(type, created_at, project): for session_count (type + date range, with or without project)
# - SessionItem(timestamp, kind): for user_message_count and cost queries (timestamp range + kind filter)

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0034_add_activity_session_count"),
    ]

    operations = [
        migrations.AlterField(
            model_name='sessionitem',
            name='timestamp',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddIndex(
            model_name='session',
            index=models.Index(condition=models.Q(('type', 'session')), fields=['created_at', 'project'], name='idx_session_by_date'),
        ),
        migrations.AddIndex(
            model_name='sessionitem',
            index=models.Index(condition=models.Q(('kind', 'user_message')), fields=['session', 'timestamp'], name='idx_item_user_session'),
        ),
        migrations.AddIndex(
            model_name='sessionitem',
            index=models.Index(condition=models.Q(('kind', 'user_message')), fields=['timestamp'], name='idx_item_user_all'),
        ),
        migrations.AddIndex(
            model_name='sessionitem',
            index=models.Index(condition=models.Q(('cost__isnull', False)), fields=['session', 'timestamp'], name='idx_item_cost_session'),
        ),
        migrations.AddIndex(
            model_name='sessionitem',
            index=models.Index(condition=models.Q(('cost__isnull', False)), fields=['timestamp'], name='idx_item_cost_all'),
        ),
    ]

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('core', '0160_statistics_queue_force_cost_sessions'),
    ]

    operations = [
        migrations.AddIndex(
            model_name='session',
            index=models.Index(
                fields=['provider', '-mtime', 'compute_version'],
                name='idx_session_compute_root',
                condition=models.Q(type='session', parent_session__isnull=True),
            ),
        ),
    ]

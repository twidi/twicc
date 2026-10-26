from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0163_timeline_event'),
    ]

    operations = [
        migrations.AddIndex(
            model_name='timelineevent',
            index=models.Index(
                condition=models.Q(('actor', 'external')), fields=['occurred_at', 'id'], name='idx_timeline_external',
            ),
        ),
    ]

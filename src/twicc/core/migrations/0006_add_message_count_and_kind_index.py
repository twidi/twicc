# Generated manually

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0005_add_session_title'),
    ]

    operations = [
        migrations.AddField(
            model_name='session',
            name='message_count',
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddIndex(
            model_name='sessionitem',
            index=models.Index(fields=['session', 'kind', 'line_num'], name='idx_session_kind_line'),
        ),
    ]

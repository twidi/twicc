from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('core', '0161_session_compute_root_index'),
    ]

    operations = [
        migrations.AddIndex(
            model_name='sessionitemcontent',
            index=models.Index(fields=['id'], name='idx_content_plain_id', condition=models.Q(codec='plain')),
        ),
    ]

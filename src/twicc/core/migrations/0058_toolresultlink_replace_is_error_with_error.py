# Generated manually

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0057_toolresultlink_is_error'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='toolresultlink',
            name='is_error',
        ),
        migrations.AddField(
            model_name='toolresultlink',
            name='error',
            field=models.TextField(blank=True, null=True),
        ),
    ]

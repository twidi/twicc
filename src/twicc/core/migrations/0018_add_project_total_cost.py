from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0017_alter_project_name'),
    ]

    operations = [
        migrations.AddField(
            model_name='project',
            name='total_cost',
            field=models.DecimalField(blank=True, decimal_places=6, max_digits=12, null=True),
        ),
    ]

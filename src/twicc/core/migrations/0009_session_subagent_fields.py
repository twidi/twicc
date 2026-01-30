# Generated manually for subagent support

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0008_add_cost_and_context_fields'),
    ]

    operations = [
        migrations.AddField(
            model_name='session',
            name='type',
            field=models.CharField(
                choices=[('session', 'Session'), ('subagent', 'Subagent')],
                db_index=True,
                default='session',
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name='session',
            name='parent_session',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name='subagents',
                to='core.session',
            ),
        ),
        migrations.AddField(
            model_name='session',
            name='agent_id',
            field=models.CharField(blank=True, max_length=255, null=True),
        ),
    ]

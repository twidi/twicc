# Generated manually on 2026-01-29

from decimal import Decimal

from django.db import migrations, models


def copy_total_cost_to_self_cost(apps, schema_editor):
    """Copy existing total_cost to self_cost and set subagents_cost to 0."""
    Session = apps.get_model('core', 'Session')
    # Update all sessions: self_cost = total_cost, subagents_cost = 0
    Session.objects.filter(total_cost__isnull=False).update(
        self_cost=models.F('total_cost'),
        subagents_cost=Decimal(0),
    )
    # For sessions without total_cost, just set subagents_cost to 0
    Session.objects.filter(total_cost__isnull=True).update(
        subagents_cost=Decimal(0),
    )


def reverse_copy(apps, schema_editor):
    """Reverse: nothing to do, fields will be dropped."""
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0009_session_subagent_fields'),
    ]

    operations = [
        # Add self_cost field
        migrations.AddField(
            model_name='session',
            name='self_cost',
            field=models.DecimalField(blank=True, decimal_places=6, max_digits=10, null=True),
        ),
        # Add subagents_cost field
        migrations.AddField(
            model_name='session',
            name='subagents_cost',
            field=models.DecimalField(blank=True, decimal_places=6, max_digits=10, null=True),
        ),
        # Copy existing data
        migrations.RunPython(copy_total_cost_to_self_cost, reverse_copy),
    ]

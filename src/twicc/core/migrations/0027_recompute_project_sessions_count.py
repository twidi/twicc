# Generated manually on 2026-02-22

from django.db import migrations


def recompute_project_metadata(apps, schema_editor):
    """Recompute sessions_count and mtime for all projects, including stale sessions."""
    Project = apps.get_model('core', 'Project')
    Session = apps.get_model('core', 'Session')
    for project in Project.objects.all():
        sessions = Session.objects.filter(
            project=project,
            last_line__gt=0,
            type='session',
        )
        project.sessions_count = sessions.count()
        max_mtime = sessions.order_by("-mtime").values_list("mtime", flat=True).first()
        project.mtime = max_mtime or 0
        project.save(update_fields=['sessions_count', 'mtime'])


def reverse_noop(apps, schema_editor):
    """Reverse: nothing to do."""
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0026_project_git_root'),
    ]

    operations = [
        migrations.RunPython(recompute_project_metadata, reverse_noop),
    ]

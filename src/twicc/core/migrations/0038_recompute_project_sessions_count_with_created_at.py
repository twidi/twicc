# Recompute sessions_count for all projects using the new rule:
# only count sessions with type=SESSION and created_at IS NOT NULL.

from django.db import migrations


def recompute_sessions_count(apps, schema_editor):
    Project = apps.get_model("core", "Project")
    Session = apps.get_model("core", "Session")
    for project in Project.objects.all():
        project.sessions_count = Session.objects.filter(
            project=project,
            type="session",
            created_at__isnull=False,
        ).count()
        project.save(update_fields=["sessions_count"])


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0037_rename_message_count_to_user_message_count"),
    ]

    operations = [
        migrations.RunPython(recompute_sessions_count, migrations.RunPython.noop),
    ]

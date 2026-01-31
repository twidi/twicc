"""Add index on Session (project, -mtime) for paginated queries."""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0014_add_project_directory"),
    ]

    operations = [
        migrations.AddIndex(
            model_name="session",
            index=models.Index(
                fields=["project", "-mtime"],
                name="idx_session_project_mtime",
            ),
        ),
    ]

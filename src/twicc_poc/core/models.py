from django.db import models


class Project(models.Model):
    """A project corresponds to a subfolder of ~/.claude/projects/"""

    id = models.CharField(max_length=255, primary_key=True)
    sessions_count = models.PositiveIntegerField(default=0)
    mtime = models.FloatField(default=0)
    archived = models.BooleanField(default=False)

    class Meta:
        ordering = ["-mtime"]

    def __str__(self):
        return self.id


class Session(models.Model):
    """A session corresponds to a *.jsonl file (excluding agent-*.jsonl)"""

    id = models.CharField(max_length=255, primary_key=True)
    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name="sessions",
    )
    last_offset = models.PositiveBigIntegerField(default=0)
    last_line = models.PositiveIntegerField(default=0)
    mtime = models.FloatField(default=0)
    archived = models.BooleanField(default=False)

    class Meta:
        ordering = ["-mtime"]

    def __str__(self):
        return self.id


class SessionItem(models.Model):
    """A session item corresponds to a single line in a JSONL file"""

    id = models.BigAutoField(primary_key=True)
    session = models.ForeignKey(
        Session,
        on_delete=models.CASCADE,
        related_name="items",
    )
    line_num = models.PositiveIntegerField()
    content = models.TextField()

    class Meta:
        ordering = ["line_num"]
        constraints = [
            models.UniqueConstraint(
                fields=["session", "line_num"],
                name="unique_session_line",
            )
        ]

    def __str__(self):
        return f"{self.session_id}:{self.line_num}"

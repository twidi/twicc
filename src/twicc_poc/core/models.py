from enum import IntEnum

from django.db import models


class DisplayLevel(IntEnum):
    """Display level for session items, determining visibility in different modes."""
    ALWAYS = 1       # Always shown in all modes
    COLLAPSIBLE = 2  # Shown in Normal, grouped in Simplified
    DEBUG_ONLY = 3   # Only shown in Debug mode


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
    compute_version = models.PositiveIntegerField(null=True, blank=True)  # NULL = never computed

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
    line_num = models.PositiveIntegerField()  # 1-based (first line = 1)
    content = models.TextField()

    # Display metadata fields
    display_level = models.PositiveSmallIntegerField(null=True, blank=True)  # DisplayLevel enum value
    group_head = models.PositiveIntegerField(null=True, blank=True, db_index=True)  # line_num of group start
    group_tail = models.PositiveIntegerField(null=True, blank=True)  # line_num of group end

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

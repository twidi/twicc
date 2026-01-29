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
    compute_version = models.PositiveIntegerField(null=True, blank=True)  # NULL = never computed
    title = models.CharField(max_length=250, null=True, blank=True)  # Session title (from first user message or custom-title)

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
    display_level = models.PositiveSmallIntegerField(null=True, blank=True)  # ItemDisplayLevel enum value
    group_head = models.PositiveIntegerField(null=True, blank=True, db_index=True)  # line_num of group start
    group_tail = models.PositiveIntegerField(null=True, blank=True)  # line_num of group end
    kind = models.CharField(max_length=50, null=True, blank=True)  # Item category/type

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


class SessionItemLink(models.Model):
    """
    Links between session items (e.g. tool_use → tool_result).

    Generic link table: source_line_num is the "origin" item,
    target_line_num is the related item, link_type describes the
    relationship, and reference provides context (e.g. the tool_use_id).
    """

    session = models.ForeignKey(
        Session,
        on_delete=models.CASCADE,
        related_name="item_links",
    )
    source_line_num = models.PositiveIntegerField()  # e.g. the line with tool_use(s)
    target_line_num = models.PositiveIntegerField()  # e.g. the line with tool_result
    link_type = models.CharField(max_length=50)  # e.g. "tool_result"
    reference = models.CharField(max_length=255)  # e.g. the tool_use_id

    class Meta:
        indexes = [
            models.Index(
                fields=["session", "source_line_num", "link_type", "reference"],
                name="idx_item_link_lookup",
            ),
        ]

    def __str__(self):
        return f"{self.session_id}:{self.source_line_num}->{self.target_line_num} ({self.link_type})"

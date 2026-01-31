from datetime import date
from decimal import Decimal

from django.db import models


class SessionType(models.TextChoices):
    """Type of session entry."""
    SESSION = "session", "Session"
    SUBAGENT = "subagent", "Subagent"


# Default prices per family (USD per million tokens) - fallback when no DB price exists
# Based on Anthropic's published pricing as of January 2026
DEFAULT_FAMILY_PRICES = {
    "opus": {
        "input_price": Decimal("15.00"),
        "output_price": Decimal("75.00"),
        "cache_read_price": Decimal("1.50"),
        "cache_write_5m_price": Decimal("18.75"),
        "cache_write_1h_price": Decimal("30.00"),
    },
    "sonnet": {
        "input_price": Decimal("3.00"),
        "output_price": Decimal("15.00"),
        "cache_read_price": Decimal("0.30"),
        "cache_write_5m_price": Decimal("3.75"),
        "cache_write_1h_price": Decimal("6.00"),
    },
    "haiku": {
        "input_price": Decimal("1.00"),
        "output_price": Decimal("5.00"),
        "cache_read_price": Decimal("0.10"),
        "cache_write_5m_price": Decimal("1.25"),
        "cache_write_1h_price": Decimal("2.00"),
    },
}


class Project(models.Model):
    """A project corresponds to a subfolder of ~/.claude/projects/"""

    id = models.CharField(max_length=255, primary_key=True)
    directory = models.CharField(max_length=500, null=True, blank=True)  # Actual filesystem path (from session cwd)
    sessions_count = models.PositiveIntegerField(default=0)
    mtime = models.FloatField(default=0)
    archived = models.BooleanField(default=False)
    name = models.CharField(max_length=25, null=True, blank=True, unique=True)  # User-defined display name
    color = models.CharField(max_length=50, null=True, blank=True)  # CSS color value (hex, hsl, etc.)

    class Meta:
        ordering = ["-mtime"]

    def __str__(self):
        return self.id


class Session(models.Model):
    """A session corresponds to a *.jsonl file, or a subagent file in {session_id}/subagents/"""

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
    message_count = models.PositiveIntegerField(default=0)  # Number of user/assistant messages (user_count * 2 - 1 if last is user)

    # Cost and context usage fields (computed from items)
    context_usage = models.PositiveIntegerField(null=True, blank=True)  # Current context usage (last known value in tokens)
    self_cost = models.DecimalField(max_digits=10, decimal_places=6, null=True, blank=True)  # Own items cost in USD
    subagents_cost = models.DecimalField(max_digits=10, decimal_places=6, null=True, blank=True)  # Sum of subagents total_cost
    total_cost = models.DecimalField(max_digits=10, decimal_places=6, null=True, blank=True)  # self_cost + subagents_cost

    # Subagent-related fields
    type = models.CharField(
        max_length=20,
        choices=SessionType.choices,
        default=SessionType.SESSION,
        db_index=True,
    )
    parent_session = models.ForeignKey(
        'self',
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='subagents',
    )
    agent_id = models.CharField(max_length=255, null=True, blank=True)  # Short agent ID from filename (e.g., "a6c7d21")

    # Runtime environment fields (last known values from JSONL)
    cwd = models.CharField(max_length=500, null=True, blank=True)  # Current working directory
    git_branch = models.CharField(max_length=255, null=True, blank=True)  # Git branch name
    model = models.CharField(max_length=100, null=True, blank=True)  # Model name (e.g., "claude-opus-4-5-20251101")

    class Meta:
        ordering = ["-mtime"]
        indexes = [
            models.Index(fields=["project", "-mtime"], name="idx_session_project_mtime"),
        ]

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

    # Cost and usage fields (from API response)
    message_id = models.CharField(max_length=100, null=True, blank=True, db_index=True)  # API message ID for deduplication
    cost = models.DecimalField(max_digits=10, decimal_places=6, null=True, blank=True)  # Line cost in USD (null if no usage or duplicate message_id)
    context_usage = models.PositiveIntegerField(null=True, blank=True)  # Total tokens at this point (null if no usage)

    class Meta:
        ordering = ["line_num"]
        constraints = [
            models.UniqueConstraint(
                fields=["session", "line_num"],
                name="unique_session_line",
            )
        ]
        indexes = [
            models.Index(
                fields=["session", "kind", "line_num"],
                name="idx_session_kind_line",
            ),
        ]

    def __str__(self):
        return f"{self.session_id}:{self.line_num}"


class SessionItemLink(models.Model):
    """
    Links between session items (e.g. tool_use → tool_result, tool_use → agent).

    Generic link table: source_line_num is the "origin" item,
    target_line_num is the related item (nullable for agent links),
    link_type describes the relationship, and reference provides context.

    Link types:
    - "tool_result": links a tool_use to its tool_result (target_line_num = result line)
    - "agent": links a Task tool_use to its agent (target_line_num = null, reference = agent_id)
    """

    session = models.ForeignKey(
        Session,
        on_delete=models.CASCADE,
        related_name="item_links",
    )
    source_line_num = models.PositiveIntegerField()  # e.g. the line with tool_use(s)
    target_line_num = models.PositiveIntegerField(null=True, blank=True)  # e.g. the line with tool_result (null for agent links)
    link_type = models.CharField(max_length=50)  # e.g. "tool_result", "agent"
    reference = models.CharField(max_length=255)  # e.g. the tool_use_id or agent_id

    class Meta:
        indexes = [
            models.Index(
                fields=["session", "link_type", "reference", "source_line_num"],
                name="idx_item_link_lookup",
            ),
        ]

    def __str__(self):
        return f"{self.session_id}:{self.source_line_num}->{self.target_line_num} ({self.link_type})"


class ModelPrice(models.Model):
    """
    Stores pricing information for AI models, with support for historical prices.

    Prices are stored per model_id (OpenRouter format, e.g. "anthropic/claude-opus-4.5")
    with an effective_date to support historical price lookups. A new entry is only
    created when prices change, not daily.
    """

    # OpenRouter model ID (e.g. "anthropic/claude-opus-4.5")
    model_id = models.CharField(max_length=100)

    # Date from which this price is effective
    effective_date = models.DateField()

    # Prices in USD per million tokens
    input_price = models.DecimalField(max_digits=12, decimal_places=6)
    output_price = models.DecimalField(max_digits=12, decimal_places=6)
    cache_read_price = models.DecimalField(max_digits=12, decimal_places=6)
    cache_write_5m_price = models.DecimalField(max_digits=12, decimal_places=6)
    cache_write_1h_price = models.DecimalField(max_digits=12, decimal_places=6)

    # When this record was created
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [["model_id", "effective_date"]]
        indexes = [
            models.Index(fields=["model_id", "-effective_date"]),
        ]

    def __str__(self):
        return f"{self.model_id} ({self.effective_date})"

    @classmethod
    def _extract_family_and_version(cls, model_id: str) -> tuple[str | None, str | None]:
        """
        Extract family and version from model_id.

        Example: "anthropic/claude-opus-4.5" -> ("opus", "4.5")
        """
        if not model_id.startswith("anthropic/claude-"):
            return None, None

        # Remove prefix: "anthropic/claude-opus-4.5" -> "opus-4.5"
        suffix = model_id.removeprefix("anthropic/claude-")
        parts = suffix.split("-")

        if not parts:
            return None, None

        family = parts[0]  # "opus", "sonnet", "haiku"
        version = parts[1] if len(parts) > 1 else None

        return family, version

    @classmethod
    def get_price_for_date(cls, model_id: str, target_date: date) -> "ModelPrice | None":
        """
        Retrieve the applicable price for a model at a given date.

        Fallback chain:
        1. Exact model_id with effective_date <= target_date
        2. Exact model_id with oldest known price (for old messages)
        3. Same family, lower version (e.g., opus-4.5 if opus-4.7 not found)
        4. Same family, higher version (e.g., opus-5.0 if no lower version)
        5. None (caller should use DEFAULT_FAMILY_PRICES)
        """
        # 1. Try to find price valid at target_date for exact model
        price = cls.objects.filter(
            model_id=model_id,
            effective_date__lte=target_date,
        ).order_by("-effective_date").first()

        if price:
            return price

        # 2. Fallback: oldest known price for exact model
        price = cls.objects.filter(
            model_id=model_id,
        ).order_by("effective_date").first()

        if price:
            return price

        # 3 & 4. Try other versions of the same family
        family, version = cls._extract_family_and_version(model_id)
        if not family:
            return None

        # Get all prices for this family, ordered by version descending
        family_prefix = f"anthropic/claude-{family}-"
        family_prices = list(
            cls.objects.filter(
                model_id__startswith=family_prefix,
            ).order_by("-effective_date").values_list("model_id", flat=True).distinct()
        )

        if not family_prices:
            return None

        # Sort by version to find lower/higher versions
        def extract_version(mid: str) -> tuple[int, ...]:
            """Extract version tuple for sorting: '4.5' -> (4, 5)"""
            v = mid.removeprefix(family_prefix)
            # Handle suffixes like ":thinking"
            v = v.split(":")[0]
            try:
                return tuple(int(x) for x in v.split("."))
            except ValueError:
                return (0,)

        if version:
            try:
                target_version = tuple(int(x) for x in version.split("."))
            except ValueError:
                target_version = (0,)

            # Find lower version first, then higher
            lower_versions = [m for m in family_prices if extract_version(m) < target_version]
            higher_versions = [m for m in family_prices if extract_version(m) > target_version]

            # Sort: lower versions descending (closest first), higher ascending (closest first)
            lower_versions.sort(key=extract_version, reverse=True)
            higher_versions.sort(key=extract_version)

            # Try lower version first, then higher
            for fallback_model_id in lower_versions + higher_versions:
                price = cls.objects.filter(
                    model_id=fallback_model_id,
                ).order_by("-effective_date").first()
                if price:
                    return price

        # 5. No price found at all
        return None

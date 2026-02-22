from datetime import date, datetime, timedelta, timezone
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
    git_root = models.CharField(max_length=500, null=True, blank=True)  # Resolved git root directory (walking up from directory)
    sessions_count = models.PositiveIntegerField(default=0)
    mtime = models.FloatField(default=0)
    stale = models.BooleanField(default=False)  # True if folder/file no longer exists on disk
    name = models.CharField(max_length=25, null=True, blank=True, unique=True)  # User-defined display name
    color = models.CharField(max_length=50, null=True, blank=True)  # CSS color value (hex, hsl, etc.)
    total_cost = models.DecimalField(max_digits=12, decimal_places=6, null=True, blank=True)  # Sum of all sessions total_cost in USD

    class Meta:
        ordering = ["-mtime"]

    def __str__(self):
        return self.id


class WeeklyActivity(models.Model):
    """Pre-computed weekly user message count, per project and global.

    Each row stores the count of user_message items for a given week (Monday).
    project=NULL means global (all projects combined).
    Incremented live by sync.py when new user messages are detected.
    """

    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name="weekly_activities",
        null=True,
        blank=True,  # NULL = global (all projects)
    )
    week = models.DateField()  # Monday of the ISO week
    count = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["project", "week"],
                name="unique_project_week",
            ),
        ]

    def __str__(self):
        label = self.project_id or "global"
        return f"{label} / {self.week} = {self.count}"


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
    stale = models.BooleanField(default=False)  # True if folder/file no longer exists on disk
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
    cwd_git_branch = models.CharField(max_length=255, null=True, blank=True)  # Git branch from cwd (unreliable for worktrees)
    model = models.CharField(max_length=100, null=True, blank=True)  # Model name (e.g., "claude-opus-4-5-20251101")

    # Resolved git directory and branch (from filesystem analysis of tool_use paths)
    git_directory = models.CharField(max_length=500, null=True, blank=True)  # Resolved git root directory
    git_branch = models.CharField(max_length=255, null=True, blank=True)  # Resolved branch name (or commit hash for detached HEAD)

    # User-controlled fields
    archived = models.BooleanField(default=False)  # User can archive sessions to hide them from default list
    pinned = models.BooleanField(default=False)  # User can pin sessions to keep them at the top of the list

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

    # Timestamp from JSONL line (stored as datetime in UTC)
    timestamp = models.DateTimeField(null=True, blank=True, db_index=True)

    # Resolved git directory and branch (from filesystem analysis of tool_use paths)
    git_directory = models.CharField(max_length=500, null=True, blank=True)  # Resolved git root directory
    git_branch = models.CharField(max_length=255, null=True, blank=True)  # Resolved branch name (or commit hash for detached HEAD)

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


class ToolResultLink(models.Model):
    """Links a tool_use to its tool_result within a session."""

    session = models.ForeignKey(
        Session,
        on_delete=models.CASCADE,
        related_name="tool_result_links",
    )
    tool_use_line_num = models.PositiveIntegerField()  # Line containing the tool_use
    tool_result_line_num = models.PositiveIntegerField()  # Line containing the tool_result
    tool_use_id = models.CharField(max_length=255)  # The tool_use ID

    class Meta:
        indexes = [
            models.Index(
                fields=["session", "tool_use_line_num", "tool_use_id"],
                name="idx_tool_result_link_lookup",
            ),
        ]

    def __str__(self):
        return f"{self.session_id}:{self.tool_use_line_num}->{self.tool_result_line_num} ({self.tool_use_id})"


class AgentLink(models.Model):
    """Links a Task tool_use to its spawned subagent within a session."""

    session = models.ForeignKey(
        Session,
        on_delete=models.CASCADE,
        related_name="agent_links",
    )
    tool_use_line_num = models.PositiveIntegerField()  # Line containing the assistant message with Task tool_use
    tool_use_id = models.CharField(max_length=255)  # The specific Task tool_use ID
    agent_id = models.CharField(max_length=255)  # The subagent ID

    class Meta:
        indexes = [
            models.Index(
                fields=["session", "tool_use_id"],
                name="idx_agent_link_lookup",
            ),
        ]

    def __str__(self):
        return f"{self.session_id}:{self.tool_use_line_num} -> agent {self.agent_id} ({self.tool_use_id})"


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


class UsageSnapshot(models.Model):
    """
    A point-in-time snapshot of Claude Code usage quotas.

    Fetched from the Anthropic OAuth usage API endpoint.
    Each row represents one API call, storing both parsed fields
    and the raw JSON response for investigation purposes.
    """

    # When the API was called
    fetched_at = models.DateTimeField()

    # Raw JSON response for future-proofing and investigation
    raw_response = models.JSONField()

    # Five-hour rolling window quota (null if not yet used in this window)
    five_hour_utilization = models.FloatField(null=True, blank=True)
    five_hour_resets_at = models.DateTimeField(null=True, blank=True)

    # Seven-day rolling window quota - global (null if not yet used in this window)
    seven_day_utilization = models.FloatField(null=True, blank=True)
    seven_day_resets_at = models.DateTimeField(null=True, blank=True)

    # Seven-day per-model quotas (null means no specific limit for that model)
    seven_day_opus_utilization = models.FloatField(null=True, blank=True)
    seven_day_opus_resets_at = models.DateTimeField(null=True, blank=True)
    seven_day_sonnet_utilization = models.FloatField(null=True, blank=True)
    seven_day_sonnet_resets_at = models.DateTimeField(null=True, blank=True)

    # Seven-day other quotas (null if not applicable)
    seven_day_oauth_apps_utilization = models.FloatField(null=True, blank=True)
    seven_day_oauth_apps_resets_at = models.DateTimeField(null=True, blank=True)
    seven_day_cowork_utilization = models.FloatField(null=True, blank=True)
    seven_day_cowork_resets_at = models.DateTimeField(null=True, blank=True)

    # Extra usage (default False if the block is absent)
    extra_usage_is_enabled = models.BooleanField(default=False)
    extra_usage_monthly_limit = models.IntegerField(null=True, blank=True)
    extra_usage_used_credits = models.FloatField(null=True, blank=True)
    extra_usage_utilization = models.FloatField(null=True, blank=True)

    class Meta:
        ordering = ["-fetched_at"]
        indexes = [
            models.Index(fields=["-fetched_at"], name="idx_usage_snapshot_fetched"),
        ]

    def __str__(self):
        return f"UsageSnapshot {self.fetched_at.isoformat()}"

    # --- Computed properties ---

    @staticmethod
    def _temporal_pct(fetched_at: datetime, resets_at: datetime, window: timedelta) -> float:
        """Calculate how far through a time window we are, as a percentage (0-100)."""
        window_start = resets_at - window
        elapsed = (fetched_at - window_start).total_seconds()
        total = window.total_seconds()
        if total <= 0:
            return 0.0
        return max(0.0, min(100.0, (elapsed / total) * 100.0))

    @property
    def five_hour_temporal_pct(self) -> float | None:
        if self.five_hour_resets_at is None:
            return None
        return self._temporal_pct(self.fetched_at, self.five_hour_resets_at, timedelta(hours=5))

    @property
    def seven_day_temporal_pct(self) -> float | None:
        if self.seven_day_resets_at is None:
            return None
        return self._temporal_pct(self.fetched_at, self.seven_day_resets_at, timedelta(days=7))

    @property
    def seven_day_opus_temporal_pct(self) -> float | None:
        if self.seven_day_opus_resets_at is None:
            return None
        return self._temporal_pct(self.fetched_at, self.seven_day_opus_resets_at, timedelta(days=7))

    @property
    def seven_day_sonnet_temporal_pct(self) -> float | None:
        if self.seven_day_sonnet_resets_at is None:
            return None
        return self._temporal_pct(self.fetched_at, self.seven_day_sonnet_resets_at, timedelta(days=7))

    @staticmethod
    def _burn_rate(utilization: float | None, temporal_pct: float | None) -> float | None:
        """
        Ratio of utilization to temporal progress.

        > 1.0 means on track to exhaust quota before reset.
        """
        if utilization is None or temporal_pct is None or temporal_pct <= 0:
            return None
        return utilization / temporal_pct

    @property
    def five_hour_burn_rate(self) -> float | None:
        return self._burn_rate(self.five_hour_utilization, self.five_hour_temporal_pct)

    @property
    def seven_day_burn_rate(self) -> float | None:
        return self._burn_rate(self.seven_day_utilization, self.seven_day_temporal_pct)

    @property
    def five_hour_started_at(self) -> datetime | None:
        if self.five_hour_resets_at is None:
            return None
        return self.five_hour_resets_at - timedelta(hours=5)

    @property
    def seven_day_started_at(self) -> datetime | None:
        if self.seven_day_resets_at is None:
            return None
        return self.seven_day_resets_at - timedelta(days=7)

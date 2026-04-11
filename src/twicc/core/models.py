from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from functools import cached_property
from typing import ClassVar, TypeAlias

from django.db import models

import logging

from django.db.models import Q

from twicc.core.enums import ItemKind

logger = logging.getLogger(__name__)


def cron_occurrences(cron_expr: str, from_dt: datetime):
    """Iterate cron occurrences from a given datetime.

    CronSim expects local time (cron expressions are in local time), so this function
    converts the input datetime from UTC to local time before passing it to CronSim,
    then converts each result back to UTC.

    Args:
        cron_expr: A 5-field cron expression (e.g., "0 9 * * *")
        from_dt: Start datetime (UTC). Occurrences will be after this time.

    Yields:
        datetime objects in UTC for each cron occurrence.
    """
    from cronsim import CronSim

    local_from = from_dt.astimezone()  # Convert UTC → local
    for occurrence in CronSim(cron_expr, local_from):
        yield occurrence.astimezone(timezone.utc)

DateType: TypeAlias = date


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
    archived = models.BooleanField(default=False)  # User can archive projects to hide from default list
    total_cost = models.DecimalField(max_digits=12, decimal_places=6, null=True, blank=True)  # Sum of all sessions total_cost in USD

    class Meta:
        ordering = ["-mtime"]

    def recalculate_total_cost(self) -> None:
        """Recalculate total_cost from non-stale SESSION-type sessions."""
        from decimal import Decimal
        from django.db.models import Sum

        total = self.sessions.filter(
            type=SessionType.SESSION,
            total_cost__isnull=False,
        ).aggregate(total=Sum("total_cost"))["total"] or Decimal(0)
        self.total_cost = total if total > 0 else None

    def __str__(self):
        return self.id


class PeriodicActivity(models.Model):
    """Pre-computed periodic activity metrics, per project and global.

    Each row stores the data for a given date (Monday for week, for example)
    `project=NULL` means global (all projects combined).
    """

    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name="%(class)s_set",
        null=True,
        blank=True,  # NULL = global (all projects)
    )
    date = models.DateField()
    user_message_count = models.PositiveIntegerField(default=0)
    session_count = models.PositiveIntegerField(default=0)
    cost = models.DecimalField(max_digits=12, decimal_places=6, default=0)

    class Meta:
        abstract = True
        constraints = [
            models.UniqueConstraint(
                fields=["project", "date"],
                name="unique_project_%(class)s",
            ),
        ]

    @classmethod
    def date_range(cls, activity_date: DateType) -> tuple[datetime, datetime]:
        """Return the (start, end) datetime range for the given activity date.

        Must be overridden by subclasses to define the period length.
        """
        raise NotImplementedError

    @classmethod
    def recalculate(cls, project_id: str | None, activity_date: DateType) -> None:
        """Recalculate all activity counters for a project+date from the database.

        Queries SessionItem and Session to compute accurate values, then
        sets them via update_or_create (idempotent).

        Args:
            project_id: The project ID, or None for global (all projects).
            activity_date: The date (day for DailyActivity, Monday for WeeklyActivity).
        """
        from django.db.models import Q, Sum

        from twicc.core.enums import ItemKind

        date_start, date_end = cls.date_range(activity_date)

        # Project filter for items (via session FK) and sessions
        item_project_filter = Q(session__project_id=project_id) if project_id is not None else Q()
        session_project_filter = Q(project_id=project_id) if project_id is not None else Q()

        # user_message_count: only from type=SESSION sessions
        user_message_count = SessionItem.objects.filter(
            item_project_filter,
            kind=ItemKind.USER_MESSAGE,
            timestamp__gte=date_start,
            timestamp__lt=date_end,
            session__type=SessionType.SESSION,
        ).count()

        # cost: from ALL session types
        cost = SessionItem.objects.filter(
            item_project_filter,
            cost__isnull=False,
            timestamp__gte=date_start,
            timestamp__lt=date_end,
        ).aggregate(total=Sum("cost"))["total"] or Decimal(0)

        # session_count: only type=SESSION sessions with at least one user message
        session_count = Session.objects.filter(
            session_project_filter,
            type=SessionType.SESSION,
            created_at__gte=date_start,
            created_at__lt=date_end,
            user_message_count__gt=0,
        ).count()

        # If all values are zero, delete the row to keep the table clean
        if user_message_count == 0 and cost == 0 and session_count == 0:
            cls.objects.filter(project_id=project_id, date=activity_date).delete()
            return

        cls.objects.update_or_create(
            project_id=project_id,
            date=activity_date,
            defaults={
                "user_message_count": user_message_count,
                "session_count": session_count,
                "cost": cost,
            },
        )

    @staticmethod
    def recalculate_for_days(project_id: str, days: set, do_global: bool = True) -> None:
        """Recalculate daily and weekly activity for the given days.

        Deduces which weeks (Mondays) are affected from the days,
        then recalculates both project-specific and global rows
        by querying SessionItem and Session from the database.

        Args:
            project_id: The project ID.
            days: Set of date objects representing affected days.
            do_global: Whether to recalculate global rows.
        """
        if not days:
            return
        mondays = {day - timedelta(days=day.weekday()) for day in days}
        for day in days:
            DailyActivity.recalculate(project_id, day)
            if do_global:
                DailyActivity.recalculate(None, day)
        for monday in mondays:
            WeeklyActivity.recalculate(project_id, monday)
            if do_global:
                WeeklyActivity.recalculate(None, monday)

    def __str__(self):
        label = self.project_id or "global"
        return f"{label} / {self.date} = {self.user_message_count}"


class WeeklyActivity(PeriodicActivity):
    """Pre-computed weekly activity metrics (messages, sessions, costs), per project and global."""

    @classmethod
    def date_range(cls, activity_date: DateType) -> tuple[datetime, datetime]:
        """Weekly range: Monday 00:00 UTC to next Monday 00:00 UTC."""
        date_start = datetime.combine(activity_date, datetime.min.time(), tzinfo=timezone.utc)
        return date_start, date_start + timedelta(days=7)


class DailyActivity(PeriodicActivity):
    """Pre-computed daily activity metrics (messages, sessions, costs), per project and global."""

    @classmethod
    def date_range(cls, activity_date: DateType) -> tuple[datetime, datetime]:
        """Daily range: day 00:00 UTC to next day 00:00 UTC."""
        date_start = datetime.combine(activity_date, datetime.min.time(), tzinfo=timezone.utc)
        return date_start, date_start + timedelta(days=1)


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
    search_version = models.PositiveIntegerField(null=True, blank=True)
    title = models.CharField(max_length=250, null=True, blank=True)  # Session title (from first user message or custom-title)
    user_message_count = models.PositiveIntegerField(default=0)  # Number of user messages (message turns)

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
    slug = models.CharField(max_length=255, null=True, blank=True)  # Last session slug (e.g., "gleaming-marinating-twilight")

    # Session creation timestamp (from first JSONL item with a timestamp)
    created_at = models.DateTimeField(null=True, blank=True)

    # Resolved git directory and branch (from filesystem analysis of tool_use paths)
    git_directory = models.CharField(max_length=500, null=True, blank=True)  # Resolved git root directory
    git_branch = models.CharField(max_length=255, null=True, blank=True)  # Resolved branch name (or commit hash for detached HEAD)

    # Lifecycle timestamps (datetime, unlike mtime which is a raw float for the JSONL file)
    last_started_at = models.DateTimeField(null=True, blank=True)  # Last time the session was started or resumed
    last_updated_at = models.DateTimeField(null=True, blank=True)  # Last time meaningful content was added
    last_stopped_at = models.DateTimeField(null=True, blank=True)  # Last time the session process stopped (our processes only)

    # Content tracking (for "unread" detection)
    last_new_content_at = models.DateTimeField(null=True, blank=True)  # Last time an assistant message was synced
    last_viewed_at = models.DateTimeField(null=True, blank=True)  # Last time the user viewed this session

    # User-controlled fields
    archived = models.BooleanField(default=False)  # User can archive sessions to hide them from default list
    pinned = models.BooleanField(default=False)  # User can pin sessions to keep them at the top of the list

    # Permission mode for the Claude SDK (e.g., "default", "acceptEdits", "plan", "bypassPermissions")
    permission_mode = models.CharField(max_length=30, null=True, default=None)
    # User-selected model for the Claude SDK (e.g., "opus", "sonnet", "haiku")
    selected_model = models.CharField(max_length=30, null=True, default=None)
    # Effort level for thinking depth (e.g., "low", "medium", "high", "max")
    effort = models.CharField(max_length=10, null=True, default=None)
    # Whether extended thinking is enabled (True=adaptive, False=disabled)
    thinking_enabled = models.BooleanField(null=True, default=None)
    # Whether the built-in Chrome MCP (Claude in Chrome) is activated for this session
    # NULL = use global default, explicit value = forced for this session
    claude_in_chrome = models.BooleanField(null=True, default=None)
    # Maximum context window size in tokens (200_000 = default 200K, 1_000_000 = extended 1M)
    # NULL = use global default, explicit value = forced for this session
    context_max = models.PositiveIntegerField(null=True, default=None)

    class Meta:
        ordering = ["-mtime"]
        indexes = [
            # Covers all "list sessions by project+type" queries (listing + count + sync)
            models.Index(fields=["project", "type", "-mtime"], name="idx_session_project_type_mtime"),
            # Covers API session listing and project sessions_count (most frequent queries)
            models.Index(
                fields=["project", "-mtime"],
                name="idx_session_visible",
                condition=models.Q(
                    user_message_count__gt=0,
                    type="session",
                    created_at__isnull=False,
                ),
            ),
        ]

    @property
    def cutoff(self) -> datetime | None:
        """The most recent lifecycle boundary: max of last_started_at and last_stopped_at."""
        if self.last_stopped_at is None:
            return self.last_started_at
        if self.last_started_at is None:
            return self.last_stopped_at
        return max(self.last_started_at, self.last_stopped_at)

    def recalculate_costs(self) -> None:
        """Recalculate self_cost, subagents_cost, and total_cost from SessionItem costs.

        - self_cost = Sum of this session's own SessionItem.cost
        - subagents_cost = Sum of SessionItem.cost for all subagent sessions
        - total_cost = self_cost + subagents_cost
        """
        from decimal import Decimal
        from django.db.models import Sum

        self.self_cost = SessionItem.objects.filter(
            session=self,
            cost__isnull=False,
        ).aggregate(total=Sum("cost"))["total"]

        self.subagents_cost = SessionItem.objects.filter(
            session__parent_session=self,
            cost__isnull=False,
        ).aggregate(total=Sum("cost"))["total"]

        total = (self.self_cost or Decimal(0)) + (self.subagents_cost or Decimal(0))
        self.total_cost = total if total > 0 else None

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
    timestamp = models.DateTimeField(null=True, blank=True)

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
            # used to recompute activity
            models.Index(
                fields=["session", "timestamp"],
                name="idx_item_user_session",
                condition=Q(kind=ItemKind.USER_MESSAGE.value)
            ),
            models.Index(
                fields=["timestamp"],
                name="idx_item_user_all",
                condition=Q(kind=ItemKind.USER_MESSAGE.value)
            ),
            models.Index(
                fields=["session", "timestamp"],
                name="idx_item_cost_session",
                condition=Q(cost__isnull=False)
            ),
            models.Index(
                fields=["timestamp"],
                name="idx_item_cost_all",
                condition=Q(cost__isnull=False)
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
    tool_name = models.CharField(max_length=255, default="")  # The tool name (e.g. "Bash", "Read")
    tool_result_at = models.DateTimeField(null=True, blank=True)  # Timestamp of the tool_result item
    extra = models.TextField(null=True, blank=True)  # Optional extra data (e.g. diff stats for Edit tools)
    error = models.TextField(null=True, blank=True)  # Error message from tool_result (None = no error)

    class Meta:
        indexes = [
            models.Index(
                fields=["session", "tool_use_line_num", "tool_use_id"],
                name="idx_tool_result_link_lookup",
            ),
            models.Index(
                fields=["session", "tool_name"],
                name="idx_tool_result_link_by_name",
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
    is_background = models.BooleanField(default=False)  # True if run_in_background was set on the Task tool_use
    started_at = models.DateTimeField(null=True, blank=True)  # Timestamp of the Task tool_use item

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

    # ── In-memory price cache ──────────────────────────────────────────

    # Cache structure: populated lazily on first access, invalidated by invalidate_price_cache()
    _prices_by_model: ClassVar[dict[str, list["ModelPrice"]] | None] = None
    _models_by_family: ClassVar[dict[str, list[str]] | None] = None

    @classmethod
    def _ensure_price_cache(cls) -> None:
        """Load all ModelPrice rows into memory if cache is not populated."""
        if cls._prices_by_model is not None:
            return

        from collections import defaultdict

        prices_by_model: dict[str, list[ModelPrice]] = defaultdict(list)
        models_by_family: dict[str, list[str]] = defaultdict(list)

        for price in cls.objects.all().order_by("model_id", "-effective_date"):
            prices_by_model[price.model_id].append(price)

        # Build family index from distinct model_ids
        seen_model_ids: set[str] = set()
        for model_id in prices_by_model:
            if model_id in seen_model_ids:
                continue
            seen_model_ids.add(model_id)
            family, _ = cls._extract_family_and_version(model_id)
            if family:
                models_by_family[family].append(model_id)

        cls._prices_by_model = dict(prices_by_model)
        cls._models_by_family = dict(models_by_family)

    @classmethod
    def invalidate_price_cache(cls) -> None:
        """Reset the in-memory price cache. Call after price data is updated."""
        cls._prices_by_model = None
        cls._models_by_family = None

    @classmethod
    def get_price_for_date(cls, model_id: str, target_date: date) -> "ModelPrice | None":
        """
        Retrieve the applicable price for a model at a given date.

        Uses an in-memory cache (loaded lazily on first call) to avoid
        SQL queries on every invocation. The cache is invalidated by
        calling invalidate_price_cache() when price data is updated.

        Fallback chain:
        1. Exact model_id with effective_date <= target_date
        2. Exact model_id with oldest known price (for old messages)
        3. Same family, lower version (e.g., opus-4.5 if opus-4.7 not found)
        4. Same family, higher version (e.g., opus-5.0 if no lower version)
        5. None (caller should use DEFAULT_FAMILY_PRICES)
        """
        cls._ensure_price_cache()
        assert cls._prices_by_model is not None
        assert cls._models_by_family is not None

        # 1. Try to find price valid at target_date for exact model
        model_prices = cls._prices_by_model.get(model_id)
        if model_prices:
            # List is sorted by effective_date descending
            for price in model_prices:
                if price.effective_date <= target_date:
                    return price

            # 2. Fallback: oldest known price for exact model (last in descending list)
            return model_prices[-1]

        # 3 & 4. Try other versions of the same family
        family, version = cls._extract_family_and_version(model_id)
        if not family:
            return None

        family_model_ids = cls._models_by_family.get(family)
        if not family_model_ids:
            return None

        # Sort by version to find lower/higher versions
        family_prefix = f"anthropic/claude-{family}-"

        def extract_version(mid: str) -> tuple[int, ...]:
            """Extract version tuple for sorting: '4.5' -> (4, 5)"""
            v = mid.removeprefix(family_prefix)
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

            lower_versions = [m for m in family_model_ids if extract_version(m) < target_version]
            higher_versions = [m for m in family_model_ids if extract_version(m) > target_version]

            lower_versions.sort(key=extract_version, reverse=True)
            higher_versions.sort(key=extract_version)

            for fallback_model_id in lower_versions + higher_versions:
                fallback_prices = cls._prices_by_model.get(fallback_model_id)
                if fallback_prices:
                    # Return most recent price for the fallback model
                    return fallback_prices[0]

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


class ProcessRun(models.Model):
    """Tracks a Claude process execution for cron lifecycle management.

    Each ClaudeProcess gets a ProcessRun row when created. SessionCron rows
    reference their ProcessRun via CASCADE — deleting a process run deletes its crons.
    Orphan process runs (from crashed TwiCC instances) are detected and handled at startup.

    Uses a plain CharField for session_id (not a FK) because new sessions may not
    exist in the Session table yet when the process starts — the Session row is created
    asynchronously by the file watcher when the JSONL file appears.
    """

    session_id = models.CharField(max_length=200)
    started_at = models.DateTimeField()

    class Meta:
        indexes = [
            models.Index(fields=["session_id"], name="idx_processrun_session"),
        ]

    def __str__(self):
        return f"ProcessRun {self.pk} for session {self.session_id}"


class SessionCron(models.Model):
    """Persisted cron job created by a Claude session.

    Saved via PostToolUse hook on CronCreate, deleted on CronDelete.
    Survives TwiCC restarts to allow automatic rescheduling of cron jobs.

    Uses a plain CharField for session_id (not a FK) for the same reason as ProcessRun:
    new sessions may not exist in the Session table yet when a cron is created.
    """

    CLAUDE_RECURRING_MAX_AGE = timedelta(days=7)
    """Maximum age of a recurring cron before it auto-expires (matches Claude CLI behavior)."""

    cron_id = models.CharField(max_length=100, unique=True)
    session_id = models.CharField(max_length=200)
    process_run = models.ForeignKey(ProcessRun, on_delete=models.CASCADE, related_name="crons", null=True, blank=True)
    cron_expr = models.CharField(max_length=100)
    recurring = models.BooleanField()
    prompt = models.TextField()
    created_at = models.DateTimeField()
    next_fire = models.DateTimeField()

    class Meta:
        indexes = [
            models.Index(fields=["session_id"], name="idx_sessioncron_session"),
        ]

    def __str__(self):
        kind = "recurring" if self.recurring else "one-shot"
        return f"Cron {self.cron_id} ({kind}) on session {self.session_id}"

    def serialize(self) -> dict:
        """Serialize for WebSocket transmission (matches the format expected by the frontend)."""
        return {
            "id": self.cron_id,
            "cron_expr": self.cron_expr,
            "recurring": self.recurring,
            "prompt": self.prompt,
            "created_at": self.created_at.timestamp(),
            "next_fire": self.next_fire.timestamp(),
        }

    JITTER_SAFETY_MARGIN = timedelta(minutes=1)
    """Extra margin added after jitter to ensure the last fire has completed."""

    @cached_property
    def last_fire(self) -> datetime | None:
        """The timestamp when the CLI will fire this cron for the last time before auto-deleting it.

        Only meaningful for recurring crons. Returns the first cron occurrence that falls
        at or after created_at + 7 days (CLAUDE_RECURRING_MAX_AGE). This is the fire that
        triggers the CLI's age check and causes deletion.

        Returns None for non-recurring crons or if the cron expression is unparseable.
        """
        if not self.recurring:
            return None
        deadline = self.created_at + self.CLAUDE_RECURRING_MAX_AGE
        try:
            for occurrence in cron_occurrences(self.cron_expr, self.created_at):
                if occurrence >= deadline:
                    return occurrence
        except Exception:
            pass
        return None

    @cached_property
    def expired_at(self) -> datetime | None:
        """Timestamp after which this recurring cron has certainly finished its last fire.

        Computed as: last_fire + jitter_max + safety_margin.
        The CLI adds a random jitter of 10% of the cron period (capped at 15 minutes)
        to each fire. We add a safety margin on top.

        Returns None for non-recurring crons or if last_fire cannot be computed.
        """
        if self.last_fire is None:
            return None
        # Compute period from two consecutive occurrences
        try:
            it = cron_occurrences(self.cron_expr, self.created_at)
            first = next(it)
            second = next(it)
            period = second - first
        except Exception:
            # Can't compute period — use max jitter (15 min) as fallback
            period = timedelta(hours=3)  # 10% of 3h = 18min > 15min cap → will use cap
        jitter_max = min(period * 0.1, timedelta(minutes=15))
        return self.last_fire + jitter_max + self.JITTER_SAFETY_MARGIN

    @classmethod
    def has_active_for_session(cls, session_id: str) -> bool:
        """Check if a session has any non-expired cron jobs.

        Recurring crons expire after CLAUDE_RECURRING_MAX_AGE (7 days, matching CLI behavior).
        One-shot crons expire after their fire time passes.
        """
        now = datetime.now(tz=timezone.utc)
        return cls.objects.filter(
            session_id=session_id,
        ).filter(
            # Recurring: created less than 7 days ago
            Q(recurring=True, created_at__gt=now - cls.CLAUDE_RECURRING_MAX_AGE)
            # One-shot: fire time not yet passed
            | Q(recurring=False, next_fire__gt=now)
        ).exists()

    @classmethod
    def active_for_session(cls, session_id: str) -> models.QuerySet:
        """Return the queryset of non-expired crons for a session."""
        now = datetime.now(tz=timezone.utc)
        return cls.objects.filter(
            session_id=session_id,
        ).filter(
            Q(recurring=True, created_at__gt=now - cls.CLAUDE_RECURRING_MAX_AGE)
            | Q(recurring=False, next_fire__gt=now)
        )


class SlashCommandSource(models.TextChoices):
    """Origin of a slash command."""
    COMMANDS_DIR = "commands_dir", "Commands directory"
    SKILLS_DIR = "skills_dir", "Skills directory"
    PLUGIN = "plugin", "Plugin"


class SlashCommand(models.Model):
    """A slash command available for use in Claude Code sessions.

    Discovered from the filesystem: user-level commands/skills (~/.claude/),
    project-level commands/skills (<project>/.claude/), and plugin commands/skills.

    Commands with project=None are global (available for all projects).
    """

    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name="slash_commands",
        null=True,
        blank=True,  # NULL = global (available for all projects)
    )
    name = models.CharField(max_length=200)  # e.g. "commit", "superpowers:brainstorming"
    source = models.CharField(max_length=20, choices=SlashCommandSource.choices)
    plugin_name = models.CharField(max_length=100, null=True, blank=True)  # e.g. "superpowers" (when source=plugin)
    description = models.TextField()
    argument_hint = models.CharField(max_length=200, null=True, blank=True)  # e.g. "[review-aspects]"

    class Meta:
        indexes = [
            models.Index(fields=["project", "name"], name="idx_slash_command_project_name"),
        ]

    def __str__(self):
        scope = self.project_id or "global"
        return f"/{self.name} ({scope})"

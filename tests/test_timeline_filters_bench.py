"""Opt-in benchmark of the filtered timeline feed (design ``docs/plans/2026-10-26-timeline-filters-design.md``
section 7). Run with:
TWICC_TIMELINE_BENCH=1 uv run pytest tests/test_timeline_filters_bench.py -s -q

It builds one million synthetic events (the mix of section 7), then times ``fetch_feed`` (the whole body
of ``GET /api/timeline/``) for every case below, in three scopes (global, a workspace of 20 projects, one
project) and two pages (the first one, a ``before`` page whose cursor is the middle of the table): 5 runs,
the median. Bounds: a single filter answers in under 100 ms, a combination in under 500 ms. It prints a
table of every timing and the query plans of the three slowest cases.

Combinations measured (a view, one value per panel group, with or without the session filter). The rarest
ones are on purpose: a combination with few or no matching rows walks the most rows before the page fills.

| # | view           | family       | author        | provider    | session | why                               |
|---|----------------|--------------|---------------|-------------|---------|-----------------------------------|
| 1 | all            | turns        | session_agent | claude_code | no      | the most common rows              |
| 2 | all            | twicc        | automatic     | codex       | no      | instance rows only (no provider)  |
| 3 | without_turns  | sessions     | you           | claude_code | no      | a small author                    |
| 4 | without_turns  | organization | agent_cli_mcp | codex       | no      | ~0.015 % of the rows              |
| 5 | waits_problems | waits        | session_agent | claude_code | no      | the waits                         |
| 6 | waits_problems | processes    | unknown       | codex       | no      | ~0.012 % of the rows              |
| 7 | waits_problems | twicc        | automatic     | claude_code | no      | no row (no unclean backend end)   |
| 8 | all            | settings     | external_mcp  | codex       | no      | no row (no external MCP)          |
| 9 | without_turns  | turns        | session_agent | claude_code | no      | can never match                   |
|10 | waits_problems | processes    | you           | claude_code | no      | ~0.02 % of the rows               |
|11 | all            | waits        | agent_cli_mcp | codex       | no      | ~0.045 % of the rows              |
|12 | without_turns  | settings     | automatic     | claude_code | no      | a mid-size combination            |
|13 | all            | sessions     | unknown       | claude_code | no      | the rows dated afterwards         |
|14 | waits_problems | waits        | external_mcp  | claude_code | no      | no row                            |
|15 | all            | turns        | session_agent | claude_code | yes     | a session's common rows           |
|16 | waits_problems | processes    | unknown       | codex       | yes     | a session, few or no rows         |
|17 | without_turns  | organization | you           | codex       | yes     | a session, few or no rows         |
|18 | waits_problems | twicc        | external_mcp  | claude_code | yes     | a session, no row                 |
|19 | all            | settings     | agent_cli_mcp | claude_code | yes     | a session, rare author            |
|20 | without_turns  | sessions     | automatic     | claude_code | yes     | a session, generated titles       |
"""

import os
import random
import statistics
import time
from collections import Counter
from datetime import UTC, datetime, timedelta

import pytest
from django.db import connection
from django.utils import timezone

from twicc.core.models import Project, Session, TimelineEvent, TimelineKind, TimelinePrecision
from twicc.core.services import timeline_feed
from twicc.core.services.timeline_feed import fetch_feed, format_occurred_at, parse_request
from twicc.core.services.timeline_filters import AUTHORS, FAMILIES, PROBLEM_REASONS, VIEWS, author_of

pytestmark = [
    pytest.mark.django_db,
    pytest.mark.skipif(os.environ.get("TWICC_TIMELINE_BENCH") != "1", reason="set TWICC_TIMELINE_BENCH=1"),
]

SEED = 20261026
ROWS = 1_000_000
BATCH = 10_000
PROJECTS = 200
SESSIONS = 20_000  # ~50 rows per session
RUNS = 5
SINGLE_BOUND_MS = 100
COMBINATION_BOUND_MS = 500
T0 = datetime(2025, 10, 26, tzinfo=UTC)
STEP_SECONDS = 30  # one row every 30 s: about 347 days for the whole table

BIG_PROJECT = "-bench-p000"  # ~10 % of the rows
WORKSPACE = tuple(f"-bench-p{index:03d}" for index in range(1, 21))  # ~30 % of the rows together
# Share of the rows of each project: the big one, the 20 of the workspace, the 179 others.
PROJECT_SHARES = [0.10] + [0.30 / 20] * 20 + [0.60 / 179] * 179

#: Kind -> share of the rows (section 7: 45 % turns, 15 % waits, 20 % session + title, 10 % process,
#: 10 % the rest: settings, organization, backend).
KIND_SHARES = {
    TimelineKind.TURN_STARTED: 22.5, TimelineKind.TURN_ENDED: 22.5,
    TimelineKind.AWAITING_INPUT_STARTED: 7.5, TimelineKind.AWAITING_INPUT_ENDED: 7.5,
    TimelineKind.SESSION_CREATED: 2.0, TimelineKind.TITLE_CHANGED: 18.0,
    TimelineKind.PROCESS_STARTED: 5.0, TimelineKind.PROCESS_ENDED: 5.0,
    TimelineKind.SETTINGS_CHANGED: 5.0,
    **{kind: 4.95 / 8 for kind in FAMILIES["organization"]},
    TimelineKind.BACKEND_STARTED: 0.025, TimelineKind.BACKEND_ENDED: 0.025,
}
#: Author -> share of the rows (section 7). No external MCP row at all.
AUTHOR_SHARES = {"session_agent": 60, "automatic": 25, "unknown": 8, "you": 6, "agent_cli_mcp": 1}
#: Share of the ``process.ended`` rows with a problem reason: 10 % of 5 % = 0.5 % of all rows.
PROBLEM_SHARE_OF_PROCESS_ENDED = 0.10
#: Share of the session rows without a provider; with the instance rows, ~0.1 % of all rows.
NULL_PROVIDER_SHARE = 0.0005

COMBINATIONS = [
    ("all", "turns", "session_agent", "claude_code", False),
    ("all", "twicc", "automatic", "codex", False),
    ("without_turns", "sessions", "you", "claude_code", False),
    ("without_turns", "organization", "agent_cli_mcp", "codex", False),
    ("waits_problems", "waits", "session_agent", "claude_code", False),
    ("waits_problems", "processes", "unknown", "codex", False),
    ("waits_problems", "twicc", "automatic", "claude_code", False),
    ("all", "settings", "external_mcp", "codex", False),
    ("without_turns", "turns", "session_agent", "claude_code", False),
    ("waits_problems", "processes", "you", "claude_code", False),
    ("all", "waits", "agent_cli_mcp", "codex", False),
    ("without_turns", "settings", "automatic", "claude_code", False),
    ("all", "sessions", "unknown", "claude_code", False),
    ("waits_problems", "waits", "external_mcp", "claude_code", False),
    ("all", "turns", "session_agent", "claude_code", True),
    ("waits_problems", "processes", "unknown", "codex", True),
    ("without_turns", "organization", "you", "codex", True),
    ("waits_problems", "twicc", "external_mcp", "claude_code", True),
    ("all", "settings", "agent_cli_mcp", "claude_code", True),
    ("without_turns", "sessions", "automatic", "claude_code", True),
]

_NON_PROBLEM_REASONS = ("user_stop", "idle", "replaced")
_USER_VIAS = ("ui", "cli", "rpc", "mcp_internal")
_AGENT_TOOL_VIAS = ("cli", "mcp_internal")


def _origin(author, kind, rng):
    """``(precision, actor, via, data)`` that make a row of ``author`` (checked by ``author_of`` below)."""
    if kind in FAMILIES["twicc"]:
        return TimelinePrecision.EXACT, "system", None, {"clean": True} if kind == TimelineKind.BACKEND_ENDED else {}
    data = {}
    if author == "session_agent":
        precision, actor, via = TimelinePrecision.EXACT, "agent", None
    elif author == "automatic":
        # A creation row by ``system`` is "Unknown" whatever its precision (section 4.3): realistic, kept.
        precision, actor, via = TimelinePrecision.EXACT, "system", None
        if kind == TimelineKind.TITLE_CHANGED:
            data = {"source": "auto"}
    elif author == "unknown":
        precision, actor, via = TimelinePrecision.DERIVED, "system", None
    elif author == "you":
        precision, actor, via = TimelinePrecision.EXACT, "user", rng.choice(_USER_VIAS)
    else:  # agent_cli_mcp
        precision, actor, via = TimelinePrecision.EXACT, "agent", rng.choice(_AGENT_TOOL_VIAS)
    return precision, actor, via, data


def _generate():
    """Insert the projects, the sessions and the events. Returns the generation facts the cases need."""
    rng = random.Random(SEED)
    project_ids = [f"-bench-p{index:03d}" for index in range(PROJECTS)]
    projects = Project.objects.bulk_create(
        [Project(id=project_id, directory=f"/tmp/bench/{project_id}") for project_id in project_ids]
    )
    # Sessions spread over the projects in proportion to their share: a uniform session draw per row
    # then gives every project its share of the rows.
    session_project = rng.choices(range(PROJECTS), weights=PROJECT_SHARES, k=SESSIONS)
    session_provider = rng.choices(["claude_code", "codex"], weights=[70, 30], k=SESSIONS)
    now = timezone.now()
    Session.objects.bulk_create([
        Session(id=f"bs{index:05d}", file_path=f"bs{index:05d}.jsonl", project=projects[session_project[index]],
                provider=session_provider[index], title=f"Session {index}", created_at=now,
                last_updated_at=now, mtime=float(index))
        for index in range(SESSIONS)
    ], batch_size=BATCH)

    kinds, kind_weights = list(KIND_SHARES), list(KIND_SHARES.values())
    authors, author_weights = list(AUTHOR_SHARES), list(AUTHOR_SHARES.values())
    author_counts, provider_counts, session_counts = Counter(), Counter(), Counter()
    problems = 0
    for start in range(0, ROWS, BATCH):
        batch_kinds = rng.choices(kinds, weights=kind_weights, k=BATCH)
        batch_authors = rng.choices(authors, weights=author_weights, k=BATCH)
        events = []
        for offset in range(BATCH):
            index = start + offset
            kind, author = batch_kinds[offset], batch_authors[offset]
            precision, actor, via, data = _origin(author, kind, rng)
            reason = ""
            if kind == TimelineKind.PROCESS_ENDED:
                if rng.random() < PROBLEM_SHARE_OF_PROCESS_ENDED:
                    reason = rng.choice(sorted(PROBLEM_REASONS))
                    problems += 1
                else:
                    reason = rng.choice(_NON_PROBLEM_REASONS)
            if kind in FAMILIES["twicc"]:
                session_id = project = provider = None
            else:
                session = rng.randrange(SESSIONS)
                session_id, project = f"bs{session:05d}", projects[session_project[session]]
                provider = None if rng.random() < NULL_PROVIDER_SHARE else session_provider[session]
                session_counts[session_id] += 1
            author_counts[author_of({"kind": kind, "precision": precision, "actor": actor, "via": via,
                                     "data": data})] += 1
            provider_counts[provider] += 1
            events.append(TimelineEvent(
                session_id=session_id, project=project, kind=kind, precision=precision, actor=actor, via=via,
                actor_ref=session_id if actor == "agent" and via else None, provider=provider, reason=reason,
                data=data,
                # Strictly increasing with the id, as live writes are: the middle id is the middle time.
                occurred_at=T0 + timedelta(seconds=index * STEP_SECONDS + rng.random() * (STEP_SECONDS - 1)),
            ))
        TimelineEvent.objects.bulk_create(events, batch_size=BATCH)
    return author_counts, provider_counts, session_counts, problems, session_project


def _session_near_50(session_counts, session_project, project_indexes):
    """The session of one of ``project_indexes`` whose row count is the nearest to 50."""
    candidates = [sid for sid in session_counts if session_project[int(sid[2:])] in project_indexes]
    return min(candidates, key=lambda sid: (abs(session_counts[sid] - 50), sid))


def _timed(params):
    request = parse_request(params)
    durations = []
    for _ in range(RUNS):
        started = time.perf_counter()
        body = fetch_feed(request)
        durations.append((time.perf_counter() - started) * 1000)
    return statistics.median(durations), len(body["events"])


def _plans(params):
    lines = []
    for queryset in timeline_feed._page_querysets(parse_request(params)):
        sql, sql_params = queryset[: parse_request(params).limit + 1].query.sql_with_params()
        with connection.cursor() as cursor:
            cursor.execute("EXPLAIN QUERY PLAN " + sql, sql_params)
            lines.append(" | ".join(str(row[-1]) for row in cursor.fetchall()))
    return lines


def test_filtered_feed_speed():
    started = time.perf_counter()
    author_counts, provider_counts, session_counts, problems, session_project = _generate()
    print(f"\nGenerated {ROWS} events in {time.perf_counter() - started:.0f} s")

    # The mix of section 7, measured through the same rules as the filter (``author_of``).
    author_share = {author: 100 * author_counts[author] / ROWS for author in AUTHORS}
    print("Authors (%):", {author: round(share, 2) for author, share in author_share.items()})
    for author, expected in {**AUTHOR_SHARES, "external_mcp": 0}.items():
        assert abs(author_share[author] - expected) <= 1, (author, author_share[author])
    print("Providers (%):", {key: round(100 * value / ROWS, 3) for key, value in provider_counts.items()})
    print(f"Problems: {100 * problems / ROWS:.3f} %")
    scopes = {
        "global": ({}, range(PROJECTS)),
        "workspace": ({"project_ids": ",".join(WORKSPACE)}, range(1, 21)),
        "project": ({"project_ids": BIG_PROJECT}, (0,)),
    }
    for name, (params, _) in scopes.items():
        ids = params.get("project_ids", "").split(",") if params else None
        rows = TimelineEvent.objects.filter(project_id__in=ids).count() if ids else ROWS
        print(f"Scope {name}: {100 * rows / ROWS:.1f} % of the rows")

    middle = TimelineEvent.objects.order_by("occurred_at", "id")[ROWS // 2]
    pages = {"first": {}, "before": {"before": f"{format_occurred_at(middle.occurred_at)},{middle.id}"}}

    results = []  # (kind, label, scope, page, median_ms, events, params)
    for scope, (scope_params, project_indexes) in scopes.items():
        session_id = _session_near_50(session_counts, session_project, set(project_indexes))
        singles = [(f"view={view}", {"view": view}) for view in VIEWS]
        singles += [(f"family={family}", {"families": family}) for family in FAMILIES]
        singles += [(f"author={author}", {"authors": author}) for author in AUTHORS]
        singles += [(f"provider={provider}", {"provider": provider}) for provider in ("claude_code", "codex")]
        singles.append((f"session ({session_counts[session_id]} rows)", {"session": session_id}))
        combinations = []
        for number, (view, family, author, provider, with_session) in enumerate(COMBINATIONS, start=1):
            params = {"view": view, "families": family, "authors": author, "provider": provider}
            if with_session:
                params["session"] = session_id
            combinations.append((f"#{number} {view}/{family}/{author}/{provider}{'/session' if with_session else ''}",
                                 params))
        for kind, cases in (("single", singles), ("combination", combinations)):
            for label, case_params in cases:
                for page, page_params in pages.items():
                    params = {**scope_params, **case_params, **page_params}
                    median, count = _timed(params)
                    results.append((kind, label, scope, page, median, count, params))

    print(f"\n{'kind':<12} {'case':<62} {'scope':<10} {'page':<7} {'median ms':>10} {'events':>7}")
    for kind, label, scope, page, median, count, _ in results:
        print(f"{kind:<12} {label:<62} {scope:<10} {page:<7} {median:>10.2f} {count:>7}")

    slowest = sorted(results, key=lambda result: result[4], reverse=True)[:3]
    print("\nThe three slowest cases:")
    for kind, label, scope, page, median, _, params in slowest:
        print(f"\n{kind} {label} [{scope}, {page}]: {median:.2f} ms")
        for plan in _plans(params):
            print(f"  {plan}")

    worst = {
        kind: max((result for result in results if result[0] == kind), key=lambda result: result[4])
        for kind in ("single", "combination")
    }
    for kind, result in worst.items():
        print(f"Worst {kind}: {result[1]} [{result[2]}, {result[3]}] {result[4]:.2f} ms")
    failures = [
        result for result in results
        if result[4] >= (SINGLE_BOUND_MS if result[0] == "single" else COMBINATION_BOUND_MS)
    ]
    assert not failures, "\n".join(f"{r[0]} {r[1]} [{r[2]}, {r[3]}]: {r[4]:.2f} ms" for r in failures)

# CLI consistency before the 2026-10-01 cutover — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every CLI surface that changes on 2026-10-01 follow one rule — reads return `items`, batch actions return `{summary, results}`, one session payload, page size 20, a default wait cursor that does not miss an early answer — without breaking anything released before the date.

**Architecture:** All dated behaviour keeps reading `listing_cutover_passed()` / `cutover_help()` in `src/twicc/cli/_output.py`. A new CLI-layer enrichment (`src/twicc/cli/_session_payload.py`) sits between `serialize_session` (unchanged, shared with the UI) and `slim_session`. `session <id>` gets a custom Click group (`SessionGroup`) so its `--slim` / `--full` work on both sides of the id. The wait cursor gets one helper (`default_wait_cursors`) used by both waits.

**Tech Stack:** Python ≥ 3.13, Django 6, Typer 0.24 / Click 8.3, pytest + pytest-django, orjson.

**Spec:** `docs/plans/2026-09-23-cli-consistency-before-cutover-design.md` (the "spec"). Read it in full before Task 1. Section numbers below (§1 … §8) are the spec's. Where this plan says "the spec table", the table in the spec is the exact list of edits: apply every row. Spec line numbers are those of `bf57c675`. The files this plan edits did not change between `bf57c675` and `cc4911ff` (the HEAD this plan was checked against). One exception is descriptive only: `src/twicc/providers/claude_code/compute.py` changed in `32cb3a54` (hunks from line 561 on), so the spec's §5 references to it from `:1583` to `:1672` are now 31 lines lower (`compute_item_kind` is at `:1614`), while `:66-69`, `:76` and `:348` did not move; this plan does not edit that file. If HEAD has moved when you start, locate every cited line by its quoted text, not by its number.

## Global Constraints

- **Cutover instant:** `LISTING_CUTOVER` in `src/twicc/cli/_output.py`, `datetime(2026, 10, 1)`, naive, local time. Overridable for tests only by the environment variable `TWICC_LISTING_CUTOVER` (Task 1).
- **Nothing released breaks before 2026-10-01.** A pure addition (new key, new flag, new keyword, an error that becomes an answer) applies now. A change to an unreleased surface (`--paginated`, `--slim`, `--full`, `sessions stop`, both `wait-reply` commands) applies now. Every other shape change is dated and announced by a notice.
- **Owner's three exceptions (apply now, no notice):** `share` page size 50 → 20; `artifacts_dir` always the path; agent settings as effective values (stored values on a subagent row).
- `serialize_session` (`src/twicc/core/serializers.py`) output does not change. The UI and the WebSocket payloads do not change.
- `topology` keeps its own projection (`TOPOLOGY_SESSION_FIELDS`) and is not enriched.
- `TWICC_LISTING_CUTOVER` appears in no user doc, skill, help text, `SKILLS-AND-CLI.md` or the migration guide. Only in the comment next to the constant, the spec and the tests.
- Plugin version: **one** minor bump for the whole lot, `0.102.1` → `0.103.0`, in `src/twicc/agent/plugin/twicc/.claude-plugin/plugin.json` (Task 13).
- Do not edit any `docs/plans/*` file other than this plan. Do not touch the untracked `docs/plans/2026-07-18-*` files. No CHANGELOG entry.
- **Never commit** unless the owner asks. If asked: one commit per task, Conventional Commit subject, a body, the `Co-Authored-By` trailer of the running model, and `git add` with an explicit file list (never a directory).
- Stay on `main`. No branch, no worktree. Never restart the dev servers. After the lot, remind the owner to restart the backend: help texts and MCP tool descriptions are evaluated at import.
- Tests: `uv run pytest …` from `/home/twidi/dev/twicc-poc`. Lint: `uvx ruff check <files>` (never `uv run ruff`). Never `uv pip`, never `--active`.
- Before running the suite with `TWICC_LISTING_CUTOVER`, check that the data dir's `.env` does not define the key: `grep -sc '^TWICC_LISTING_CUTOVER' "${TWICC_DATA_DIR:-$HOME/.twicc}/.env" || true` must print `0` or nothing (nothing = no `.env` file, which is fine) (a key in the data dir's `.env` wins over the environment; the suite loads the `.env` of `TWICC_DATA_DIR` when set, else `~/.twicc/`).
- Before editing any skill, read `src/twicc/agent/plugin/README.md` and two neighbouring skills to match the tone.
- Every test that asserts a dated behaviour pins its side with a `before` / `after` fixture (`monkeypatch.setattr(_output, "LISTING_CUTOVER", FUTURE|PAST)`, `FUTURE = datetime(2200, 1, 1)`, `PAST = datetime(2000, 1, 1)`, both naive) or passes the explicit flag (`full=True`, `paginated=True`). A test that reads an import-time value (help text, MCP description) is two-sided: it reads `_output.listing_cutover_passed()` and asserts the matching side.
- A Typer command function whose parameters default to `typer.Option(...)` must be called with every argument when a test calls it directly: an `OptionInfo` default is truthy.
- **Baseline first.** Before Task 1, run `cd /home/twidi/dev/twicc-poc && uv run pytest -q` once (in the background) and record the failing test ids. Some tests depend on the data dir, not on this lot (e.g. `tests/test_wait_reply.py::test_create_session_hands_its_wait_arguments_over[*]` fails with `no_provider_configured` in a data dir where TwiCC never started; `test_processes_empty_scope_reports_the_same_window` fails with no live backend until Task 2 patches it). Every later "Expected: PASS" means "no failure beyond that baseline"; triage only new failures.
- **Line numbers are those of `cc4911ff`, before any task.** Earlier tasks of this plan shift them (Task 1 alone adds ~20 lines near the top of `_output.py` and 3 import lines to `tests/test_pagination_cutover.py`; Tasks 2, 6, 7, 8 add imports to `src/twicc/cli/__init__.py`). Always locate an edit by the quoted text, symbol or test name it names; use the number only as a hint.

## Review Focus

1. **`whoami` over MCP before the date, no flag** — an agent calling the always-loaded `whoami` tool must keep today's object (`session_id`, `agent_settings`, `session`, nine-field `process`) and get no notice. Test in Task 7.
2. **`sessions get` placeholder on an empty database** — the `{"id": None}` fallback of `_build_placeholder_template` must still carry every `CLI_ENRICHED_KEYS` key, `null`. Test in Task 4.
3. **`session self` through the MCP identity** — `dispatch_tool("session", {"session_id": "self"}, session_id=X)` must return X's row (the keyword resolves through `forced_session_id`, not PID ancestry). Test in Task 6.
4. **A session re-messaged and not answered yet** — the new default cursor must wait, not return the answer to the previous message. Test in Task 10.
5. **`session <subagent-id> --full`** — a subagent row keeps its stored agent settings (mostly `null`) and `process: null`; the enrichment must not resolve them. Test in Task 6.

## File map

| File | Change | Task |
|---|---|---|
| `src/twicc/cli/_output.py` | env override; page size 20; notice texts; help constants; `pagination_notice` shapes; `slim_notice` kind `whoami` | 1, 2, 5, 7, 8 |
| `src/twicc/cli/projects.py`, `workspaces.py`, `sessions.py`, `artifacts.py`, `search.py`, `share.py`, `session.py` | literal page sizes → constant | 2 |
| `src/twicc/projects.py` | `project_directories_cached`, `project_directory_cached` | 3 |
| `src/twicc/cli/_session_payload.py` (new) | `CLI_ENRICHED_KEYS`, `cli_session_payloads` | 4 |
| `src/twicc/cli/sessions.py`, `sessions_get.py`, `session.py` (`agents`) | use the enrichment | 4 |
| `src/twicc/core/serializers.py` | `SESSION_LISTING_FIELDS` grows by sixteen; comment | 5 |
| `src/twicc/cli/_session_group.py` (new) | `SessionGroup` | 6 |
| `src/twicc/cli/__init__.py` | `session` group, flags, keywords; `whoami`; lookups `--paginated`; `peers`; `sessions stop` help; wait helps; page-size constant | 2, 6, 7, 8, 9, 10 |
| `src/twicc/cli/session.py` | `build_session_payload`, `main` (lookup rule, flags), `wait_reply` cursor | 6, 10 |
| `src/twicc/cli/_remote.py` | comment above `HOST_BOUND_PARAMS` | 6 |
| `src/twicc/cli/whoami.py` | flags, dated default | 7 |
| `src/twicc/cli/sessions_get.py`, `projects_get.py`, `workspaces_get.py`, `peers.py` | `items` envelope | 8 |
| `src/twicc/cli/sessions_stop.py`, `_stop_batch.py` | `{summary, results}`, bare refusal, `skipped_self` | 9 |
| `src/twicc/cli/_wait_reply.py`, `sessions_wait_reply.py`, `send_message/command.py`, `send_messages.py` | `default_wait_cursors`; texts | 10 |
| skills, `SKILLS-AND-CLI.md`, `ORCHESTRATION.md` | docs | 11, 12 |
| `frontend/public/help/cli-rpc-migration-2026-10-01.md`, `plugin.json` | guide, bump | 13 |

---

### Task 1: Test override `TWICC_LISTING_CUTOVER` (§8)

**Files:**
- Modify: `src/twicc/cli/_output.py` (imports `:20-28`, constant `:106-113`, logger `:138`)
- Test: `tests/test_pagination_cutover.py` (new section at the end of the file)

**Interfaces:**
- Produces: `_output._BUILT_IN_LISTING_CUTOVER: datetime`, `_output.LISTING_CUTOVER_ENV = "TWICC_LISTING_CUTOVER"`, `_output._listing_cutover_from_env(default: datetime) -> datetime`, `_output.LISTING_CUTOVER` (now derived), `_output._NOTICE_LOGGER` (moved above the constant, same name).

**Behaviour:** Variable unset (the real case): identical to today, before and after the date. Variable set to a naive ISO date / date-time: every value derived from the constant (import-time help texts, `hidden=` of the retired commands, `MCP_EXCLUDED_ROOTS`, the MCP registry, every call-time check) uses it. Invalid value: built-in date plus one warning on stderr, exit code unchanged, stdout clean.

- [ ] **Step 1: Write the failing tests.** Add `import os`, `import subprocess`, `import sys` to the top import block of `tests/test_pagination_cutover.py`, then append:

```python
# --- the test override ------------------------------------------------------


class _Collect(logging.Handler):
    def __init__(self):
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record):
        self.records.append(record)


@pytest.mark.parametrize("raw", [None, "", "   "])
def test_the_override_is_ignored_when_unset_or_blank(monkeypatch, raw):
    handler = _Collect()
    _isolate_logger(monkeypatch, handlers=[handler])
    if raw is None:
        monkeypatch.delenv(_output.LISTING_CUTOVER_ENV, raising=False)
    else:
        monkeypatch.setenv(_output.LISTING_CUTOVER_ENV, raw)
    default = datetime(2026, 10, 1)  # noqa: DTZ001
    assert _output._listing_cutover_from_env(default) == default
    assert handler.records == []


@pytest.mark.parametrize(("raw", "expected"), [
    ("2026-09-24", datetime(2026, 9, 24)),  # noqa: DTZ001
    ("2026-09-24T13:30", datetime(2026, 9, 24, 13, 30)),  # noqa: DTZ001
])
def test_the_override_reads_a_naive_date_or_date_time(monkeypatch, raw, expected):
    monkeypatch.setenv(_output.LISTING_CUTOVER_ENV, raw)
    assert _output._listing_cutover_from_env(datetime(2026, 10, 1)) == expected  # noqa: DTZ001


@pytest.mark.parametrize("raw", ["not-a-date", "2026-09-24T00:00+02:00", "2026-09-24T00:00Z"])
def test_an_invalid_override_falls_back_with_one_warning(monkeypatch, raw):
    handler = _Collect()
    _isolate_logger(monkeypatch, handlers=[handler])
    monkeypatch.setenv(_output.LISTING_CUTOVER_ENV, raw)
    default = datetime(2026, 10, 1)  # noqa: DTZ001
    assert _output._listing_cutover_from_env(default) == default
    assert len(handler.records) == 1
    assert _output.LISTING_CUTOVER_ENV in handler.records[0].getMessage()


_PROBE = (
    "from twicc.cli import _output; "
    "print(_output.LISTING_CUTOVER.isoformat(), _output.listing_cutover_passed(), "
    "repr(_output.CUTOVER_NOTICE))"
)


def _probe(tmp_path, value, dotenv=None):
    env = {k: v for k, v in os.environ.items() if k != "TWICC_LISTING_CUTOVER"}
    env["TWICC_DATA_DIR"] = str(tmp_path)
    if value is not None:
        env["TWICC_LISTING_CUTOVER"] = value
    if dotenv is not None:
        (tmp_path / ".env").write_text(dotenv)
    return subprocess.run(
        [sys.executable, "-c", _PROBE], env=env, capture_output=True, text=True, timeout=120,
    )


def test_the_override_reaches_the_import_time_values(tmp_path):
    done = _probe(tmp_path, "2000-01-01")
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == "2000-01-01T00:00:00 True ''"


def test_an_invalid_override_never_breaks_the_import(tmp_path):
    done = _probe(tmp_path, "garbage")
    assert done.returncode == 0, done.stderr
    assert done.stdout.startswith(f"{_output._BUILT_IN_LISTING_CUTOVER.isoformat()} ")
    assert done.stdout.count("\n") == 1, "stdout holds the probe's print only"
    assert "Ignoring TWICC_LISTING_CUTOVER='garbage'" in done.stderr


def test_the_data_dir_env_file_wins_over_the_environment(tmp_path):
    done = _probe(tmp_path, "2200-01-01", dotenv="TWICC_LISTING_CUTOVER=2000-01-01\n")
    assert done.returncode == 0, done.stderr
    assert done.stdout.startswith("2000-01-01T00:00:00 True")
```

- [ ] **Step 2: Run the tests, verify they fail.**
Run: `uv run pytest tests/test_pagination_cutover.py -k "override or env_file_wins" -q`
Expected: FAIL — `AttributeError: module 'twicc.cli._output' has no attribute 'LISTING_CUTOVER_ENV'` for the in-process tests; for the subprocess ones, an `AssertionError` on the printed date (the variable is ignored today), except `test_an_invalid_override_never_breaks_the_import`, which fails earlier with `AttributeError: … '_BUILT_IN_LISTING_CUTOVER'` (it reads that constant to build its expected prefix).

- [ ] **Step 3: Implement.** In `src/twicc/cli/_output.py`: add `import os` to the imports; replace `LISTING_CUTOVER = datetime(2026, 10, 1)` with the code block of spec §8 ("Where it is read") verbatim — that block already starts with `_NOTICE_LOGGER = logging.getLogger("twicc.cli.cutover")` — and **delete** the old `_NOTICE_LOGGER = …` line at `:138` together with one of the blank lines around it (keep two blank lines before `def _in_mcp_call`), so the logger is defined once, above the constant. Keep the inline justification of the old line on the new one: `_BUILT_IN_LISTING_CUTOVER = datetime(2026, 10, 1)  # noqa: DTZ001 — local time, as announced` (the spec block has a bare `# noqa: DTZ001`). Keep the existing `#: Local wall-clock instant …` comment **directly above `_BUILT_IN_LISTING_CUTOVER`** (not above `_NOTICE_LOGGER`, which the spec block puts first), and append one line to it: `#: TWICC_LISTING_CUTOVER (undocumented, tests only) replaces it when set; see docs/plans/2026-09-23-cli-consistency-before-cutover-design.md, section 8.` `listing_cutover_passed`, `_CUTOVER_DATE` and every `cutover_help` keep reading the module global: no other change.

- [ ] **Step 4: Run the tests, verify they pass.**
Run: `uv run pytest tests/test_pagination_cutover.py tests/test_slim_cutover.py tests/test_process_commands_removal.py -q`
Expected: PASS.

- [ ] **Step 5: Lint.** Run: `uvx ruff check src/twicc/cli/_output.py tests/test_pagination_cutover.py` — no new finding on the touched lines.

**Docs:** none (undocumented on purpose).

---

### Task 2: Page size 20 everywhere (§1)

**Files:**
- Modify: `src/twicc/cli/_output.py` (`PAGINATED_DEFAULT_LIMIT` `:84-89`, `pagination_notice` docstring `:164-169`, `CUTOVER_NOTICE` `:339`, `CUTOVER_NOTICE_OBJECT` `:347`, `PAGINATED_HELP` `:355`, `limit_help` comment `:375`)
- Modify: `src/twicc/cli/projects.py:18,41`, `workspaces.py:15,26`, `sessions.py:229,256`, `artifacts.py:27,65`, `session.py:376,388,532,539`, `search.py:43,73`, `share.py:35-37,64`
- Modify: `src/twicc/cli/__init__.py` imports `:24-28`; `limit_help` calls `:86,159,229,943,976,1067,1193,1905`; `session content` help `:648`, `session messages` help `:673`
- Test: `tests/test_pagination_cutover.py`, `tests/test_cli_pagination_envelope.py`; fixture docstrings only in `tests/test_artifact_bookmarks.py`, `tests/test_cli_processes_listing.py`, `tests/test_cli_resolve_filters.py` (add these three to Step 5's lint command too)

**Interfaces:**
- Produces: `PAGINATED_DEFAULT_LIMIT = 20`; new constant `CUTOVER_NOTICE_PAGED: str`; `CUTOVER_NOTICE` / `CUTOVER_NOTICE_OBJECT` without the page-size clause.

**Behaviour:**

| Command | Before the date, no flag | Before, `--paginated` | After |
|---|---|---|---|
| `projects`, `workspaces`, `sessions`, `artifacts`, `session agents`, `session workflows`, `search` | 20 (unchanged) | 20 (was 50, unreleased) | 20 |
| `session content`, `session messages` | everything (unchanged); notice says "and pages at 20 by default" | 20 | 20 |
| `share` | **20 now** (was 50); notice without page-size clause | 20 | 20 |
| `processes` (retired) | 20 | 20 | removed |

- [ ] **Step 1: Update the tests that pin 50 (they become the failing tests).**
  - In `tests/test_pagination_cutover.py` (cited by test name: Task 1 adds three import lines above them, so line numbers shift by 3), every assertion of a 50-row page becomes 20 — the `--paginated` / after-date page: `payload["pagination"]["limit"] == 50` and `len(payload["items"]) == 50` (the two lines at `cc4911ff` `:126-127`), and `len(payload["items"]) == 50` (`:198`). Find them with `grep -n "== 50" tests/test_pagination_cutover.py`. Keep the created row count above 20 so `has_more` stays meaningful.
  - `test_share_omits_the_page_size_clause` (`cc4911ff` `:150-155`): docstring → "`share` pages at the constant, so only its shape changes."; assert `"pages at" not in err`. This one already passes today (it is a wording update, not a failing test).
  - `tests/test_pagination_cutover.py:348-362`: `test_the_limit_help_names_both_page_sizes_before` → assert `_output.limit_help("messages", None) == "Max number of messages to return (default: no limit; 20 with --paginated)."`; `test_the_limit_help_collapses_after` → `limit_help("sessions", 20) == "Max number of sessions to return (default: 20)."`; rename `test_a_command_already_at_fifty_never_mentions_the_flag` → `test_a_command_at_the_constant_never_mentions_the_flag`, docstring "A default equal to the constant: the flag changes nothing.", assert `"--paginated" not in _output.limit_help("shares", _output.PAGINATED_DEFAULT_LIMIT)` and `"--paginated" not in _output.limit_help("sessions", 20)` (keep its `before` fixture).
  - `tests/test_cli_pagination_envelope.py:315-319` and `:364`: `50` → `20` (`"limit": 20`).
  - `tests/test_cli_pagination_envelope.py:432` (in `test_processes_empty_scope_reports_the_same_window`, `:424`): `"limit": 50` → `20`. This test calls `resolve_live_twicc_or_exit()` (`src/twicc/cli/processes.py:82-85`) unpatched, so it fails with "TwiCC is not running" on a machine with no live backend in `~/.twicc/`: add a `monkeypatch` parameter and `monkeypatch.setattr("twicc.cli._twicc_info.resolve_live_twicc_or_exit", lambda: type("I", (), {"pid": 4242})())` (the function imports it inside `main`, so the module-attribute patch applies).
  - The section comment `tests/test_pagination_cutover.py:118`, `# --- phase 2: the envelope and the page size become the default`, → `# --- phase 2: the envelope becomes the default (and the page size, on session content / messages)` (keep the dash padding to the file's width).
  - Four fixture docstrings say the pinned tests assert "the pre-cutover shape (a bare array, and the per-command default page size). Past the date both change" — false once the listings page at the constant: `_before_the_pagination_cutover` in `tests/test_artifact_bookmarks.py:20-22`, `tests/test_cli_processes_listing.py:21-23` and `tests/test_cli_resolve_filters.py:27-29` → "(a bare array). Past the date it changes"; and in `tests/test_cli_pagination_envelope.py:30-32` → "(a bare array, and, for `session content` / `messages`, the default page size)". `git grep -n "pre-cutover shape" -- tests` lists these four (the sentence is split over two lines).
  - Delete `test_the_flag_overrides_a_command_s_own_default` (`tests/test_cli_pagination_envelope.py:323-332`), per spec §7 "Page size".
  - Add to `tests/test_pagination_cutover.py`:

```python
def test_every_listing_default_is_the_constant():
    assert _output.PAGINATED_DEFAULT_LIMIT == 20


def test_share_pages_at_twenty_on_both_sides(monkeypatch, project, capsysbinary):
    """Owner's exception: 50 → 20 now, with no page-size clause in the notice."""
    from twicc.core.models import Share

    session = make_sessions(project, 1)[0]
    now = timezone.now()
    for i in range(25):
        Share.objects.create(
            kind="session", session=session, token=f"pg20-token-{i}",
            created_at=now + timedelta(minutes=i),
        )
    for pinned in (FUTURE, PAST):
        monkeypatch.setattr(_output, "LISTING_CUTOVER", pinned)
        cli_share.list_main()
        payload, err = read(capsysbinary)
        rows = payload["items"] if isinstance(payload, dict) else payload
        assert len(rows) == 20
        assert "pages at" not in err


def test_only_the_two_transcript_readers_announce_a_page_size():
    """Import-time strings: two-sided on the effective constant."""
    if _output.listing_cutover_passed():
        assert _output.CUTOVER_NOTICE == _output.CUTOVER_NOTICE_PAGED == ""
        return
    assert "pages at" not in _output.CUTOVER_NOTICE
    assert "pages at" not in _output.CUTOVER_NOTICE_OBJECT
    assert "and pages at 20 by default" in _output.CUTOVER_NOTICE_PAGED
    assert "the page size is 20" in _output.PAGINATED_HELP


def test_the_runtime_notice_names_a_page_size_only_where_it_changes(before, project, capsysbinary):
    make_sessions(project, 1)
    cli_sessions.main(project=project.id, full=True)
    _, err = read(capsysbinary)
    assert "`sessions`" in err and "pages at" not in err
    cli_session.messages("co-sess-0")
    _, err = read(capsysbinary)
    assert "and pages at 20 by default" in err
```

  `cli_session.messages("co-sess-0")` is a valid call: every argument after the id has a default.

  Add the MCP-side check of the same rule (import-time, two-sided):

```python
def test_only_the_transcript_readers_announce_a_page_size_to_agents():
    from twicc.mcp.tools import iter_mcp_tools

    described = {t.name: t.description for t in iter_mcp_tools()}
    paged = {n for n, d in described.items() if "pages at 20 by default" in d}
    if _output.listing_cutover_passed():
        assert paged == set()
    else:
        assert paged == {"session_content", "session_messages"}
```

- [ ] **Step 2: Run, verify they fail.**
Run: `uv run pytest tests/test_pagination_cutover.py tests/test_cli_pagination_envelope.py -q`
Expected: FAIL on the `20` assertions and the new tests (e.g. `"pages at" not in _output.CUTOVER_NOTICE` fails before `CUTOVER_NOTICE_PAGED` is even reached).

- [ ] **Step 3: Implement.**
  - `PAGINATED_DEFAULT_LIMIT = 20`. Rewrite its comment: the page size of every listing (from 2026-10-01 for `session content` / `messages`, which return everything by default until then), and the one `--paginated` supplies when no `--limit` is given.
  - Each listing module imports `PAGINATED_DEFAULT_LIMIT` from `twicc.cli._output` and passes it in `pagination_notice(default_limit=…)` and `resolve_limit(default=…)`. `share.py` drops the comment at `:35-36`. `session content` / `session messages` keep `None`. `processes.py:116` keeps its literal `20` (retired on the date).
  - `__init__.py`: import `PAGINATED_DEFAULT_LIMIT` and `CUTOVER_NOTICE_PAGED`; every `limit_help("…", 20)` and `limit_help("shares", 50)` becomes `limit_help("…", PAGINATED_DEFAULT_LIMIT)` except `processes` (`:1420`, keeps `20`) and the two `None`; `session content` (`:648`) and `session messages` (`:673`) use `CUTOVER_NOTICE_PAGED` instead of `CUTOVER_NOTICE`.
  - `_output.py` texts:

```python
CUTOVER_NOTICE = cutover_help(
    f"DEPRECATION: from {_CUTOVER_DATE} this returns {{items, pagination}} instead "
    "of a bare array. Pass --paginated now to get that shape today. ",
    "",
)

#: Same, for the two commands whose flagless page size also changes on the date.
CUTOVER_NOTICE_PAGED = cutover_help(
    f"DEPRECATION: from {_CUTOVER_DATE} this returns {{items, pagination}} instead "
    f"of a bare array, and pages at {PAGINATED_DEFAULT_LIMIT} by default. Pass "
    "--paginated now to get that shape today. ",
    "",
)

#: Same as :data:`CUTOVER_NOTICE`, for the one listing whose current shape is
#: already an object (``search``).
CUTOVER_NOTICE_OBJECT = cutover_help(
    f"DEPRECATION: from {_CUTOVER_DATE} this renames `hits` to `items` and "
    "`total_hits` to `pagination.total`, and moves `limit`/`offset` under "
    "`pagination`. Pass --paginated now to get that shape today. ",
    "",
)
```

    `PAGINATED_HELP` before-text: "the page size becomes {N}" → "the page size is {N}". `limit_help` comment at `:375` → `# A command whose default equals the constant: the flag changes nothing for it.` `pagination_notice` docstring: "``50`` means its page size is not changing, which exempts ``share`` from that clause without a special case" → "a default equal to :data:`PAGINATED_DEFAULT_LIMIT` means the page size is not changing, so the notice names the shape alone". `pagination_notice`'s runtime clause (`:189-190`) needs no code change.

- [ ] **Step 4: Run, verify they pass.**
Run: `uv run pytest tests/test_pagination_cutover.py tests/test_cli_pagination_envelope.py tests/test_slim_cutover.py tests/test_process_commands_removal.py tests/test_mcp_tools.py -q`
Expected: PASS.

- [ ] **Step 5: Lint.** `uvx ruff check src/twicc/cli/_output.py src/twicc/cli/projects.py src/twicc/cli/workspaces.py src/twicc/cli/sessions.py src/twicc/cli/artifacts.py src/twicc/cli/session.py src/twicc/cli/search.py src/twicc/cli/share.py src/twicc/cli/__init__.py tests/test_pagination_cutover.py tests/test_cli_pagination_envelope.py`

**Docs:** the page-size text edits are in Task 11 (skills, `SKILLS-AND-CLI.md`) and Task 13 (guide).

---

### Task 3: Project-directory cache helpers (§2 "The project-directory helper")

**Files:**
- Modify: `src/twicc/projects.py` (after `get_project_directory`, `:104-106`)
- Create: `tests/test_project_directory_cache.py`

**Interfaces:**
- Produces: `project_directories_cached(project_ids: Iterable[str]) -> dict[str, str | None]`, `project_directory_cached(project_id: str) -> str | None`.

**Behaviour:** new functions, no caller yet: nothing changes for users.

- [ ] **Step 1: Write the failing tests** in `tests/test_project_directory_cache.py`:

```python
"""The read-through cache the CLI session payload uses for project_directory."""

from __future__ import annotations

import pytest

from twicc import projects
from twicc.core.models import Project


@pytest.fixture(autouse=True)
def empty_cache(monkeypatch):
    monkeypatch.setattr(projects, "_project_directories", {})


@pytest.fixture
def two_projects(db):
    Project.objects.create(id="-p-one", directory="/p/one")
    Project.objects.create(id="-p-two", directory=None)


def test_a_hit_asks_no_query(db, django_assert_num_queries):
    projects._project_directories["-p-one"] = "/cached"
    with django_assert_num_queries(0):
        assert projects.project_directory_cached("-p-one") == "/cached"


def test_a_miss_asks_one_query_then_none(two_projects, django_assert_num_queries):
    with django_assert_num_queries(1):
        assert projects.project_directory_cached("-p-one") == "/p/one"
    assert projects._project_directories["-p-one"] == "/p/one"
    with django_assert_num_queries(0):
        assert projects.project_directory_cached("-p-one") == "/p/one"


def test_a_stored_none_is_a_hit(db, django_assert_num_queries):
    projects._project_directories["-p-two"] = None
    with django_assert_num_queries(0):
        assert projects.project_directory_cached("-p-two") is None


def test_several_misses_cost_one_query(two_projects, django_assert_num_queries):
    with django_assert_num_queries(1):
        found = projects.project_directories_cached(["-p-one", "-p-two", "-p-none", "-p-one"])
    assert found == {"-p-one": "/p/one", "-p-two": None, "-p-none": None}
    assert "-p-none" not in projects._project_directories, "no row: not stored"
    assert projects._project_directories["-p-two"] is None


def test_a_cached_key_survives_a_call(two_projects):
    projects._project_directories["-p-kept"] = "/kept"
    projects.project_directories_cached(["-p-one"])
    assert projects._project_directories["-p-kept"] == "/kept"
```

  `monkeypatch.setattr(projects, "_project_directories", {})` rebinds the module global; the helpers read `_project_directories` as a module global at call time (same module).

- [ ] **Step 2: Run, verify they fail.**
Run: `uv run pytest tests/test_project_directory_cache.py -q`
Expected: FAIL with `AttributeError: module 'twicc.projects' has no attribute 'project_directory_cached'` (or `'project_directories_cached'` for `test_several_misses_cost_one_query` and `test_a_cached_key_survives_a_call`).

- [ ] **Step 3: Implement** in `src/twicc/projects.py`, right after `get_project_directory`:

```python
def project_directories_cached(project_ids: Iterable[str]) -> dict[str, str | None]:
    """Directory of each project, read through the module cache.

    A cached key answers without a query, a stored ``None`` included: the test
    is key membership, never ``.get()``, which cannot tell a miss from a hit.
    The misses cost ONE query, and each row found is stored with a single-key
    assignment (atomic under the GIL, never ``clear()``), so a concurrent
    ``load_project_directories`` rebuild costs at most one extra read of the
    same value. An id with no ``Project`` row maps to ``None`` and is not stored.
    """
    found: dict[str, str | None] = {}
    misses: list[str] = []
    for project_id in dict.fromkeys(project_ids):
        try:
            found[project_id] = _project_directories[project_id]
        except KeyError:
            misses.append(project_id)
    if misses:
        for project_id, directory in Project.objects.filter(id__in=misses).values_list("id", "directory"):
            _project_directories[project_id] = directory
            found[project_id] = directory
        for project_id in misses:
            found.setdefault(project_id, None)
    return found


def project_directory_cached(project_id: str) -> str | None:
    """One-id form of :func:`project_directories_cached`."""
    return project_directories_cached([project_id])[project_id]
```

  Accuracy note on the spec (no code change): "a project's directory never changes" is not strictly true — `ensure_project_directory` (`projects.py:128-161`) rewrites it whenever the value it receives differs from the cached one. Every write of `Project.directory` in `projects.py` also updates the cache of the process that runs it, after commit: `ensure_project_directory` (`transaction.on_commit`, `:161`), `_create_or_get_project` (`:394`) and `_adopt_directory_sync` (`:426-434`). In the backend, it runs from `apply_session_complete` (`compute_base.py:3301`, applied in the backend by `db_writer.py:2077`) and from `sync_session_items_from_file` (`compute_base.py:3798`). A value cached by another process (a terminal `twicc`) lives for one call only. One narrow race remains in the backend: a cache-miss read of this helper taken before that commit can be stored after the `on_commit` update, leaving the old value cached until the next write for that project. Accepted: the window is one transaction, the value only feeds a read-only CLI field, and `get_project_directory` shares the same model.

  `try` / `except KeyError` rather than `in` then `[]`: a `clear()` between the two would raise. `Iterable` is already imported in the module (used at `:713`); confirm with `grep -n "Iterable" src/twicc/projects.py | head -3`.

- [ ] **Step 4: Run, verify they pass.** `uv run pytest tests/test_project_directory_cache.py -q` — PASS.
- [ ] **Step 5: Lint.** `uvx ruff check src/twicc/projects.py tests/test_project_directory_cache.py`

**Docs:** none.

---

### Task 4: The CLI session enrichment (§2 "The CLI session payload")

**Files:**
- Create: `src/twicc/cli/_session_payload.py`
- Modify: `src/twicc/cli/sessions.py:250-267` (`main`), `src/twicc/cli/sessions_get.py` (docstring `:1-19`, `_build_placeholder_template` `:33-47`, `main` `:79-102`), `src/twicc/cli/session.py:380-392` (`agents`)
- Create: `tests/test_cli_session_payload.py`
- Modify: `tests/conftest.py` (autouse fixture resetting the project-directory cache, Step 3)
- Adjust (only if red, see Step 4): `tests/test_cli_session_process_state.py`, `tests/test_cli_pagination_envelope.py:631-658`

**Interfaces:**
- Consumes: `project_directories_cached` (Task 3).
- Produces: `CLI_ENRICHED_KEYS: tuple[str, ...] = ("project_directory", "scratch_dir", "orchestration_scratch_dir", "question_widget")`; `cli_session_payloads(sessions: Iterable[Session]) -> list[dict]` (serialized + enriched, input order, no projection, no `process`).

**Behaviour (now, both sides of the date):** every row of `sessions`, `sessions get` and `session agents` gains `project_directory`, `scratch_dir`, `orchestration_scratch_dir`, `question_widget` (additive); `artifacts_dir` is always the path (owner's exception); the eight agent settings are effective on a session row and stored on a subagent row (owner's exception). Placeholders gain the four keys as `null`; their `artifacts_dir` stays `null`. `--slim` rows are unchanged in this task (the projection grows in Task 5).

- [ ] **Step 1: Write the failing tests** in `tests/test_cli_session_payload.py`:

```python
"""The CLI-layer enrichment every session-emitting command applies."""

from __future__ import annotations

import orjson
import pytest
from django.utils import timezone

from twicc import projects
from twicc.cli import session as cli_session
from twicc.cli import sessions as cli_sessions
from twicc.cli import sessions_get as cli_sessions_get
from twicc.cli._session_payload import CLI_ENRICHED_KEYS, cli_session_payloads
from twicc.core.models import Project, Session, SessionType
from twicc.core.serializers import serialize_session
from twicc.paths import get_session_artifacts_dir, get_session_scratch_dir

SYNCED = {
    "claudeCodeDefaultModel": "opus", "claudeCodeDefaultEffort": "high",
    "claudeCodeDefaultPermissionMode": "default", "claudeCodeDefaultThinking": True,
    "claudeCodeDefaultClaudeInChrome": False, "claudeCodeDefaultFastMode": False,
    "claudeCodeDefaultContextMax": 200000,
    "codexDefaultModel": "gpt-sol", "codexDefaultEffort": "medium",
    "codexDefaultPermissionMode": "read_only", "codexDefaultContextMax": 272000,
    "codexDefaultFastMode": False,
}


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    # resolve_agent_settings imports read_synced_settings at call time.
    monkeypatch.setattr("twicc.synced_settings.read_synced_settings", lambda: dict(SYNCED))
    monkeypatch.setattr(projects, "_project_directories", {})
    monkeypatch.setattr("twicc.cli._twicc_info.resolve_live_twicc", lambda: None)
    monkeypatch.setattr("twicc.cli.sessions_get._PLACEHOLDER_TEMPLATE", None)


@pytest.fixture
def project(db):
    return Project.objects.create(id="-tmp-enrich", directory="/tmp/enrich")


def make(project, sid, **extra):
    values = {
        "id": sid, "project": project, "provider": "claude_code", "file_path": f"{sid}.jsonl",
        "type": SessionType.SESSION, "created_at": timezone.now(), "last_line": 3,
        "user_message_count": 1,
    }
    return Session.objects.create(**(values | extra))


def _rows(capsysbinary):
    payload = orjson.loads(capsysbinary.readouterr().out)
    return payload["items"] if isinstance(payload, dict) else payload


def test_paths_and_directory(project):
    [row] = cli_session_payloads([make(project, "e1")])
    assert row["project_directory"] == "/tmp/enrich"
    assert row["artifacts_dir"] == str(get_session_artifacts_dir("e1")), "a path even with no artifact"
    assert row["has_artifacts"] is False
    assert row["scratch_dir"] == str(get_session_scratch_dir("e1"))
    assert row["orchestration_scratch_dir"] is None


def test_orchestration_scratch_dir_comes_from_the_annotation(project):
    session = make(project, "e2", annotations={"scratch_dir": "/shared/x"})
    assert cli_session_payloads([session])[0]["orchestration_scratch_dir"] == "/shared/x"


def test_null_settings_are_resolved_and_question_widget_true(project):
    [row] = cli_session_payloads([make(project, "e3")])
    assert (row["selected_model"], row["effort"], row["permission_mode"]) == ("opus", "high", "default")
    assert row["thinking_enabled"] is True
    assert row["question_widget"] is True


def test_stored_settings_win_and_false_stays_false(project):
    session = make(project, "e4", selected_model="sonnet", effort="low", question_widget=False)
    [row] = cli_session_payloads([session])
    assert (row["selected_model"], row["effort"], row["question_widget"]) == ("sonnet", "low", False)


def test_a_subagent_keeps_its_stored_values(project):
    parent = make(project, "e5")
    sub = make(project, "e5-sub", type=SessionType.SUBAGENT, parent_session=parent)
    [row] = cli_session_payloads([sub])
    assert row["selected_model"] is None
    assert row["question_widget"] is None
    assert row["artifacts_dir"] == str(get_session_artifacts_dir("e5-sub"))


def test_a_codex_row_keeps_null_for_unsupported_fields(project):
    [row] = cli_session_payloads([make(project, "e6", provider="codex")])
    assert row["thinking_enabled"] is None
    assert row["claude_in_chrome"] is None
    assert row["selected_model"] == "gpt-sol"


def test_one_query_for_a_cold_page_none_for_a_warm_one(project, django_assert_num_queries):
    other = Project.objects.create(id="-tmp-enrich-2", directory="/tmp/enrich-2")
    rows = [make(project, "q1"), make(project, "q2"), make(other, "q3")]
    with django_assert_num_queries(1):
        cli_session_payloads(rows)
    with django_assert_num_queries(0):
        cli_session_payloads(rows)


def test_the_serializer_is_untouched(project):
    session = make(project, "e7")
    before = serialize_session(session)
    cli_session_payloads([session])
    assert serialize_session(session) == before
    assert "question_widget" not in before
    assert "project_directory" not in before


def test_enriched_keys_are_the_ones_the_serializer_lacks(project):
    session = make(project, "e8")
    added = set(cli_session_payloads([session])[0]) - set(serialize_session(session))
    assert added == set(CLI_ENRICHED_KEYS)


def test_the_three_commands_carry_the_enrichment(project, capsysbinary):
    parent = make(project, "c1")
    make(project, "c1-sub", type=SessionType.SUBAGENT, parent_session=parent)
    cli_sessions.main(project=project.id, full=True)
    assert all(set(CLI_ENRICHED_KEYS) <= set(r) for r in _rows(capsysbinary))
    cli_sessions_get.main(["c1", "nope"], full=True)
    known, placeholder = _rows(capsysbinary)
    assert set(known) == set(placeholder)
    assert all(placeholder[k] is None for k in CLI_ENRICHED_KEYS)
    assert placeholder["artifacts_dir"] is None
    cli_session.agents("c1", full=True)
    [sub] = _rows(capsysbinary)
    assert sub["selected_model"] is None and sub["process"] is None


def test_the_empty_database_placeholder_has_the_enriched_keys(db, capsysbinary):
    """Review focus 2: the `{"id": None}` fallback of the template."""
    cli_sessions_get.main(["ghost"], full=True)
    [placeholder] = _rows(capsysbinary)
    assert set(CLI_ENRICHED_KEYS) <= set(placeholder)
    assert placeholder["known"] is False
```

  `_rows` reads both shapes, so these tests pass on both sides of the date and after Task 8. The `django_assert_num_queries` blocks count only what `cli_session_payloads` asks: `serialize_session` is query-free (module docstring of `src/twicc/core/serializers.py`).

- [ ] **Step 2: Run, verify they fail.** `uv run pytest tests/test_cli_session_payload.py -q` — FAIL, `ModuleNotFoundError: No module named 'twicc.cli._session_payload'`.

- [ ] **Step 3: Implement** `src/twicc/cli/_session_payload.py`:

```python
"""The session payload every CLI command emits.

Order, shared by every caller: ``serialize_session`` (unchanged, shared with the
UI) → this enrichment → the reduced projection when asked → the ``process``
block. Design: docs/plans/2026-09-23-cli-consistency-before-cutover-design.md, §2.
"""

from __future__ import annotations

from collections.abc import Iterable

#: Keys this enrichment adds that ``serialize_session`` never emits. The
#: ``sessions get`` placeholder carries them too, ``null``.
CLI_ENRICHED_KEYS: tuple[str, ...] = (
    "project_directory", "scratch_dir", "orchestration_scratch_dir", "question_widget",
)


def cli_session_payloads(sessions: Iterable) -> list[dict]:
    """Serialize and enrich ``sessions`` (``Session`` rows), in input order.

    One query at most, for the project directories the cache lacks.
    ``artifacts_dir`` is always the path to write to. (``has_artifacts`` says
    whether it holds anything only when the backend runs the command — MCP,
    RPC; in a terminal process the artifacts watcher never started and it is
    always ``False``, ``artifacts_watcher.session_has_artifacts``.) Agent settings are the effective values on a
    session row (stored value, else the current global default;
    ``question_widget`` ``null`` means enabled) and the stored values on a
    subagent row, which runs inside its parent's process.
    """
    from twicc.core.serializers import serialize_session
    from twicc.paths import get_session_artifacts_dir, get_session_scratch_dir
    from twicc.projects import project_directories_cached
    from twicc.providers.helpers import AgentSettings, get_provider_helpers

    sessions = list(sessions)
    directories = project_directories_cached(s.project_id for s in sessions if s.project_id)
    payloads = []
    for session in sessions:
        data = serialize_session(session)
        data["project_directory"] = directories.get(session.project_id) if session.project_id else None
        data["artifacts_dir"] = str(get_session_artifacts_dir(session.id))
        data["scratch_dir"] = str(get_session_scratch_dir(session.id))
        data["orchestration_scratch_dir"] = (session.annotations or {}).get("scratch_dir") or None
        settings = AgentSettings.from_session(session)
        if session.parent_session_id is None:
            settings = get_provider_helpers(session.provider).resolve_agent_settings(settings)
            if settings.question_widget is None:
                settings = settings._replace(question_widget=True)
        data.update(settings._asdict())
        payloads.append(data)
    return payloads
```

  `resolve_agent_settings` (`src/twicc/providers/helpers.py:608`) calls `read_synced_settings()` once per row; the first call of the process reads the file, every later one an in-process cache under a lock (`src/twicc/synced_settings.py:332-341`); never the database: no query, accepted.

  **Test docstrings the enrichment makes false** (reword, assertions unchanged):
  - `tests/test_cli_session_process_state.py:38-44` (`fresh_placeholder_template`): "this file is the only place in the suite that calls ``sessions get`` with an empty ``Session`` table" → "this file, like `tests/test_cli_session_payload.py`, calls ``sessions get`` with an empty ``Session`` table"; its `{"id": None}` mention gains "plus ``CLI_ENRICHED_KEYS``".
  - `tests/test_cli_session_process_state.py:285` ("no pid is resolved and no row read") and `:730` ("No query, and no pid resolution either."): the enrichment may now ask one `Project` query on a cold cache; scope both to the process lookup — "no pid is resolved and no `ProcessRun` row read", "No `ProcessRun` query, and no pid resolution either." (the assertions already filter on `core_processrun`).

  **Shared cache in the test process.** From this task on, every test that emits a session through the CLI writes to `twicc.projects._project_directories`, one dict for the whole pytest process; a later test could read a directory an earlier test cached. Add to `tests/conftest.py`:

```python
@pytest.fixture(autouse=True)
def fresh_project_directory_cache(monkeypatch):
    """The CLI session payload reads project directories through a module-level
    cache (twicc.projects._project_directories); give each test its own."""
    from twicc import projects

    monkeypatch.setattr(projects, "_project_directories", {})
```

  `projects.py` reads the global at call time, so the rebinding applies to every helper there (`get_project_directory`, `ensure_project_directory`, `load_project_directories` included). The per-file fixtures of Tasks 3 and 4 then become redundant but harmless; keep them (they document the dependency).

  Wire it:
  - `sessions.py` `main`: `data = [serialize_session(s) for s in sessions]` → `data = cli_session_payloads(sessions)` (import it from `twicc.cli._session_payload` inside the function; drop the now-unused `serialize_session` import).
  - `session.py` `agents`: same replacement on `qs[offset : offset + limit]`; drop the unused `serialize_session` import there too. The comment above the `row["process"] = None` loop (`session.py:394-397`) says "this resolves no pid and runs no query"; the enrichment now reads project directories (one query on a cache miss). Scope the comment to the `process` block: "so the `process` block resolves no pid and runs no query".
  - `sessions_get.py`: import `CLI_ENRICHED_KEYS` and `cli_session_payloads` at module top, next to `from twicc.cli._output import …` (`_session_payload.py` imports nothing Django-bound at module level, so this keeps `--help` fast). `_build_placeholder_template` returns `{k: None for k in serialize_session(sample)} | dict.fromkeys(CLI_ENRICHED_KEYS)` and, in the fallback, `{"id": None} | dict.fromkeys(CLI_ENRICHED_KEYS)`. In `main`, enrich the known rows in one call: `known_ids = [sid for sid in unique_ids if sid in sessions_by_id]`, `enriched = dict(zip(known_ids, cli_session_payloads(sessions_by_id[sid] for sid in known_ids), strict=True))`, then `entry = project(enriched[sid])` for a known id. `main`'s local `from twicc.core.serializers import serialize_session` (`sessions_get.py:64`) becomes unused: drop it (ruff F401); `_build_placeholder_template` keeps its own import. Update the module docstring: placeholder keys = serializer keys + `CLI_ENRICHED_KEYS`, all `null`. Update the two texts that describe the template: the comment above `_PLACEHOLDER_TEMPLATE` (`:26-29`, "derive it lazily from one real ``serialize_session`` output … never drifts from the canonical serializer") gains "plus the keys the CLI enrichment adds (``CLI_ENRICHED_KEYS``)", and the `_build_placeholder_template` docstring (`:34-39`, "matching ``serialize_session`` output … Falls back to a minimal ``{"id": None}`` shape") becomes "matching ``serialize_session`` output plus ``CLI_ENRICHED_KEYS`` … Falls back to ``{"id": None}`` plus ``CLI_ENRICHED_KEYS`` if the DB has no session yet".

- [ ] **Step 4: Run, verify they pass.**
Run: `uv run pytest tests/test_cli_session_payload.py tests/test_cli_session_process_state.py tests/test_cli_pagination_envelope.py tests/test_slim_cutover.py tests/test_cli_resolve_filters.py -q`, then the whole suite once (`uv run pytest -q`, in the background): the new `conftest.py` fixture touches every test. No module imports the dict `_project_directories` by name — `grep -rn "[^d]_project_directories" --include=*.py src tests` (the `[^d]` skips `load_project_directories`) shows only `src/twicc/projects.py`, the new tests and the new `conftest.py` fixture — so the rebinding reaches every reader.
Expected: PASS. If a test in those files asserted `artifacts_dir is None` or a stored `null` setting on a session row, update it to the new rule and say why in its docstring (owner's exception).

- [ ] **Step 5: Lint.** `uvx ruff check src/twicc/cli/_session_payload.py src/twicc/cli/sessions.py src/twicc/cli/sessions_get.py src/twicc/cli/session.py tests/test_cli_session_payload.py tests/conftest.py`

**Docs:** Task 11 and Task 13.

---

### Task 5: The reduced projection grows; its help texts (§2 "The reduced projection", "Help texts")

**Files:**
- Modify: `src/twicc/core/serializers.py:113-145` (comment and `SESSION_LISTING_FIELDS`)
- Modify: `src/twicc/cli/_output.py` `SLIM_HELP` `:381`, `FULL_HELP` `:392`, `_TOPOLOGY_FULL_LEAD` `:409`
- Test: `tests/test_slim_cutover.py` (`assert_full` `:121-127`, new test), any test asserting a now-kept field is absent (`tests/test_cli_session_process_state.py:269` reads `SESSION_LISTING_FIELDS` itself and follows the tuple: no change expected)

**Interfaces:**
- Consumes: `cli_session_payloads` (Task 4) — four of the new fields exist only because the enrichment runs before `slim_session`.
- Produces: `SESSION_LISTING_FIELDS` with the sixteen new names.

**Behaviour:** `--slim` (unreleased) rows gain sixteen fields now. From the date the reduced projection is the default, with those fields. Flagless calls before the date are unchanged (full payload).

- [ ] **Step 1: Write the failing test** in `tests/test_slim_cutover.py`:

```python
NEW_SLIM_FIELDS = {
    "last_line", "cwd", "git_directory", "project_directory", "artifacts_dir",
    "scratch_dir", "orchestration_scratch_dir", "compacted", "hybrid",
    "permission_mode", "selected_model", "effort", "thinking_enabled",
    "claude_in_chrome", "fast_mode", "question_widget",
}
DROPPED_FIELDS = {
    "tasks", "plan_paths", "goals", "layout", "last_started_at", "last_updated_at",
    "last_stopped_at", "last_viewed_at", "mtime", "self_cost", "subagents_cost",
    "slug", "browser_url", "compute_version_up_to_date",
}


def test_the_reduced_projection_keeps_everything_but_the_dropped_fields(tree, capsysbinary):
    assert NEW_SLIM_FIELDS <= set(SESSION_LISTING_FIELDS)
    assert "context_max" in SESSION_LISTING_FIELDS
    slim_rows, _ = listing("sessions", tree, capsysbinary, slim=True)
    full_rows, _ = listing("sessions", tree, capsysbinary, full=True)
    for slim_row, full_row in zip(slim_rows, full_rows, strict=True):
        assert set(full_row) - set(slim_row) == DROPPED_FIELDS
        assert set(slim_row["process"]) == {"state"}
```

  Change `assert_full` (`:121-127`): `assert "cwd" in row` → `assert "layout" in row, "a field only the full payload carries"` (spec §7). The topology tests (`:280-310`) keep `"cwd" in node["session"]`: a topology node is the serializer payload, not the listing projection.

- [ ] **Step 2: Run, verify it fails.** `uv run pytest tests/test_slim_cutover.py -q` — FAIL on the new test.

- [ ] **Step 3: Implement.** Add to `SESSION_LISTING_FIELDS`:

```python
    # Detail a caller acts on: where the session runs and writes, and the
    # agent settings it runs with. Four of these (project_directory,
    # scratch_dir, orchestration_scratch_dir, question_widget) come from the
    # CLI enrichment (twicc/cli/_session_payload.py), which runs before this
    # projection.
    "last_line", "cwd", "git_directory", "project_directory", "artifacts_dir",
    "scratch_dir", "orchestration_scratch_dir", "compacted", "hybrid",
    "permission_mode", "selected_model", "effort", "thinking_enabled",
    "claude_in_chrome", "fast_mode", "question_widget",
```

  Rewrite the comment above the tuple (`:113-128`) to the new rule: everything except what is verbose or of no use to a caller — the per-session blobs (`tasks`, `plan_paths`, `goals`, `layout`), the redundant timestamps (`mtime`, `last_started_at`, `last_updated_at`, `last_stopped_at`, `last_viewed_at`), the cost breakdown (`self_cost`, `subagents_cost`), `slug`, `browser_url`, `compute_version_up_to_date`. Its first sentence ("The reduced projection a listing returns by default (or with ``--slim`` before the cutover)") names every command that returns it: "…that `sessions`, `sessions get`, `session agents`, `session <id>` and `whoami` return by default from the cutover (or with `--slim` before it)". Keep the paragraph about `TOPOLOGY_SESSION_FIELDS` (why the two tuples stay distinct), but drop its last sentence's figure ("Merging them would make a 332-node topology 17% heavier to serve a listing concern."): it was measured on the old 27-field tuple and is not re-measured — write "Merging them would make every topology node carry listing-only fields."

  `_output.py`: set `SLIM_HELP` (before side), `FULL_HELP` (both sides) and `_TOPOLOGY_FULL_LEAD` to the exact texts of spec §2 "Help texts", with one wording correction in `FULL_HELP`: "every field of the session" → "every field of the session payload" on both sides (the serializer leaves out stored columns such as `file_path`, `last_offset`, `compute_version`). `SLIM_HELP` becomes (mind the doubled braces: the literal `{state}` sits in an f-string):

```python
SLIM_HELP = cutover_help(
    "Return a reduced projection of each session: every field except the "
    "payloads you fetch per session (tasks, plan_paths, goals, layout), the "
    "redundant timestamps (mtime, last_started_at, last_updated_at, "
    "last_stopped_at, last_viewed_at), the cost breakdown (self_cost, "
    "subagents_cost), slug, browser_url and compute_version_up_to_date; its "
    f"`process` block is {{state}} (null on a subagent). Off by default until {_CUTOVER_DATE}, when it "
    "becomes the default and this flag turns into an accepted no-op.",
    "Accepted and ignored: the reduced projection is the default. Kept so scripts "
    "that migrated during the deprecation window keep working untouched.",
)
```

  Any other new help text that contains a literal `{…}` inside an f-string (`{items}`, `{peers}`, `{state}`, `{items, pagination}`) doubles its braces the same way; a plain (non-f) string keeps single braces. `_TOPOLOGY_FULL_LEAD` keeps its trailing "Disabled by default: …" sentence after the new lead. One correction to the spec's `_TOPOLOGY_FULL_LEAD` text: "every stored session field" is inaccurate — `serialize_session` (`src/twicc/core/serializers.py:183-285`) leaves out many stored columns (`file_path`, `last_offset`, `compute_version`, `cwd_git_branch`, `question_widget`, …) and the CLI enrichment keys. Write "Emit the full serializer payload for every node — agent settings as stored, `artifacts_dir` as the serializer reports it (set only once the backend has seen an artifact, so always `null` from a terminal), none of the CLI-added keys (`project_directory`, `scratch_dir`, `orchestration_scratch_dir`, `question_widget`) — minus its `process` block, which sits at `nodes[].process` — and that full `process` block". Use the same wording in `twicc-topology/SKILL.md:44,129` (Task 11).

- [ ] **Step 4: Run, verify they pass.**
Run: `uv run pytest tests/test_slim_cutover.py tests/test_cli_session_process_state.py tests/test_cli_pagination_envelope.py tests/test_cli_session_payload.py -q` and `uv run pytest -q -k topology`
Expected: PASS. Fix any test that asserted a now-kept field is absent from a slim row (spec §2).

- [ ] **Step 5: Lint.** `uvx ruff check src/twicc/core/serializers.py src/twicc/cli/_output.py tests/test_slim_cutover.py`

**Docs:** `twicc-sessions/SKILL.md` examples and `twicc-topology/SKILL.md:44,129` in Task 11.

---

### Task 6: `session <id>` — keywords, lookup rule, `--slim` / `--full`, dated default (§2)

**Files:**
- Create: `src/twicc/cli/_session_group.py`
- Modify: `src/twicc/cli/__init__.py:625-645` (`session_app`, `_session_default`)
- Modify: `src/twicc/cli/session.py:68-99` (`main`); add `build_session_payload`
- Modify: `src/twicc/cli/_remote.py:101-106` (comment) and `_navigate` (`:197-248`, the `--slim` / `--full` refusal in resilient mode)
- Modify: `src/twicc/cli/_session_keywords.py:1-10` (module docstring), `src/twicc/cli/topology.py:10-15` (comment), `src/twicc/cli/_output.py:443-444` (the comment above `SLIM_CUTOVER_NOTICE`)
- Create: `tests/test_cli_session_command.py`
- Modify: `tests/test_session_keywords.py` (remote preflight parametrization)
- Adjust: `tests/test_cli_session_process_state.py:696-707`, `tests/test_slim_cutover.py` (`:311-316`, `:363`, `:374`)

**Interfaces:**
- Consumes: `cli_session_payloads` (Task 4), grown projection (Task 5), `resolve_session_keyword` / `SELF_PARENT_KEYWORDS` (`src/twicc/cli/_session_keywords.py`).
- Produces: `SessionGroup(TyperGroup)` and `HELP_REQUESTED` (the `ctx.meta` key); `session.build_session_payload(session, *, slim: bool) -> dict`; `session.main(session_id: str, *, slim: bool = False, full: bool = False) -> None`; `ctx.obj` holds the **resolved** id for every `session` subcommand.

**Behaviour:**

| Call | Before the date | After |
|---|---|---|
| `session X` | full + enrichment + notice (terminal / RPC; none on MCP) | reduced, `process: {state}` |
| `session X --full` / `session --full X` | full, no notice | full |
| `session X --slim` | reduced | reduced |
| `session self`, `session parent` (and `session self messages`, `session self stop`, …) | resolved (now) | same |
| `session X` on a row with no `created_at` / no user message | the row (now; was exit 1) | same |
| `session X --full agents`, `session --full X agents` | exit 2 | exit 2 |
| `session X --slim --full` | exit 2 | exit 2 |
| `session self messages --help` outside a session | prints help | same |

- [ ] **Step 1: Write the failing tests** in `tests/test_cli_session_command.py`:

```python
"""`session <id>`: flags on both sides of the id, keywords, lookup rule, date."""

from __future__ import annotations

import asyncio
import importlib
from datetime import datetime

import orjson
import pytest
from typer.testing import CliRunner

from twicc.cli import _output, app
from twicc.cli import session as cli_session
from twicc.core.models import Project, Session, SessionType
from twicc.rpc.generator import build_registry, render_argv
from twicc.rpc.invoker import invoke

PAST = datetime(2000, 1, 1)     # noqa: DTZ001
FUTURE = datetime(2200, 1, 1)   # noqa: DTZ001
NOTICE = "returns the reduced session projection by default"


@pytest.fixture
def before(monkeypatch):
    monkeypatch.setattr(_output, "LISTING_CUTOVER", FUTURE)


@pytest.fixture
def after(monkeypatch):
    monkeypatch.setattr(_output, "LISTING_CUTOVER", PAST)


@pytest.fixture(autouse=True)
def no_backend(monkeypatch):
    monkeypatch.setattr("twicc.cli._twicc_info.resolve_live_twicc", lambda: None)


@pytest.fixture
def rows(db):
    project = Project.objects.create(id="-tmp-sc", directory="/tmp/sc")
    root = Session.objects.create(
        id="sc-root", project=project, provider="claude_code", file_path="r.jsonl",
        type=SessionType.SESSION, created_at="2026-09-20T10:00:00Z", user_message_count=1,
    )
    Session.objects.create(  # no created_at, no user message
        id="sc-child", project=project, provider="claude_code", file_path="c.jsonl",
        type=SessionType.SESSION, spawned_by=root, spawn_root=root,
    )
    Session.objects.create(
        id="sc-sub", project=project, provider="claude_code", file_path="s.jsonl",
        type=SessionType.SUBAGENT, parent_session=root, created_at="2026-09-20T10:00:00Z",
        user_message_count=1,
    )
    return project


def cli(*argv):
    return CliRunner().invoke(app, list(argv))


def first_row(result):
    return result[0] if isinstance(result, list) else result["items"][0]


@pytest.mark.parametrize("argv", [
    ("session", "sc-root", "--full"),
    ("session", "--full", "sc-root"),
    ("session", "--full", "--", "sc-root"),
])
def test_full_on_either_side_of_the_id(after, rows, argv):
    result = cli(*argv)
    assert result.exit_code == 0, result.output
    assert "layout" in orjson.loads(result.stdout)
    # Spec § Tests: the orders go through `invoke` too (the /rpc/ path).
    through_rpc = invoke(list(argv))
    assert through_rpc.exit_code == 0, through_rpc.error
    assert "layout" in through_rpc.result


@pytest.mark.parametrize("argv", [
    ("session", "sc-root", "--full", "agents"),
    ("session", "--full", "sc-root", "agents"),
])
def test_a_group_flag_before_a_subcommand_is_refused(rows, argv):
    result = invoke(list(argv))
    assert result.exit_code == 2
    assert "--slim / --full apply to `session <id>` alone, not to `agents`" in result.error


def test_the_subcommand_keeps_its_own_flag(after, rows):
    result = invoke(["session", "sc-root", "agents", "--full"])
    assert result.exit_code == 0, result.error
    assert "layout" in first_row(result.result)


def test_both_flags_are_refused(rows):
    result = invoke(["session", "sc-root", "--slim", "--full"])
    assert result.exit_code == 2
    assert "--slim and --full are mutually exclusive" in result.error


def test_the_mcp_argv_is_the_bare_call(after, rows):
    registry = build_registry()
    argv = render_argv(registry["session"], {"session_id": "sc-root", "full": True})
    assert argv == ["session", "--full", "--", "sc-root"]
    result = invoke(argv)
    assert result.exit_code == 0, result.error
    assert "layout" in result.result
    assert render_argv(registry["session/agents"], {"session_id": "X", "full": True}) == [
        "session", "X", "agents", "--full",
    ]


def test_before_the_flagless_call_is_full_and_announced(before, rows, capsysbinary):
    cli_session.main("sc-root")
    out, err = capsysbinary.readouterr()
    row = orjson.loads(out)
    assert "layout" in row and "project_directory" in row
    assert f"`session` {NOTICE}" in err.decode()


@pytest.mark.parametrize("flags", [{"full": True}, {"slim": True}])
def test_before_a_flag_is_unannounced(before, rows, capsysbinary, flags):
    cli_session.main("sc-root", **flags)
    _, err = capsysbinary.readouterr()
    assert err == b""


def test_after_the_flagless_call_is_reduced(after, rows, capsysbinary):
    cli_session.main("sc-root")
    out, err = capsysbinary.readouterr()
    row = orjson.loads(out)
    assert "layout" not in row
    assert set(row["process"]) == {"state"}
    assert err == b""


def test_a_row_with_no_user_message_is_served(before, rows, capsysbinary):
    cli_session.main("sc-child", full=True)
    assert orjson.loads(capsysbinary.readouterr().out)["id"] == "sc-child"


def test_no_row_exits_1(rows):
    result = invoke(["session", "nope", "--full"])
    assert result.exit_code == 1
    assert "not found" in result.error


def test_the_transcript_readers_still_refuse_such_a_row(rows):
    assert invoke(["session", "sc-child", "messages"]).exit_code == 1


def test_a_subagent_keeps_stored_settings_and_no_process(before, rows, capsysbinary):
    """Review focus 5."""
    cli_session.main("sc-sub", full=True)
    row = orjson.loads(capsysbinary.readouterr().out)
    assert row["process"] is None
    assert row["selected_model"] is None


def test_self_and_parent_resolve(rows, monkeypatch):
    child = Session.objects.get(id="sc-child")
    monkeypatch.setattr("twicc.cli._drop_request.whoami.resolve_current_session", lambda: child)
    assert invoke(["session", "self", "--full"]).result["id"] == "sc-child"
    assert invoke(["session", "parent", "--full"]).result["id"] == "sc-root"
    assert invoke(["session", "parent", "messages"]).exit_code == 0


def test_self_reaches_a_subcommand(rows, monkeypatch):
    """Spec § Tests: `session self messages` gets the resolved id in ctx.obj."""
    root = Session.objects.get(id="sc-root")
    monkeypatch.setattr("twicc.cli._drop_request.whoami.resolve_current_session", lambda: root)
    seen = []
    monkeypatch.setattr(
        "twicc.cli.session.messages", lambda session_id, **kw: seen.append(session_id),
    )
    assert invoke(["session", "self", "messages"]).exit_code == 0
    assert seen == ["sc-root"]


@pytest.mark.parametrize("pinned", [FUTURE, PAST])
def test_full_is_the_full_payload_on_both_sides(monkeypatch, rows, capsysbinary, pinned):
    monkeypatch.setattr(_output, "LISTING_CUTOVER", pinned)
    cli_session.main("sc-root", full=True)
    out, err = capsysbinary.readouterr()
    row = orjson.loads(out)
    assert "layout" in row and "slug" in row and "mtime" in row
    assert set(row["process"]) == {"id", "state", "started_at", "last_state_change_at", "pid"}
    assert err == b""


def test_self_is_refused_outside_a_session(rows, monkeypatch):
    """Refused by the keyword resolution, not by a lookup of the literal id
    "self" (which also exits 1 today, for the wrong reason)."""
    monkeypatch.setattr("twicc.cli._drop_request.whoami.resolve_current_session", lambda: None)
    result = invoke(["session", "self"])
    assert result.exit_code == 1
    assert result.result["errors"][0]["code"] == "session_context_not_found"


@pytest.mark.parametrize("argv", [["session", "parent"], ["session", "parent", "messages"]])
def test_parent_is_refused_for_a_root_session(rows, monkeypatch, argv):
    """Spec § Tests: refused like the other keyword call sites — a root
    session has no spawner."""
    root = Session.objects.get(id="sc-root")
    monkeypatch.setattr("twicc.cli._drop_request.whoami.resolve_current_session", lambda: root)
    result = invoke(argv)
    assert result.exit_code == 1
    assert result.result["errors"][0]["code"] == "parent_not_found"


def test_help_after_a_keyword_needs_no_session(monkeypatch):
    monkeypatch.setattr("twicc.cli._drop_request.whoami.resolve_current_session", lambda: None)
    result = cli("session", "self", "messages", "--help")
    assert result.exit_code == 0, result.output
    assert "Usage" in result.output


def test_session_self_stop_submits_the_resolved_id(rows, monkeypatch):
    child = Session.objects.get(id="sc-child")
    monkeypatch.setattr("twicc.cli._drop_request.whoami.resolve_current_session", lambda: child)
    seen = []
    # Not the dotted string "twicc.cli.process_stop.stop_cmd": depending on
    # import order, `twicc.cli.process_stop` is the `process stop` command
    # function (src/twicc/cli/__init__.py:1771), which hides the submodule.
    # Patch the module object itself.
    monkeypatch.setattr(
        importlib.import_module("twicc.cli.process_stop"), "stop_cmd",
        lambda session_id, **kw: seen.append(session_id),
    )
    invoke(["session", "self", "stop", "--timeout", "1"])
    assert seen == ["sc-child"]


@pytest.mark.django_db(transaction=True)
def test_self_through_the_mcp_identity(monkeypatch):
    """Review focus 3: the MCP-forced identity, not the PID ancestry."""
    from twicc.mcp import server as mcp_server

    project = Project.objects.create(id="-tmp-sc-mcp", directory="/tmp/sc-mcp")
    Session.objects.create(
        id="sc-mcp", project=project, provider="claude_code", file_path="m.jsonl",
        type=SessionType.SESSION,
    )
    result = asyncio.run(mcp_server.dispatch_tool(
        "session", {"session_id": "self", "full": True}, session_id="sc-mcp",
    ))
    assert result["exit_code"] == 0, result
    assert result["result"]["id"] == "sc-mcp"
```

  `_session_stop` imports `stop_cmd` lazily inside the function (`src/twicc/cli/__init__.py:824-829`), so patching the attribute on the **module object** (`importlib.import_module("twicc.cli.process_stop")`) works. The dotted string `"twicc.cli.process_stop.stop_cmd"` is unreliable: depending on import order, `twicc.cli.process_stop` is either the `process stop` command function defined at `src/twicc/cli/__init__.py:1771` (which hides the submodule, and monkeypatch raises `AttributeError`) or, once the submodule has been imported, the module itself. `importlib.import_module` returns the module in both cases.

  `emit_validation_errors` (`src/twicc/cli/_drop_request/output.py:17-21`) emits `{"status": "validation_error", "errors": [{"field", "code", "message"}]}` through `emit_json`, so `invoke` returns it in `result.result` alongside exit 1.

  Adjust existing tests:
  - `tests/test_cli_session_process_state.py::test_it_is_the_same_block_the_listing_builds` (`:696`): `cli_session.main("s1", full=True)`.
  - `tests/test_slim_cutover.py:311-316`: add `["session", "slim-root", "--slim", "--full"]` to the parametrization.
  - `test_the_flags_reach_the_mcp_schema` (`:363`): add `"session"` to the loop; assert `{"slim", "full"}.isdisjoint(p.name for p in registry["session/messages"].params)` (no subcommand gets the group flags); `session/agents` keeps its own (already in the loop).
  - `test_the_help_texts_match_the_side_of_the_cutover_we_are_on` (`:374`): add `"session"` to `announcing`.

- [ ] **Step 2: Run, verify they fail.** `uv run pytest tests/test_cli_session_command.py -q` — FAIL (no such option `--full`; keyword not resolved). Four tests already pass today and are regression guards, not proofs: `test_the_subcommand_keeps_its_own_flag`, `test_the_transcript_readers_still_refuse_such_a_row`, `test_help_after_a_keyword_needs_no_session` and — only for its exit code — `test_self_is_refused_outside_a_session` (its `session_context_not_found` assertion fails today with `TypeError: 'NoneType' object is not subscriptable`: the literal id "self" is looked up and not found, and that error carries no `result`).

- [ ] **Step 3: Implement.**
  `src/twicc/cli/_session_group.py`:

```python
"""The ``session`` group: ``--slim`` / ``--full`` accepted after the id too.

A Click group does not intersperse arguments: in ``session X --full`` the
parser stops at ``X`` and reads ``--full`` as a subcommand name. This group
moves a run of ``--slim`` / ``--full`` that directly follows the id and ends
the argv in front of the id, and refuses one that precedes a subcommand.
Design: docs/plans/2026-09-23-cli-consistency-before-cutover-design.md, §2.
"""

from __future__ import annotations

from typer.core import TyperGroup

from twicc.cli._output import emit_error

_GROUP_FLAGS = ("--slim", "--full")

#: ``ctx.meta`` key: a help option follows the id, so the callback must not
#: resolve ``self`` / ``parent`` (the help must print outside a session).
HELP_REQUESTED = "twicc.session_help"


class SessionGroup(TyperGroup):
    def parse_args(self, ctx, args):
        args = list(args)
        index = 0
        while index < len(args) and args[index].startswith("-"):
            if args[index] == "--":
                # The argv render_argv builds for MCP / RPC: already parseable.
                return super().parse_args(ctx, args)
            index += 1
        if index < len(args):
            rest = args[index + 1:]
            # Tokens after `--` are values, never a help request.
            before_dashdash = rest[:rest.index("--")] if "--" in rest else rest
            ctx.meta[HELP_REQUESTED] = any(
                token in ctx.help_option_names for token in before_dashdash
            )
            run_length = 0
            while run_length < len(rest) and rest[run_length] in _GROUP_FLAGS:
                run_length += 1
            if run_length:
                following = rest[run_length:]
                if not following:
                    args = args[:index] + rest[:run_length] + [args[index]]
                elif following[0] in self.commands and not ctx.resilient_parsing:
                    emit_error(
                        "Error: --slim / --full apply to `session <id>` alone, "
                        f"not to `{following[0]}`.",
                        code=2,
                    )
        return super().parse_args(ctx, args)
```

  `src/twicc/cli/__init__.py`:

```python
# Placed with the other module-level imports of __init__.py that follow
# ensure_env_loaded() (hence the noqa), e.g. right after the `_output` block.
from twicc.cli._session_group import HELP_REQUESTED, SessionGroup  # noqa: E402

session_app = typer.Typer(
    name="session",
    cls=SessionGroup,
    help=SLIM_CUTOVER_NOTICE + "Inspect a session.",
    invoke_without_command=True,
)
app.add_typer(session_app)


@session_app.callback(invoke_without_command=True)
def _session_default(
    ctx: typer.Context,
    session_id: str = typer.Argument(help=(
        "The session ID (for normal sessions or agents) to look up, or 'self' "
        "(your own session) or 'parent' (the session that spawned you)."
    )),
    slim: bool = typer.Option(False, "--slim", help=SLIM_HELP),
    full: bool = typer.Option(False, "--full", help=FULL_HELP),
) -> None:
    """Show a single session as JSON."""
    if ctx.invoked_subcommand is not None and (slim or full):
        emit_error(
            "Error: --slim / --full apply to `session <id>` alone, not to "
            f"`{ctx.invoked_subcommand}`.",
            code=2,
        )
    if slim and full:
        emit_error("Error: --slim and --full are mutually exclusive.", code=2)
    if not ctx.resilient_parsing and not ctx.meta.get(HELP_REQUESTED):
        from twicc.cli._session_keywords import SELF_PARENT_KEYWORDS, resolve_session_keyword

        session_id = resolve_session_keyword(
            session_id, param_name="SESSION_ID", allowed=SELF_PARENT_KEYWORDS,
        )
    ctx.obj = session_id
    if ctx.invoked_subcommand is not None:
        return

    from twicc.cli.session import main as session_main

    session_main(session_id, slim=slim, full=full)
```

  Click sets `ctx.invoked_subcommand` before it runs the group callback, so the first check sees `session --full X agents`.

  `src/twicc/cli/session.py`:

```python
def build_session_payload(session, *, slim: bool) -> dict:
    """The row `session <id>` emits: enriched, projected, then its process block.

    Shared with `whoami` (new shape), which already holds the row.
    """
    from twicc.cli._process_state import (
        attach_process_blocks,
        load_process_rows,
        resolve_listing_twicc_pid,
    )
    from twicc.cli._session_payload import cli_session_payloads
    from twicc.core.serializers import slim_session

    data = cli_session_payloads([session])[0]
    if slim:
        data = slim_session(data)
    # A subagent runs inside its parent's process: nothing to read for one,
    # not even the pid.
    rows = (
        {} if session.parent_session_id is not None
        else load_process_rows([session.id], resolve_listing_twicc_pid())
    )
    attach_process_blocks([data], rows, slim=slim)
    return data


def main(session_id: str, *, slim: bool = False, full: bool = False) -> None:
    """Print one session row: any ``Session`` row with that id, as ``sessions get``.

    Carries the same ``process`` block ``sessions`` puts on every row, built by
    the same helper: the two commands take the same argument and name the same
    thing, so answering the live state on one and omitting it on the other
    sent a caller after a single session through the listing to get it.

    Reduced by default from the cutover (full plus a notice before it);
    ``--full`` / ``--slim`` choose. The transcript readers (content, messages,
    agents, plan, workflows, workflow) keep ``_get_session``; this reads
    metadata, which exists as soon as the row does.
    """
    import django

    django.setup()
    slim = slim_notice("session", slim, full)

    from twicc.core.models import Session

    session = Session.objects.filter(id=session_id).first()
    if session is None:
        emit_error(f"Error: session '{session_id}' not found.", code=1)
    emit_json(build_session_payload(session, slim=slim))
```

  `src/twicc/cli/_remote.py:101-106`: `session` (and its subcommands) moves into the list of commands that truly resolve `self` / `parent` (`send-message`, `update-session`, `topology`, `session`), where rejecting them over `--remote` is required; `process` / `process stop` / `process wait` stay in the "treat the id literally" sentence.

  **`--remote` parses in resilient mode** (`_remote._make_context`, `:142-144`), where `SessionGroup` never refuses (it checks `ctx.resilient_parsing`, which shell completion also sets). Without a guard, `twicc --remote URL session --full X agents` passes the local pre-flight (it navigates to `session/agents`; the leaf's own `full=False` overwrites the group's `full=True` in `merged`, which only feeds the pre-flight checks) and is refused only by the remote server, after the HTTP round trip: `forward()` posts the original argv (`_remote.py:777`), which the remote `SessionGroup` refuses with exit 2. And `session X --full agents` is reported locally as a generic "unknown command". Add the refusal to `_navigate`, for the `session` level only, so both forms fail early, locally, with the same text as the local CLI (printed under the forwarder's `twicc:` prefix, as every `RemoteUsageError` is, where the local CLI prints `Error: …`; exit 2 in both cases):

```python
# At the top of _remote.py, with the other `twicc.cli` imports:
from twicc.cli._session_group import SessionGroup

# Module level, right above `_make_context` (after `_HOST_BOUND_KEYWORDS`):
_SESSION_GROUP_FLAG_MISUSE = "--slim / --full apply to `session <id>` alone, not to `{sub}`."

        # inside the `while True:` loop, right after `remaining = _remaining_tokens(ctx)`
        # and its `if not remaining:` branch:
        if isinstance(cmd, SessionGroup):
            # The refusal only when a registered subcommand follows, as the
            # local SessionGroup does (step 5); anything else falls through to
            # the generic "unknown command" below, as Click reports it locally
            # (step 6).
            run_end = 0
            while run_end < len(remaining) and remaining[run_end] in ("--slim", "--full"):
                run_end += 1
            if run_end and run_end < len(remaining) and remaining[run_end] in cmd.commands:
                # `session X --full agents`: resilient mode left the run in place.
                raise RemoteUsageError(_SESSION_GROUP_FLAG_MISUSE.format(sub=remaining[run_end]))
            if (ctx.params.get("slim") or ctx.params.get("full")) and remaining[0] in cmd.commands:
                # `session --full X agents`: the group took the flag, a subcommand follows.
                raise RemoteUsageError(_SESSION_GROUP_FLAG_MISUSE.format(sub=remaining[0]))
```

  Add this case to the remote tests below:

```python
def test_remote_reports_an_unknown_token_after_the_flag_as_unknown():
    from twicc.cli._remote import RemoteUsageError, resolve_command

    with pytest.raises(RemoteUsageError, match="unknown command") as exc:
        resolve_command(["session", "abc", "--full", "bogus"])
    assert "apply to `session <id>` alone" not in str(exc.value)
```

  For `session X --full` alone, `SessionGroup.parse_args` has already moved the run in front of the id, so `remaining` is empty and the bare route is taken before this check. Importing `twicc.cli._session_group` from `_remote.py` creates no cycle: `_session_group` imports only `typer.core` and `twicc.cli._output`.

  Tests, in `tests/test_session_keywords.py` next to the remote preflight tests:

```python
@pytest.mark.parametrize("argv", [
    ["session", "--full", "abc", "agents"],
    ["session", "abc", "--full", "agents"],
    ["session", "abc", "--slim", "messages"],
])
def test_remote_refuses_a_group_flag_before_a_subcommand(argv):
    from twicc.cli._remote import RemoteUsageError, resolve_command

    with pytest.raises(RemoteUsageError, match="apply to `session <id>` alone"):
        resolve_command(argv)


@pytest.mark.parametrize("argv", [
    ["session", "abc", "--full"],
    ["session", "--full", "abc"],
])
def test_remote_accepts_the_group_flag_on_the_bare_call(argv):
    from twicc.cli._remote import resolve_command

    resolved = resolve_command(argv)
    assert resolved.path == "session"
    assert resolved.params["full"] is True
```

  And add `["session", "KEYWORD"]` and `["session", "KEYWORD", "messages"]` to the `argv_template` parametrization of `test_remote_preflight_rejects_every_keyword_call_site_before_http` (`:155-163`): `session_id` is in `HOST_BOUND_PARAMS`, so both keywords are refused before any HTTP call. Update that test's docstring ("all four real command shapes" → "every real command shape").

  `src/twicc/cli/_session_keywords.py:1-10`: the module docstring counts "the four call sites this lot touches"; add `session <id>` (and its subcommands) as a later call site with the same contract (structured `validation_error`, exit 1).

  `src/twicc/cli/topology.py:10-15`: "any other field can be recovered for a specific node via ``twicc session <id>``" → "via ``twicc session <id> --full``" (from 2026-10-01 the bare call is reduced).

  `src/twicc/cli/_output.py:443-444`, the comment above `SLIM_CUTOVER_NOTICE`: "Prepended to the help of the three session listings while the full payload is still their default." → "Prepended to the help of the session commands whose default becomes the reduced projection (`sessions`, `sessions get`, `session agents`, `session <id>`) while the full payload is still their default."

- [ ] **Step 4: Run, verify they pass.**
Run: `uv run pytest tests/test_cli_session_command.py tests/test_cli_session_process_state.py tests/test_slim_cutover.py tests/test_process_commands_removal.py tests/test_mcp_tools.py tests/test_mcp_server.py -q`, then every file listed by `grep -ln "HOST_BOUND\|--remote\|_remote" tests/*.py`.
Expected: PASS.

- [ ] **Step 5: Lint.** `uvx ruff check src/twicc/cli/_session_group.py src/twicc/cli/__init__.py src/twicc/cli/session.py src/twicc/cli/_remote.py src/twicc/cli/_session_keywords.py src/twicc/cli/topology.py src/twicc/cli/_output.py tests/test_cli_session_command.py tests/test_session_keywords.py tests/test_cli_session_process_state.py tests/test_slim_cutover.py`

**Docs:** `twicc-session/SKILL.md`, `twicc-sessions/SKILL.md:201`, `SKILLS-AND-CLI.md:18,40-41,255` in Task 11.

---

### Task 7: `whoami` — `--slim` / `--full`, dated default (§2 "`whoami`")

**Files:**
- Modify: `src/twicc/cli/whoami.py`
- Modify: `src/twicc/cli/_output.py` (new `WHOAMI_CUTOVER_NOTICE`, `WHOAMI_HELP`, `WHOAMI_SLIM_HELP`, `WHOAMI_FULL_HELP`; `slim_notice` gains `kind="whoami"`)
- Modify: `src/twicc/cli/__init__.py:2049-2052` (registration), `src/twicc/cli/_drop_request/whoami.py:17-19` (module docstring)
- Create: `tests/test_cli_whoami_cutover.py`
- Adjust: `tests/test_mcp_server.py:33-51`, `tests/test_process_commands_removal.py:342-358`, `tests/test_slim_cutover.py` (`:363`, `:374`, one new test)

**Interfaces:**
- Consumes: `session.build_session_payload` (Task 6).
- Produces: `whoami_cmd(slim: bool = typer.Option(...), full: bool = typer.Option(...))`. Direct callers pass both arguments (Global Constraints).

**Behaviour:**

| Call | Before the date | After |
|---|---|---|
| `whoami` (terminal) | today's object + notice on stderr | `session self` reduced |
| `whoami` (MCP) | today's object, no notice | `session self` reduced |
| `whoami --full` / `--slim` | `session self --full` / `--slim` | same |
| `whoami --slim --full` | exit 2, even outside a session | same |
| outside a session | exit 1, today's message | same |

- [ ] **Step 1: Write the failing tests** in `tests/test_cli_whoami_cutover.py`:

```python
"""`whoami`: today's object until the date, the `session self` payload after."""

from __future__ import annotations

import asyncio
from datetime import datetime

import orjson
import pytest
import typer
from django.utils import timezone

from twicc.agent.states import AgentState
from twicc.cli import _output
from twicc.cli import session as cli_session
from twicc.cli.whoami import whoami_cmd
from twicc.core.models import ProcessRun, Project, Session, SessionType

PAST = datetime(2000, 1, 1)     # noqa: DTZ001
FUTURE = datetime(2200, 1, 1)   # noqa: DTZ001
TWICC_PID = 6161
LEGACY_KEYS = {
    "session_id", "title", "project_id", "project_directory", "current_working_directory",
    "artifacts_dir", "scratch_dir", "agent_settings", "session", "process",
}


@pytest.fixture
def before(monkeypatch):
    monkeypatch.setattr(_output, "LISTING_CUTOVER", FUTURE)


@pytest.fixture
def after(monkeypatch):
    monkeypatch.setattr(_output, "LISTING_CUTOVER", PAST)


@pytest.fixture
def me(db, monkeypatch):
    project = Project.objects.create(id="-tmp-who", directory="/tmp/who")
    session = Session.objects.create(
        id="who-me", project=project, provider="claude_code", file_path="w.jsonl",
        type=SessionType.SESSION,
    )
    now = timezone.now()
    ProcessRun.objects.create(
        session_id=session.id, provider="claude_code", twicc_pid=TWICC_PID,
        state=AgentState.ASSISTANT_TURN.value, started_at=now, last_state_change_at=now,
    )
    monkeypatch.setattr("twicc.cli._drop_request.whoami.resolve_current_session", lambda: session)
    info = type("I", (), {"pid": TWICC_PID})()
    monkeypatch.setattr("twicc.cli._twicc_info.resolve_live_twicc", lambda: info)
    monkeypatch.setattr("twicc.cli._twicc_info.resolve_live_twicc_or_exit", lambda: info)
    return session


def run(capsysbinary, *, slim=False, full=False):
    whoami_cmd(slim=slim, full=full)
    out, err = capsysbinary.readouterr()
    return orjson.loads(out), err.decode()


def session_self(capsysbinary, **flags):
    cli_session.main("who-me", **flags)
    return orjson.loads(capsysbinary.readouterr().out)


def test_before_no_flag_is_today_s_object_with_a_notice(before, me, capsysbinary):
    data, err = run(capsysbinary)
    assert LEGACY_KEYS <= set(data)
    assert len(data["process"]) == 9
    assert "`whoami` returns the `session self` payload" in err


@pytest.mark.parametrize("flag", ["slim", "full"])
def test_a_flag_is_session_self_on_both_sides(monkeypatch, me, capsysbinary, flag):
    for pinned in (FUTURE, PAST):
        monkeypatch.setattr(_output, "LISTING_CUTOVER", pinned)
        data, err = run(capsysbinary, **{flag: True})
        assert data == session_self(capsysbinary, **{flag: True})
        assert err == ""


def test_after_no_flag_is_session_self_reduced(after, me, capsysbinary):
    data, err = run(capsysbinary)
    assert data == session_self(capsysbinary)
    assert set(data["process"]) == {"state"}
    assert err == ""


def test_both_flags_exit_2_outside_a_session(db, monkeypatch):
    monkeypatch.setattr("twicc.cli._drop_request.whoami.resolve_current_session", lambda: None)
    with pytest.raises(typer.Exit) as exc:
        whoami_cmd(slim=True, full=True)
    assert exc.value.exit_code == 2


def test_outside_a_session_exits_1(db, monkeypatch):
    monkeypatch.setattr("twicc.cli._drop_request.whoami.resolve_current_session", lambda: None)
    with pytest.raises(typer.Exit) as exc:
        whoami_cmd(slim=False, full=False)
    assert exc.value.exit_code == 1


@pytest.mark.django_db(transaction=True)
def test_mcp_before_the_date_keeps_today_s_object_silently(monkeypatch):
    """Review focus 1. The MCP envelope carries no warnings, so the notice
    recorder itself is watched."""
    from twicc.mcp import server as mcp_server

    monkeypatch.setattr(_output, "LISTING_CUTOVER", FUTURE)
    recorded = []
    monkeypatch.setattr(_output, "_record_notice", recorded.append)
    project = Project.objects.create(id="-tmp-who-mcp", directory="/tmp/who-mcp")
    Session.objects.create(
        id="who-mcp", project=project, provider="claude_code", file_path="m.jsonl",
        type=SessionType.SESSION,
    )
    monkeypatch.setattr(
        "twicc.cli._twicc_info.resolve_live_twicc_or_exit", lambda: type("I", (), {"pid": 1})(),
    )
    result = asyncio.run(mcp_server.dispatch_tool("whoami", {}, session_id="who-mcp"))
    assert result["exit_code"] == 0, result
    assert result["result"]["session_id"] == "who-mcp"
    assert "agent_settings" in result["result"]
    assert recorded == []
```

  Adjust existing tests:
  - `tests/test_mcp_server.py::test_call_tool_whoami_uses_bound_identity` (`:33-51`): it takes no `monkeypatch` parameter today and the module imports only `from datetime import UTC` — add the `monkeypatch` parameter, `from datetime import datetime` (next to `UTC`) and `from twicc.cli import _output`. Then pin the before side (`monkeypatch.setattr(_output, "LISTING_CUTOVER", datetime(2200, 1, 1))  # noqa: DTZ001`), keep `result["result"]["session"]["id"]`; add a twin pinned after, which serves the new shape from the same setup:

```python
@pytest.mark.django_db(transaction=True)
def test_call_tool_whoami_after_the_date_is_the_session_self_row(isolated_data_dir, monkeypatch):
    import os

    import orjson

    from twicc.core.models import Project, Session

    monkeypatch.setattr(_output, "LISTING_CUTOVER", datetime(2000, 1, 1))  # noqa: DTZ001
    (isolated_data_dir / "twicc.info.json").write_bytes(
        orjson.dumps({"pid": os.getpid(), "port": 3500, "started_at": "2026-07-06T00:00:00Z"}),
    )
    project = Project.objects.create(id="-tmp-p3", directory="/tmp/p3", name="p3")
    session = Session.objects.create(
        id="33333333-3333-3333-3333-333333333333", project=project,
        provider="claude_code", file_path="p3.jsonl",
    )
    result = asyncio.run(mcp_server.dispatch_tool("whoami", {}, session_id=session.id))
    assert result["exit_code"] == 0, result
    assert result["result"]["id"] == session.id
    assert set(result["result"]["process"]) == {"state"}
```

  The twin's session has no `created_at` and no user message: the new lookup rule serves it. The `twicc.info.json` written into the isolated data dir is what `resolve_live_twicc` reads on the new path (through `resolve_listing_twicc_pid`), as it is for the legacy path (`resolve_live_twicc_or_exit`).
  - `tests/test_process_commands_removal.py::test_whoami_still_serves_its_nine_field_process_row` (`:342`): rename to `test_after_the_date_whoami_full_carries_the_session_s_own_identity`; call `whoami_cmd(slim=False, full=True)`; assert `set(process) == {"id", "state", "started_at", "last_state_change_at", "pid"}` and that the top level carries `provider`, `id`, `title`, `project_id`. It relies on the `live_backend` fixture (`resolve_live_twicc`), since the new path resolves the pid through `resolve_listing_twicc_pid`. Add a `before`-pinned twin that calls `whoami_cmd(slim=False, full=False)` and keeps the nine-field assertion.
  - `tests/test_slim_cutover.py`: add `["whoami", "--slim", "--full"]` to the `:311-316` parametrization of `test_slim_and_full_are_mutually_exclusive` (spec §7). `invoke()` does not enforce local-only (only the `/rpc/` view does), so the test's CLI half, `invoke` half and `warnings == ()` check all apply; `whoami`'s check runs before the lookup, so it holds outside a session. In `test_the_flags_reach_the_mcp_schema`, add: `props = tools_by_name()["whoami"].json_schema["properties"]`, `props["slim"]["type"] == props["full"]["type"] == "boolean"` (import `tools_by_name` from `twicc.mcp.tools`). In `test_the_help_texts_match_the_side_of_the_cutover_we_are_on`, add: before → `described["whoami"].startswith("DEPRECATION")` and "`session self` payload" in it; after → `not described["whoami"].startswith("DEPRECATION")`.

- [ ] **Step 2: Run, verify they fail.** `uv run pytest tests/test_cli_whoami_cutover.py -q` — FAIL (`whoami_cmd() got an unexpected keyword argument 'slim'`). `test_mcp_before_the_date_keeps_today_s_object_silently` already passes today: it is a regression guard (review focus 1), not a proof.

- [ ] **Step 3: Implement.**
  - `_output.py`: after `SLIM_CUTOVER_NOTICE`, add `WHOAMI_CUTOVER_NOTICE` and `WHOAMI_HELP` with the exact before / after texts of spec §2 (`whoami` section and "Help texts" bullets), through `cutover_help`, `{date}` = `_CUTOVER_DATE` — with one change: write the word as `` `process` `` (backticks) wherever the texts say "process block" / "process row" ("with its `process` block inside", "the nine-field `process` row"). Rich wraps the top-level `--help` table at the terminal width, and a wrapped description line that starts with the bare word `process` matches the retired-command check of `tests/test_process_commands_removal.py:390` (`^\s*│?\s*process(es)?\s`): with `TWICC_LISTING_CUTOVER=2000-01-01 COLUMNS=70` that test fails on the spec wording. The same fragility already exists today (the `TOPOLOGY_CUTOVER_NOTICE` wraps to "│ … process block on every node" at 70 columns, harmless only because the notice is empty after the date), so also anchor that regex on the command column: `r"^(?:│ |  )process(es)?\s"` (a Rich table row starts `│ <name>`, a plain Click row `  <name>`; a wrapped description line has more leading spaces). Add this test next to it in `tests/test_process_commands_removal.py` (`COLUMNS` does change Rich's width in `CliRunner`; verified by a review run):

```python
RETIRED_ROW = re.compile(r"^(?:│ |  )process(es)?\s", re.MULTILINE)


@pytest.mark.parametrize("width", ["70", "80", "200"])
def test_the_top_help_lists_the_retired_rows_only_before_the_date(width):
    """Import-time help: two-sided on the effective cutover. A wrapped
    description line starting with the word `process` must not read as a row."""
    from typer.testing import CliRunner

    from twicc.cli import app

    top_help = CliRunner().invoke(app, ["--help"], env={"COLUMNS": width}).output
    rows = {m.group(0).split()[-1] for m in RETIRED_ROW.finditer(top_help)}
    if _output.listing_cutover_passed():
        assert rows == set(), top_help
    else:
        assert rows == {"process", "processes"}, top_help
```

  Use the same `RETIRED_ROW` in `test_the_help_texts_match_the_side_of_the_cutover_we_are_on` (`:390`) instead of its inline pattern. `WHOAMI_SLIM_HELP`: before "Return the `session self` payload, reduced. Without --slim or --full, this command returns its current object until {date}.", after "Return the `session self` payload, reduced (the default)."; `WHOAMI_FULL_HELP`: before "Return the `session self` payload in full — every field of the session payload. Without --slim or --full, this command returns its current object until {date}.", after "Return the `session self` payload in full — every field of the session payload." ("session payload", not "session": the payload leaves out stored columns such as `file_path`, `last_offset`, `compute_version`; same correction as `FULL_HELP` in Task 5.) In `slim_notice`, add `elif kind == "whoami":` whose `change` is the spec text ("`whoami` returns the `session self` payload — the session row with its `process` block inside, reduced by default — instead of its current object (`session_id` becomes `id`, `agent_settings.<field>` becomes `<field>`, `current_working_directory` becomes `git_directory`). Pass --full to get that row in full"); the shared template appends ", or --slim to get the new shape today; …". Document the new `kind` in the docstring.
  - `whoami.py`:

```python
import typer

from twicc.cli._output import (
    WHOAMI_FULL_HELP, WHOAMI_SLIM_HELP, emit_error, emit_json, listing_cutover_passed, slim_notice,
)


def whoami_cmd(
    slim: bool = typer.Option(False, "--slim", help=WHOAMI_SLIM_HELP),
    full: bool = typer.Option(False, "--full", help=WHOAMI_FULL_HELP),
) -> None:
    """Print details of the session that owns the calling process.

    Walks the PID ancestry from the current process upward and matches
    against the live agents tracked by TwiCC.

    With ``--slim`` or ``--full`` — and from the cutover without a flag — it
    prints the ``session self`` payload: the session row (reduced, or in full
    with ``--full``) with its ``process`` block inside, built by
    :func:`twicc.cli.session.build_session_payload`.

    Without a flag, until the cutover, it prints its historical object:
    ``session_id``, ``title``, ``project_id``, ``project_directory``,
    ``current_working_directory`` (resolved from tool_use paths, may differ
    from ``project_directory`` when the agent works in a worktree or other
    repo), ``artifacts_dir`` and ``scratch_dir`` (the session's own working
    directories, already joined with the session id),
    ``orchestration_scratch_dir`` (the shared scratch folder, present only when
    the session is part of an orchestration tree), the resolved
    ``agent_settings``, the ``session`` sub-object (the serializer payload,
    without the CLI enrichment ``session <ID>`` adds), and the matching
    ``process`` row with nine fields (``provider``, ``session_id``,
    ``session_title`` and ``project_id`` on top of the compact block's five).

    Useful from inside a session's Bash tool to discover the session's
    own identity (the agent doesn't otherwise know its TwiCC session_id).
    From a plain terminal, this command exits 1 with a clear message —
    by design, ``whoami`` is only meaningful inside an active session.
    """
    # First: the refusal wins outside a session and records no notice.
    if slim and full:
        emit_error("Error: --slim and --full are mutually exclusive.", code=2)

    # Lazy imports to keep --help fast (no Django setup until we need it).
    import django

    django.setup()

    from twicc.cli._drop_request.whoami import resolve_current_session

    session = resolve_current_session()
    if session is None:
        msg = (
            "No TwiCC session found in PID ancestry. whoami is only "
            "meaningful from inside an active agent session."
        )
        typer.echo(msg, err=True)
        raise typer.Exit(1)

    # Read before slim_notice, which only returns a boolean.
    legacy = not slim and not full and not listing_cutover_passed()
    slim = slim_notice("whoami", slim, full, kind="whoami")
    if not legacy:
        from twicc.cli.session import build_session_payload

        emit_json(build_session_payload(session, slim=slim))
        return

    # The historical object, unchanged until the cutover.
    from twicc.agent.states import AgentState
    from twicc.cli._process_state import (
        serialize_dead_process_row,
        serialize_process_row,
    )
    from twicc.cli._twicc_info import resolve_live_twicc_or_exit
    from twicc.core.models import ProcessRun, Project
    from twicc.core.serializers import serialize_session
    from twicc.paths import get_session_artifacts_dir, get_session_scratch_dir
    from twicc.pending_titles import get_pending_title
    from twicc.providers.helpers import AgentSettings, get_provider_helpers

    # From here to the end: today's code, verbatim, from
    # `helpers = get_provider_helpers(session.provider)` (whoami.py:60)
    # to `emit_json(data)` (whoami.py:104) — resolved settings, project
    # directory, live process row, orchestration_scratch_dir, the `data` dict.
```

    The last block of the function is the current `whoami.py:60-104`, copied unchanged (it is 45 lines; the plan does not repeat it). Only the imports move below the `legacy` branch, so the new path pays for none of them.

  - `src/twicc/cli/_drop_request/whoami.py:17-19` (module docstring, the paragraph on what `resolve_current_session` returns: "so callers can serialise what ``twicc session <ID>`` returns, minus the ``process`` block"): → "so callers can build the session payload (`twicc session <ID>` does it through `build_session_payload`)".
  - `__init__.py:2051-2052`: add `WHOAMI_CUTOVER_NOTICE, WHOAMI_HELP` to the `_output` import block; register `app.command("whoami", help=WHOAMI_CUTOVER_NOTICE + WHOAMI_HELP)(whoami_cmd)`.

- [ ] **Step 4: Run, verify they pass.**
Run: `uv run pytest tests/test_cli_whoami_cutover.py tests/test_mcp_server.py tests/test_process_commands_removal.py tests/test_slim_cutover.py tests/test_mcp_tools.py -q`
Expected: PASS.

- [ ] **Step 5: Lint.** `uvx ruff check src/twicc/cli/whoami.py src/twicc/cli/_output.py src/twicc/cli/__init__.py src/twicc/cli/_drop_request/whoami.py tests/test_cli_whoami_cutover.py tests/test_mcp_server.py tests/test_process_commands_removal.py tests/test_slim_cutover.py`

**Docs:** `twicc-whoami/SKILL.md`, `SKILLS-AND-CLI.md:34,43,90-92` in Task 11.

---

### Task 8: Batch lookups and `peers` return `items` from the date (§3)

**Files:**
- Modify: `src/twicc/cli/_output.py` (`pagination_notice` shapes `"lookup"`, `"peers"`; new `LOOKUP_CUTOVER_NOTICE`, `PEERS_CUTOVER_NOTICE`, `LOOKUP_ENVELOPE_HELP`, `PEERS_ENVELOPE_HELP`)
- Modify: `src/twicc/cli/sessions_get.py`, `projects_get.py`, `workspaces_get.py`, `peers.py`
- Modify: `src/twicc/cli/__init__.py` (`_projects_get` `:102-128`, `_workspaces_get` `:173-195`, `_sessions_get` `:595-622`, `peers` `:2095-2096`)
- Create: `tests/test_cli_lookup_envelope.py`
- Adjust: `tests/test_peer_cli.py:51-56`, `tests/test_slim_cutover.py::test_the_notice_date_is_read_at_call_time` (`:189-195`)

**Interfaces:**
- Produces: `pagination_notice(..., shape="lookup" | "peers")`, rendered without the page-size clause; a `--paginated` flag on the four commands; `sessions_get.main(session_ids, *, slim=False, full=False, paginated=False)`, `projects_get.main(project_ids, *, paginated=False)`, `workspaces_get.main(workspace_ids, *, paginated=False)`, `peers_cmd(paginated: bool = typer.Option(...))`.

**Behaviour:**

| Command | Before the date, no flag | Before, `--paginated` | After (flag or not) |
|---|---|---|---|
| `sessions get` | bare array + lookup notice, then slim notice | `{"items": [...]}` | `{"items": [...]}` |
| `projects get`, `workspaces get` | bare array + lookup notice | `{"items": [...]}` | `{"items": [...]}` |
| `peers` | `{"peers": [...]}` + peers notice | `{"items": [...]}` | `{"items": [...]}` |

No `pagination` key anywhere. MCP gets no notice.

- [ ] **Step 1: Write the failing tests** in `tests/test_cli_lookup_envelope.py`:

```python
"""Batch lookups and `peers`: `items` from the date, `--paginated` before it."""

from __future__ import annotations

from datetime import datetime

import pytest

from twicc.cli import _output
from twicc.core.models import Project, Session, SessionType
from twicc.rpc.invoker import invoke

PAST = datetime(2000, 1, 1)     # noqa: DTZ001
FUTURE = datetime(2200, 1, 1)   # noqa: DTZ001

LOOKUPS = {
    "sessions get": ["sessions", "get", "lk-s", "--full"],
    # No leading dash: Click would read "-tmp-lk" as an option. The CLI re-adds
    # the dash (twicc-projects/SKILL.md), and no `--` either: `--paginated` is
    # appended after the id and would become a positional past `--`.
    "projects get": ["projects", "get", "tmp-lk"],
    "workspaces get": ["workspaces", "get", "nope-ws"],
    "peers": ["peers"],
}


@pytest.fixture(autouse=True)
def data(db, monkeypatch):
    monkeypatch.setattr("twicc.cli._twicc_info.resolve_live_twicc", lambda: None)
    monkeypatch.setattr("twicc.cli.sessions_get._PLACEHOLDER_TEMPLATE", None)
    project = Project.objects.create(id="-tmp-lk", directory="/tmp/lk")
    Session.objects.create(
        id="lk-s", project=project, provider="claude_code", file_path="s.jsonl",
        type=SessionType.SESSION,
    )


@pytest.mark.parametrize("name", LOOKUPS)
def test_before_the_current_shape_and_one_notice(monkeypatch, name):
    monkeypatch.setattr(_output, "LISTING_CUTOVER", FUTURE)
    result = invoke(LOOKUPS[name])
    assert result.exit_code == 0, result.error
    assert len(result.warnings) == 1
    if name == "peers":
        assert set(result.result) == {"peers"}
        assert "under `items` instead of `peers`" in result.warnings[0]
    else:
        assert isinstance(result.result, list)
        assert f"`{name}` returns" in result.warnings[0]
    assert "pages at" not in result.warnings[0]


@pytest.mark.parametrize("name", LOOKUPS)
@pytest.mark.parametrize("pinned", [FUTURE, PAST])
def test_paginated_is_the_new_shape_on_both_sides(monkeypatch, name, pinned):
    monkeypatch.setattr(_output, "LISTING_CUTOVER", pinned)
    result = invoke([*LOOKUPS[name], "--paginated"])
    assert result.exit_code == 0, result.error
    assert set(result.result) == {"items"}
    assert result.warnings == ()


@pytest.mark.parametrize("name", LOOKUPS)
def test_after_items_without_pagination(monkeypatch, name):
    monkeypatch.setattr(_output, "LISTING_CUTOVER", PAST)
    result = invoke(LOOKUPS[name])
    assert set(result.result) == {"items"}
    assert result.warnings == ()


def test_a_flagless_sessions_get_gets_two_notices_in_order(monkeypatch):
    monkeypatch.setattr(_output, "LISTING_CUTOVER", FUTURE)
    warnings = invoke(["sessions", "get", "lk-s"]).warnings
    assert len(warnings) == 2
    assert "instead of a bare array" in warnings[0]
    assert "reduced session projection" in warnings[1]


@pytest.mark.parametrize("name", LOOKUPS)
def test_mcp_is_never_notified(monkeypatch, name):
    from twicc.mcp.identity import mcp_call

    monkeypatch.setattr(_output, "LISTING_CUTOVER", FUTURE)
    token = mcp_call.set(True)
    try:
        assert invoke(LOOKUPS[name]).warnings == ()
    finally:
        mcp_call.reset(token)


def test_the_help_flips_with_the_effective_constant():
    """Import-time: two-sided on the effective constant (real clock or override)."""
    from twicc.mcp.tools import iter_mcp_tools, tools_by_name

    # tools_by_name() holds CommandSpec objects (no `description`); the MCP
    # descriptions come from iter_mcp_tools().
    described = {t.name: t.description for t in iter_mcp_tools()}
    specs = tools_by_name()
    for name in ("sessions_get", "projects_get", "workspaces_get", "peers"):
        assert described[name].startswith("DEPRECATION") != _output.listing_cutover_passed(), name
        assert "paginated" in specs[name].json_schema["properties"], name
```

  `workspaces get` reads `workspaces.json` from the data dir (`read_workspaces`); an unknown id gives a placeholder whatever the file holds, so the test does not depend on it.

  Adjust existing tests:
  - `tests/test_peer_cli.py::test_peers_lists_active_and_broken_only` (`:51-56`): pin the before side with `monkeypatch.setattr(_output, "LISTING_CUTOVER", datetime(2200, 1, 1))  # noqa: DTZ001` (the test is about content, not shape). It takes no `monkeypatch` parameter today: add it, and add `from datetime import datetime` and `from twicc.cli import _output` to the module imports (it has neither today).
  - `tests/test_slim_cutover.py::test_the_notice_date_is_read_at_call_time` (`:189-195`): call `listing("sessions get", tree, capsysbinary, paginated=True)`; docstring: "`paginated=True` silences the lookup notice, so the date can only come from the slim one — built from the pinned constant, not the import-time string."
  - `tests/test_slim_cutover.py:313` (`sessions get … --slim --full`, exit 2 in the wrapper) and `:346` (`test_mcp_is_never_notified` with `sessions get`) stay green; re-run them. The docstring of `test_mcp_is_never_notified` ("Neither command has a pagination notice that could mask the slim one.") becomes false for `sessions get`: rewrite it to "MCP silences every notice: `sessions get`'s lookup notice and slim notice, and `topology`'s."
  - `LISTINGS["sessions get"]` in `tests/test_slim_cutover.py` reads `rows_of(payload)`, which already unwraps `items`.

- [ ] **Step 2: Run, verify they fail.** `uv run pytest tests/test_cli_lookup_envelope.py -q` — FAIL: `test_paginated_is_the_new_shape_on_both_sides` with "No such option: --paginated"; `test_before_the_current_shape_and_one_notice` with `assert 0 == 1` (no lookup notice yet); `test_after_items_without_pagination` with `TypeError: unhashable type: 'dict'` for the three lookups (a bare array) and `{'peers'} == {'items'}` for `peers`; `test_a_flagless_sessions_get_gets_two_notices_in_order` with `assert 1 == 2`; `test_the_help_flips_with_the_effective_constant` because `paginated` is not in the schemas yet. `test_mcp_is_never_notified` already passes today (no lookup notice exists yet): it is a regression guard.

- [ ] **Step 3: Implement.**
  - `pagination_notice`: add the two shapes; the page-size clause applies only to `"array"` and `"object"`:

```python
    if shape == "lookup":
        change = f"`{command}` returns {{\"items\": [...]}} instead of a bare array"
    elif shape == "peers":
        change = "`peers` returns its list under `items` instead of `peers`"
    elif shape == "object":
        change = (...)  # unchanged
    else:
        change = (...)  # unchanged
    if shape in ("array", "object") and default_limit != PAGINATED_DEFAULT_LIMIT:
        change += f", and pages at {PAGINATED_DEFAULT_LIMIT} by default"
```

    Document the two shapes in the docstring.
  - New constants in `_output.py`, after `SLIM_CUTOVER_NOTICE` (texts of spec §3; mind the doubled braces):

```python
#: Prepended to the help of the three batch lookups while they still return a
#: bare array. Empty afterwards, like :data:`CUTOVER_NOTICE`.
LOOKUP_CUTOVER_NOTICE = cutover_help(
    f"DEPRECATION: from {_CUTOVER_DATE} this returns {{items}} instead of a bare "
    "array. Pass --paginated now to get that shape today. ",
    "",
)

#: Same, for ``peers``, whose list sits under ``peers`` today.
PEERS_CUTOVER_NOTICE = cutover_help(
    f"DEPRECATION: from {_CUTOVER_DATE} this returns its list under {{items}} "
    "instead of {peers}. Pass --paginated now to get that shape today. ",
    "",
)

LOOKUP_ENVELOPE_HELP = cutover_help(
    "Wrap the result in {items} — no pagination: one entry per id asked. Off by "
    f"default until {_CUTOVER_DATE}, when it becomes the only shape and this flag "
    "an accepted no-op.",
    "Accepted and ignored: the result is always wrapped in {items}.",
)

PEERS_ENVELOPE_HELP = cutover_help(
    "Wrap the result in {items} — no pagination: every approved peer. Off by "
    f"default until {_CUTOVER_DATE}, when it becomes the only shape and this flag "
    "an accepted no-op.",
    "Accepted and ignored: the result is always wrapped in {items}.",
)
```

  Note the second string of `PEERS_CUTOVER_NOTICE` is a plain string: `{peers}` keeps single braces there. Add three of them — `LOOKUP_CUTOVER_NOTICE`, `PEERS_CUTOVER_NOTICE`, `LOOKUP_ENVELOPE_HELP` — to the `from twicc.cli._output import (…)` block of `__init__.py` (`:24-28`); `PEERS_ENVELOPE_HELP` is used only by `peers.py`, which imports it itself (importing it in `__init__.py` too would be an unused import, ruff F401).
  - `sessions_get.main`: after `django.setup()`, `paginated = pagination_notice("sessions get", paginated, default_limit=None, shape="lookup")` **then** `slim = slim_notice(...)`; end with `emit_json({"items": results} if paginated else results)`.
  - `projects_get.main`: same call after its `django.setup()` (`:56`); `emit_json({"items": results} if paginated else results)`.
  - `workspaces_get.main`: `pagination_notice("workspaces get", …, shape="lookup")` **first** (the module never sets Django up).
  - The "scripts can ``zip(ids, output)``" notes become "…``zip(ids, output)`` (``output["items"]`` from 2026-10-01, or with ``--paginated``)": `sessions_get.py:13` (module docstring), `sessions_get.py:66-67` and `projects_get.py:64-65` (comments). `processes_get.py:45` stays (retired command, keeps its array). `git grep -n "zip(ids" src/twicc/cli` lists exactly these four.
  - `peers.py`: `peers_cmd(paginated: bool = typer.Option(False, "--paginated", help=PEERS_ENVELOPE_HELP))`; after `django.setup()`, `paginated = pagination_notice("peers", paginated, default_limit=None, shape="peers")`; `emit_json({"items": peers} if paginated else {"peers": peers})`. Import `typer`, `PEERS_ENVELOPE_HELP` and `pagination_notice` at module top.
  - `__init__.py`, the three registrations (each docstring stays as the code comment; the `help=` repeats its text after the prefix, because a prefix cannot be prepended to a docstring):

```python
@projects_app.command(
    name="get",
    help=LOOKUP_CUTOVER_NOTICE + (
        "Look up projects by id or path (placeholder for missing, includes archived).\n\n"
        "Unlike ``twicc projects``, ``get`` takes no filter flags: when the "
        "caller names the projects it cares about, the archived-by-default "
        "filter would only blur the meaning of the placeholder rows."
    ),
)
def _projects_get(
    project_ids: list[str] = typer.Argument(..., metavar="PROJECT...", help=...),  # this argument is unchanged: keep today's metavar and help
    paginated: bool = typer.Option(False, "--paginated", help=LOOKUP_ENVELOPE_HELP),
) -> None:
    # (today's docstring stays here, as the code comment)
    from twicc.cli.projects_get import main as projects_get_main

    projects_get_main([derive_project_id(pid)[0] for pid in project_ids], paginated=paginated)
```

    `_workspaces_get` the same way, with its own docstring text ("Look up workspaces by id (placeholder for missing, includes archived).\n\nUnlike ``twicc workspaces``, ``get`` takes no filter flags: when the caller names the workspaces it cares about, the archived-by-default filter would only blur the meaning of the placeholder rows.") and `workspaces_get_main(workspace_ids, paginated=paginated)`. `_sessions_get`: its existing `help=SLIM_CUTOVER_NOTICE + (…)` becomes `help=LOOKUP_CUTOVER_NOTICE + SLIM_CUTOVER_NOTICE + (…)`, and it gains the same `paginated` option, passed as `sessions_get_main(session_ids, slim=slim, full=full, paginated=paginated)`. `peers` (`:2095-2096`):

```python
from twicc.cli.peers import peers_cmd  # noqa: E402
app.command(
    name="peers",
    help=PEERS_CUTOVER_NOTICE + (
        "List peer instances approved for cross-instance messaging.\n\n"
        "Peers are other TwiCC instances the user has paired with (friend-request "
        "flow, managed in the web UI only). Use this to resolve a peer's id or "
        "exact name before ``twicc peer-send``. Output includes ``active`` peers "
        "(messageable) and ``broken`` ones (revoked/unreachable — listed so a "
        "failing send can be explained instead of \"peer unknown\")."
    ),
)(peers_cmd)
```

- [ ] **Step 4: Run, verify they pass.**
Run: `uv run pytest tests/test_cli_lookup_envelope.py tests/test_peer_cli.py tests/test_slim_cutover.py tests/test_cli_pagination_envelope.py tests/test_cli_session_process_state.py tests/test_cli_session_payload.py tests/test_mcp_tools.py -q`, then every file listed by `grep -rlnE '"(projects|workspaces|sessions)", "get"|\["peers"\]|projects_get|workspaces_get|sessions_get' tests`.
Expected: PASS.

- [ ] **Step 5: Lint.** `uvx ruff check src/twicc/cli/_output.py src/twicc/cli/sessions_get.py src/twicc/cli/projects_get.py src/twicc/cli/workspaces_get.py src/twicc/cli/peers.py src/twicc/cli/__init__.py tests/test_cli_lookup_envelope.py tests/test_peer_cli.py tests/test_slim_cutover.py`

**Docs:** Task 11 (`twicc-sessions`, `twicc-projects`, `twicc-workspaces`, `twicc-peers`, `SKILLS-AND-CLI.md`) and Task 13 (guide).

---

### Task 9: `sessions stop` — `{summary, results}`, bare call refused, caller never stopped (§4)

**Files:**
- Modify: `src/twicc/cli/_stop_batch.py:32-155` (`stop_session_ids` gains `caller_id`)
- Modify: `src/twicc/cli/sessions_stop.py` (docstring `:1-17`, `main`)
- Modify: `src/twicc/cli/__init__.py:372-385` (help), `src/twicc/cli/sessions_wait_reply.py:24-27` (docstring), `src/twicc/cli/_session_selection.py:9-11` (module docstring)
- Test: `tests/test_cli_sessions_stop.py`, `tests/test_cli_stop_batch.py`

**Interfaces:**
- Consumes: `resolve_current_session` (`src/twicc/cli/_drop_request/whoami.py:57`); `session self stop` works through Task 6.
- Produces: `stop_session_ids(unique_ids, *, timeout: int, force: bool, twicc_pid: int, caller_id: str | None = None) -> list[dict]`; status `"skipped_self"`.

**Behaviour (now; the command is unreleased, no notice):**
- Output `{"summary": {"total", "succeeded", "failed", "all_succeeded"}, "results": {"<id>": entry}}`, `results` in selection order, each entry unchanged (`session_id`, `session_known`, `status`, `request_uuid`, `provider`, `session_title`, `project_id`, `error`).
- Empty selection (a filter matched nothing) → `{"summary": {"total": 0, "succeeded": 0, "failed": 0, "all_succeeded": true}, "results": {}}`, exit 0.
- No id and no filter → exit 1, `Error: sessions stop needs at least one session id or one filter — a bare call would stop every running session.`, before `ensure_server_available`.
- The caller (named, `self`, or selected by a filter) → `status: "skipped_self"`, `request_uuid: null`, error "The calling session is never stopped by `sessions stop`; use `session self stop`.", counted in `failed`, no drop submitted.
- Exit 0 whenever the command ran.
- `processes stop` unchanged (passes no `caller_id`, keeps its array).

- [ ] **Step 1: Update and add tests (they become the failing tests).** In `tests/test_cli_sessions_stop.py`:
  - `fake_stop(ids, *, timeout, force, twicc_pid, caller_id=None)`: `seen.update(ids=list(ids), timeout=timeout, force=force, pid=twicc_pid, caller_id=caller_id)`; return `[{"session_id": sid, "status": "skipped_self" if sid == caller_id else "stopped"} for sid in ids]`.
  - Add `def results(payload): return list(payload["results"].values())`; every assertion that read the old list reads `results(run(...))`; `== []` becomes `== {"summary": {"total": 0, "succeeded": 0, "failed": 0, "all_succeeded": True}, "results": {}}`.
  - An autouse fixture `no_caller` patches `twicc.cli._drop_request.whoami.resolve_current_session` to `lambda: None` (the suite may run inside a TwiCC session, whose PID ancestry would otherwise resolve a real caller). The `caller` fixture below overrides it for its tests.
  - Replace `test_a_bare_call_stops_everything_running` (`:87`):

```python
def test_a_bare_call_is_refused_before_the_server_check(project, monkeypatch, capsysbinary):
    def boom():
        raise AssertionError("a bad call must be named before the server is checked")

    monkeypatch.setattr("twicc.cli._drop_request.transport.ensure_server_available", boom)
    with pytest.raises(typer.Exit) as exc:
        sessions_stop.main([], timeout=30)
    assert exc.value.exit_code == 1
    assert "needs at least one session id or one filter" in capsysbinary.readouterr().err.decode()


def test_stop_everything_running_is_written_on_purpose(project, server, capsysbinary):
    make_session(project, "busy")
    make_run("busy")
    make_session(project, "idle")
    make_run("idle", AgentState.USER_TURN)
    make_session(project, "gone")
    run(capsysbinary, state=["starting", "assistant_turn", "awaiting_user_input", "user_turn"])
    assert set(server["ids"]) == {"busy", "idle"}
```

  - Add `import typer` to the module imports (the existing tests import it locally; the new module-level tests need it).
  - Seven existing tests call `run(…)` with no id and no filter — `force=` and `timeout=` are not filters — and would hit the new refusal. Each passes `project=project.id` (every session they create lives in `project`, so the selection is unchanged), and reads the output through `results(…)`:
    - `test_a_stopped_session_is_not_in_the_batch` (`:100`),
    - `test_hidden_sessions_are_stopped_too` (`:110`),
    - `test_an_archived_session_is_still_reachable_by_id` (`:121`, bare `run(capsysbinary)` at `:127`; `sessions stop` selects with `archived=True`, so the project filter still reaches the archived row),
    - `test_force_travels_to_the_stopper` (`:189`, `run(capsysbinary, force=True)`),
    - `test_the_live_set_is_narrowed_in_sql_not_in_python` (`:266`),
    - `test_a_session_whose_transcript_is_not_indexed_yet_is_stopped` (`:284`),
    - `test_the_timeout_and_the_pid_reach_the_stopper` (`:389`, `run(capsysbinary, timeout=7)`).
    The eighth bare call (`:95`) is in `test_a_bare_call_stops_everything_running`, which this task replaces. Confirm the list with `grep -n "run(capsysbinary" tests/test_cli_sessions_stop.py` (bare calls at `:95,106,116,127,193,279,294,395`) and by reading each test; a call that names an id or sets a filter already needs nothing.
  - `test_every_filter_reaches_the_query` (`:309`) iterates the result as a list: `[e["session_id"] for e in result]` → `[e["session_id"] for e in results(result)]`. Its docstring (`:313-314`), "A filter that is silently dropped turns its case into a bare stop." → "…into a stop of everything running." (a bare call is now refused).
  - The module docstring (`:5`) says "Three rules decide it"; with the fourth rule below it becomes "Four rules decide it".
  - Rewrite the module docstring's first rule (`:8-10`, "which is what makes a bare ``sessions stop`` bounded by what is alive rather than by how many sessions exist"): a bare call is refused; the live-set narrowing still bounds any filtered call; and add a fourth rule — the calling session is never stopped (`skipped_self`).
  - `test_a_non_positive_timeout_is_refused` (`:215-221`) calls `sessions_stop.main([], timeout=0)`: after this task that is also a bare call, whose refusal exits 1 too, so a mutant deleting the timeout check would survive. Pass `project="stop-project"` and also assert `"--timeout must be > 0" in capsysbinary.readouterr().err.decode()`.
  - The two tests that call `sessions_stop.main([], timeout=30)` directly and assert exit 2 — `test_no_backend_means_nothing_to_stop` (`:224`) and `test_an_unreachable_server_is_refused_before_anything_is_selected` (`:401`) — pass a filter: `sessions_stop.main([], timeout=30, project="stop-project")`. Their exit-2 assertions stay: a filtered call still reaches the server checks. Find any other direct bare call with `grep -n "sessions_stop.main(\[\]" tests/test_cli_sessions_stop.py` and give it the same filter unless it is the new refusal test.
  - New tests:

```python
@pytest.fixture
def caller(project, monkeypatch):
    me = make_session(project, "me")
    make_run("me")
    monkeypatch.setattr("twicc.cli._drop_request.whoami.resolve_current_session", lambda: me)
    return me


@pytest.mark.parametrize("how", ["named", "self", "spawn-tree"])
def test_the_caller_is_skipped_not_stopped(project, server, caller, capsysbinary, how):
    # In the caller's tree, so `--spawn-tree self` selects both (the filter
    # matches spawn_root_id = me OR pk = me, src/twicc/cli/sessions.py:83).
    make_session(project, "other", spawned_by=caller, spawn_root=caller)
    make_run("other")
    if how == "spawn-tree":
        payload = run(capsysbinary, spawn_tree="self")
    else:
        payload = run(capsysbinary, "me" if how == "named" else "self", "other")
    assert server["caller_id"] == "me"
    assert payload["results"]["me"]["status"] == "skipped_self"
    assert payload["results"]["other"]["status"] == "stopped"
    assert payload["summary"] == {"total": 2, "succeeded": 1, "failed": 1, "all_succeeded": False}


def test_no_caller_skips_nothing(project, server, capsysbinary):
    make_session(project, "busy")
    make_run("busy")
    payload = run(capsysbinary, project=project.id)
    assert server["caller_id"] is None
    # The stub reports `skipped_self` only for its `caller_id`: with none,
    # every entry is `stopped` (fails today on the list-shaped output).
    assert {e["status"] for e in results(payload)} == {"stopped"}
```

```python


def test_the_summary_counts_every_non_stopped_status_as_failed(project, server, monkeypatch, capsysbinary):
    statuses = ["stopped", "timeout", "skipped_unknown", "rejected"]
    monkeypatch.setattr(
        "twicc.cli._stop_batch.stop_session_ids",
        lambda ids, **kw: [{"session_id": sid, "status": s} for sid, s in zip(ids, statuses, strict=True)],
    )
    payload = run(capsysbinary, "a", "b", "c", "d")
    assert payload["summary"] == {"total": 4, "succeeded": 1, "failed": 3, "all_succeeded": False}
    assert list(payload["results"]) == ["a", "b", "c", "d"]


def test_each_result_is_the_per_id_entry_unchanged(project, server, monkeypatch, capsysbinary):
    """`results` re-keys the entries; it never drops or renames a field."""
    entry = {
        "session_id": "a", "session_known": True, "status": "stopped",
        "request_uuid": "req-1", "provider": "claude_code",
        "session_title": "A title", "project_id": project.id, "error": None,
    }
    monkeypatch.setattr("twicc.cli._stop_batch.stop_session_ids", lambda ids, **kw: [dict(entry)])
    payload = run(capsysbinary, "a")
    assert payload["results"] == {"a": entry}
```

  The `run` helper passes positional ids as `session_ids` and keyword filters through (`spawn_tree="self"` included); `resolve_spawn_tree_filter` calls `resolve_current_session` by its module global, so the `caller` fixture's patch reaches it.

  - In `tests/test_cli_stop_batch.py` (it already drives the real `_stop_batch.stop_session_ids` with its `project`, `make_session`, `TWICC_PID` helpers), add:

```python
def test_the_caller_is_skipped_before_any_submission(project, monkeypatch):
    """No drop is submitted for the caller; its entry says where to go instead."""
    make_session(project, "me")

    def no_submit(payload, *, kind):
        raise AssertionError(f"nothing may be submitted here, got {payload}")

    monkeypatch.setattr("twicc.cli._drop_request.transport.submit", no_submit)
    [entry] = _stop_batch.stop_session_ids(
        ["me"], timeout=1, force=False, twicc_pid=TWICC_PID, caller_id="me",
    )
    assert entry["status"] == "skipped_self"
    assert entry["request_uuid"] is None
    assert "`session self stop`" in entry["error"]
    assert entry["session_known"] is True


def test_processes_stop_passes_no_caller(project, monkeypatch, capsysbinary):
    """The retired command keeps stopping the caller until its removal."""
    from twicc.cli import processes_stop

    monkeypatch.setattr("twicc.cli._drop_request.transport.ensure_server_available", lambda: None)
    monkeypatch.setattr(
        "twicc.cli._twicc_info.resolve_live_twicc", lambda: type("I", (), {"pid": TWICC_PID})(),
    )
    # A real caller, so a mutant that resolved it and passed `caller_id=`
    # would be seen (with no caller it would pass `None` and survive).
    me = make_session(project, "me")
    monkeypatch.setattr("twicc.cli._drop_request.whoami.resolve_current_session", lambda: me)
    seen = {}

    def fake_stop(ids, **kwargs):
        seen.update(kwargs, ids=list(ids))
        return []

    monkeypatch.setattr("twicc.cli._stop_batch.stop_session_ids", fake_stop)
    processes_stop.stop_cmd(["me"], timeout=5)
    assert seen["ids"] == ["me"], "the caller reaches the stopper"
    assert "caller_id" not in seen
```

  `processes_stop.stop_cmd` imports `ensure_server_available`, `resolve_live_twicc` and `stop_session_ids` inside the function (`src/twicc/cli/processes_stop.py:60-62,134`), so the module-attribute patches apply. It runs `merge_session_scope_ids(["me"], …)` for real; with no scope that returns the explicit ids — check `src/twicc/cli/_session_scope.py` and patch `twicc.cli._session_scope.merge_session_scope_ids` to `lambda ids, **kw: list(ids)` if it queries anything the fixture lacks.

- [ ] **Step 2: Run, verify they fail.** `uv run pytest tests/test_cli_sessions_stop.py tests/test_cli_stop_batch.py -q` — FAIL (the new `caller_id` keyword and output shape do not exist yet). `test_processes_stop_passes_no_caller` and `test_stop_everything_running_is_written_on_purpose` (the `--state` filter already selects the live set) already pass: they are regression guards.

- [ ] **Step 3: Implement.**
  - `_stop_batch.py`: add the keyword `caller_id: str | None = None`; in the per-id loop, right after `outcomes[sid] = entry`:

```python
        if sid == caller_id:
            entry["status"] = "skipped_self"
            entry["error"] = (
                "The calling session is never stopped by `sessions stop`; "
                "use `session self stop`."
            )
            continue
```

    Extend the docstring (the keyword and the status). The function still returns a list; `sessions stop` shapes it. The section comment near its end (`_stop_batch.py:153`, `# --- Emit JSON array in input order ---`) → `# --- Return the entries in input order (each command shapes its envelope) ---`.
  - `sessions_stop.py` `main`: move the `has_filter` expression above the server check; after `reject_conflicting_scopes(...)` and **before** `transport.ensure_server_available()`:

```python
    if not session_ids and not has_filter:
        emit_error(
            "Error: sessions stop needs at least one session id or one filter — "
            "a bare call would stop every running session.",
            code=1,
        )
```

    Replace `if has_filter or not explicit:` by `if has_filter:`. After the pid check, and in place of the current `if not targets: emit_json([])` / `emit_json(stop_session_ids(...))` ending:

```python
    from twicc.cli._drop_request.whoami import resolve_current_session

    current = resolve_current_session()
    caller_id = current.id if current is not None else None

    # (the existing selection code stays here, unchanged except for the
    # `if has_filter:` guard above: `explicit = resolve_explicit_ids(...)`,
    # `seen`, `selected`, and `targets = explicit + selected`; the old
    # `if not targets: emit_json([]); return` block and the old final
    # `emit_json(stop_session_ids(...))` are replaced by what follows)
    entries = stop_session_ids(
        targets, timeout=timeout, force=force, twicc_pid=twicc_pid, caller_id=caller_id,
    ) if targets else []
    succeeded = sum(1 for entry in entries if entry["status"] == "stopped")
    emit_json({
        "summary": {
            "total": len(entries),
            "succeeded": succeeded,
            "failed": len(entries) - succeeded,
            # True on an empty selection, as `_batch_runner` answers for
            # send-messages / update-sessions: nothing asked, nothing failed.
            "all_succeeded": succeeded == len(entries),
        },
        "results": {entry["session_id"]: entry for entry in entries},
    })
```

    Rewrite the module docstring: a bare call is refused; the caller is never stopped (`skipped_self`); output `{summary, results}`. The `main` docstring (`sessions_stop.py:39`, "Stop every selected session that currently has a process.") gains "— never the calling session (`skipped_self`)".
  - `__init__.py`: `_sessions_stop` `help=` → "Stop the agents behind selected sessions (only those actually running). A bare call is refused; the calling session is never stopped."; the `SESSION_ID...` help (`:380-385`): replace "Omit them to select with the filters below — a bare `sessions stop` stops every running session, which is bounded by what is alive, not by how many sessions exist." with "Omit them to select with the filters below; at least one id or one filter is required — a bare call is refused. The calling session is never stopped (`skipped_self`); use `session self stop`." Update the function docstring accordingly.
  - `sessions_wait_reply.py:24-27`: "``sessions stop`` can afford a bare call because the live process set bounds it; nothing bounds this one." → "``sessions stop`` refuses a bare call too."
  - `_stop_batch.py:1-10` (module docstring): "The drop-per-id submission, the per-id pre-check, the single-deadline poll loop and the outcome shape live here so the two cannot drift into reporting the same operation differently." → the per-id **entry** is shared; each command shapes the envelope (`processes stop` a bare array until its removal, `sessions stop` `{summary, results}`), and only `sessions stop` passes `caller_id`.
  - `tests/test_cli_stop_batch.py:52-53` (docstring of `test_an_unknown_id_is_reported_not_dropped`): "One entry per input id is the contract both commands publish: a caller zips its ids against the output." → "One entry per input id is the contract of the shared function: `processes stop` emits the list as is, `sessions stop` re-keys it by id under `results`."
  - `tests/test_cli_stop_batch.py:92-93` (docstring of `test_the_output_follows_the_input_order`): "A caller aligns the array with the ids it passed; reordering breaks every `zip(ids, output)` silently." → "`processes stop` emits this list as is and `sessions stop` keys it under `results` in the same order, so a reordering breaks both."
  - `git grep -n "zip(ids\|one entry per\|both commands" -- tests/test_cli_stop_batch.py tests/test_cli_sessions_stop.py` lists the test texts on the per-id contract; re-read each.
  - `_session_selection.py:9-11` (module docstring): "``stop`` is bounded by the live process set and can afford a bare call, while a bare wait would poll every indexed session until its deadline." → "``stop`` is narrowed to the live process set; both refuse a bare call — a bare stop would stop every running session, a bare wait would poll every indexed session until its deadline."
  - `sessions_stop.py:111` (inline comment in `main`): "Narrowed to what is running, which is what bounds a bare call." → "Narrowed to what is running, which bounds any filtered call."
  - Every other text on a bare `sessions stop`: `grep -rn "bare" src/twicc/cli --include=*.py` and re-read each hit that names `sessions stop` or `stop`; the four above plus the module docstring of `sessions_stop.py` and the `_sessions_stop` help/docstring in `__init__.py` are all of them at `cc4911ff`.

- [ ] **Step 4: Run, verify they pass.**
Run: `uv run pytest tests/test_cli_sessions_stop.py tests/test_cli_stop_batch.py tests/test_process_commands_removal.py tests/test_sessions_wait_reply_documentation.py tests/test_cli_session_command.py -q`, then every file listed by `grep -rlnE 'stop_session_ids|sessions_stop|"sessions", "stop"' tests`.
Expected: PASS.

- [ ] **Step 5: Lint.** `uvx ruff check src/twicc/cli/_stop_batch.py src/twicc/cli/sessions_stop.py src/twicc/cli/__init__.py src/twicc/cli/sessions_wait_reply.py src/twicc/cli/_session_selection.py tests/test_cli_sessions_stop.py tests/test_cli_stop_batch.py`

**Docs:** Task 11 (the `sessions stop` rows of spec §7) and Task 13 (guide "Stopping").

---

### Task 10: The default wait cursor, and the wait / send help texts (§5, §6 "Code", "The usage rule")

**Files:**
- Modify: `src/twicc/cli/_wait_reply.py` (new `default_wait_cursors`)
- Modify: `src/twicc/cli/session.py:572-640` (`wait_reply` docstring and `cursor = session.last_line`)
- Modify: `src/twicc/cli/sessions_wait_reply.py:8-13,150-163`
- Modify: `src/twicc/cli/__init__.py` (plural `--since` help `:454-455`, plural docstring `:550-553` and `:571-576`, singular `--from` help `:723-724`, singular docstring `:787`)
- Modify: `src/twicc/cli/send_message/command.py` (`--wait-reply` help `:48-64`), `src/twicc/cli/send_messages.py` (`--wait-reply` help `:131-145`)
- Create: `tests/test_wait_default_cursor.py`
- Adjust: `tests/test_cli_session_wait.py:109-118`, `tests/test_cli_sessions_wait_reply.py:169,521` (names and docstrings only), `tests/test_session_wait_documentation.py` `SANCTIONED` (`:53-57`) if a help gains a `--wait-reply` mention

**Interfaces:**
- Produces: `default_wait_cursors(sessions: Iterable[Session]) -> dict[str, int]` in `src/twicc/cli/_wait_reply.py`.

**Behaviour (now; both commands unreleased):** with neither `--from` nor `--since`, a session whose compute is current starts strictly after its last `USER_MESSAGE` item (`0` when none); a session whose compute is not current keeps `last_line`; a session with no indexed row keeps `0`. `--from` / `--since` unchanged.

- [ ] **Step 1: Write the failing tests** in `tests/test_wait_default_cursor.py`:

```python
"""The default cursor: after the last user message, when the compute is current."""

from __future__ import annotations

import orjson
import pytest
import typer
from django.utils import timezone

from twicc.agent.states import AgentState
from twicc.cli import session as cli_session
from twicc.cli import sessions_wait_reply
from twicc.cli._wait_reply import default_wait_cursors
from twicc.core.enums import ItemKind
from twicc.core.models import ProcessRun, Project, Session, SessionType
from twicc.providers.helpers import get_provider_helpers

TWICC_PID = 7171


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    from twicc.cli import _twicc_info, _wait_reply

    monkeypatch.setattr(_wait_reply, "POLL_INTERVAL_SECONDS", 0.001)
    monkeypatch.setattr(_wait_reply, "AGENT_FLUSH_SECONDS", 0.02)
    monkeypatch.setattr(_wait_reply, "SESSION_ROW_GRACE_SECONDS", 0.02)
    monkeypatch.setattr(_twicc_info, "resolve_live_twicc", lambda: type("I", (), {"pid": TWICC_PID})())


@pytest.fixture
def project(db):
    return Project.objects.create(id="-tmp-dc", directory="/tmp/dc")


def make(project, sid, *, provider="claude_code", ready=True, last_line=10):
    # Never a literal version: settings_test sets its own (CLAUDE_CODE_COMPUTE_VERSION = 99).
    version = get_provider_helpers(provider).current_compute_version if ready else None
    return Session.objects.create(
        id=sid, project=project, provider=provider, file_path=f"{sid}.jsonl",
        type=SessionType.SESSION, created_at=timezone.now(), last_line=last_line,
        user_message_count=1, compute_version=version,
    )


def running(session):
    now = timezone.now()
    ProcessRun.objects.create(
        provider=session.provider, session_id=session.id, twicc_pid=TWICC_PID,
        started_at=now, state=AgentState.ASSISTANT_TURN.value, last_state_change_at=now,
        awaiting_user_input=False,
    )


def user(session, line):
    session.items.create(line_num=line, kind=ItemKind.USER_MESSAGE, content=orjson.dumps(
        {"type": "user", "message": {"role": "user", "content": "go"}}).decode())


def final(session, line, text="done"):
    session.items.create(line_num=line, kind=ItemKind.ASSISTANT_MESSAGE, content=orjson.dumps({
        "type": "assistant", "message": {"role": "assistant", "stop_reason": "end_turn",
                                         "content": [{"type": "text", "text": text}]}}).decode())


def wait(capsysbinary, sid, **kw):
    kw.setdefault("timeout", 0.5)
    with pytest.raises(typer.Exit):
        cli_session.wait_reply(sid, **kw)
    return orjson.loads(capsysbinary.readouterr().out)["reply"]


def test_an_answer_given_before_the_wait_is_returned(project, capsysbinary):
    s = make(project, "a1")
    running(s)
    user(s, 3)
    final(s, 5, "early")
    reply = wait(capsysbinary, "a1")
    assert (reply["outcome"], reply["line_num"], reply["text"]) == ("replied", 5, "early")


def test_no_user_message_waits_from_zero(project):
    assert default_wait_cursors([make(project, "a2")]) == {"a2": 0}


def test_a_re_messaged_session_returns_the_new_answer(project, capsysbinary):
    s = make(project, "a3")
    running(s)
    user(s, 2)
    final(s, 3, "first")
    user(s, 4)
    final(s, 6, "second")
    assert wait(capsysbinary, "a3")["text"] == "second"


def test_a_re_messaged_session_not_answered_yet_waits(project, capsysbinary):
    """Review focus 4: the answer to the previous message is not returned."""
    s = make(project, "a4")
    running(s)
    user(s, 2)
    final(s, 3, "old")
    user(s, 4)
    assert wait(capsysbinary, "a4", timeout=0.2)["outcome"] != "replied"


def test_a_queued_command_does_not_move_the_anchor(project, capsysbinary):
    """The documented Claude busy-message limit, pinned: a message delivered
    while busy is a SYSTEM attachment, so the anchor stays on the previous one."""
    s = make(project, "a5")
    running(s)
    user(s, 2)
    final(s, 5, "running turn's close")
    s.items.create(line_num=6, kind=ItemKind.SYSTEM, content=orjson.dumps(
        {"type": "attachment", "attachment": {"type": "queued_command", "prompt": "later"}}).decode())
    assert default_wait_cursors([s]) == {"a5": 2}
    assert wait(capsysbinary, "a5")["line_num"] == 5


def test_an_idle_session_ending_on_an_api_error(project, capsysbinary):
    s = make(project, "a6")
    user(s, 2)
    s.items.create(line_num=4, kind=ItemKind.API_ERROR, content=orjson.dumps({
        "type": "system", "subtype": "api_error", "isApiErrorMessage": True,
        "result": "You've hit your usage limit."}).decode())
    assert wait(capsysbinary, "a6")["outcome"] == "provider_error"


def test_explicit_cursors_are_unchanged(project, capsysbinary):
    s = make(project, "a7")
    running(s)
    user(s, 2)
    final(s, 5)
    assert wait(capsysbinary, "a7", from_line=5, timeout=0.2)["outcome"] != "replied"
    assert wait(capsysbinary, "a7", from_line=0)["line_num"] == 5


def test_since_is_unchanged_on_a_ready_session(project, capsysbinary):
    """Spec § Tests "Cursor": `--since` keeps its meaning when the compute is
    current — the instant places the cursor, not the last user message."""
    from datetime import UTC, datetime, timedelta

    s = make(project, "a9")
    running(s)
    base = datetime(2026, 9, 20, 12, 0, tzinfo=UTC)
    user(s, 2)
    final(s, 5, "after the instant")
    s.items.filter(line_num=2).update(timestamp=base)
    s.items.filter(line_num=5).update(timestamp=base + timedelta(minutes=10))
    early = (base + timedelta(minutes=5)).isoformat()
    late = (base + timedelta(minutes=20)).isoformat()
    assert wait(capsysbinary, "a9", since=early)["line_num"] == 5
    assert wait(capsysbinary, "a9", since=late, timeout=0.2)["outcome"] != "replied"


def test_the_indexing_lag_case_returns_the_previous_answer(project, capsysbinary):
    """Documented and accepted: a send without --wait-reply, then a wait
    without --from, before the new message is indexed."""
    s = make(project, "a8")
    running(s)
    user(s, 2)
    final(s, 3, "previous")
    assert wait(capsysbinary, "a8")["text"] == "previous"


@pytest.mark.parametrize("version", ["null", "older"])
def test_a_session_whose_compute_is_not_current_keeps_last_line(project, capsysbinary, version):
    s = make(project, "b1", ready=False, last_line=10)
    if version == "older":
        current = get_provider_helpers("claude_code").current_compute_version
        Session.objects.filter(id="b1").update(compute_version=current - 1)
        s.refresh_from_db()
    running(s)
    user(s, 2)
    final(s, 5, "below last_line")
    assert default_wait_cursors([s]) == {"b1": 10}
    assert wait(capsysbinary, "b1", timeout=0.2)["outcome"] != "replied"


def test_a_codex_null_kind_item_does_not_move_the_anchor(project):
    s = make(project, "c1", provider="codex")
    user(s, 3)
    s.items.create(line_num=4, kind=None, content=orjson.dumps(
        {"type": "response_item", "payload": {"type": "function_call_output"}}).decode())
    assert default_wait_cursors([s]) == {"c1": 3}


def test_the_helper_asks_one_query_for_a_batch(project, django_assert_num_queries):
    ready = [make(project, f"r{i}") for i in range(3)]
    for s in ready:
        user(s, 7)
    stale = make(project, "st", ready=False, last_line=40)
    with django_assert_num_queries(1):
        cursors = default_wait_cursors([*ready, stale])
    assert cursors == {"r0": 7, "r1": 7, "r2": 7, "st": 40}


def test_the_plural_reads_every_cursor_in_one_query(project, capsysbinary, monkeypatch):
    """The command itself, not only the helper: one USER_MESSAGE query per batch."""
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    monkeypatch.setattr(
        "twicc.cli._wait_reply.wait_for_replies",
        lambda cursors, **kw: {sid: {"outcome": "timeout", "since_line_num": c} for sid, c in cursors.items()},
    )
    for i in range(3):
        user(make(project, f"q{i}"), 5)
    with CaptureQueriesContext(connection) as queries:
        sessions_wait_reply.main(["q0", "q1", "q2"], timeout=1)
    item_queries = [q for q in queries.captured_queries if "core_sessionitem" in q["sql"]]
    assert len(item_queries) == 1


def test_the_plural_applies_both_rules(project, capsysbinary, monkeypatch):
    seen = {}

    def fake(cursors, **kw):
        seen.update(cursors)
        return {sid: {"outcome": "timeout", "since_line_num": c} for sid, c in cursors.items()}

    monkeypatch.setattr("twicc.cli._wait_reply.wait_for_replies", fake)
    ready = make(project, "p1")
    user(ready, 4)
    make(project, "p2", ready=False, last_line=9)
    sessions_wait_reply.main(["p1", "p2"], timeout=1)
    assert seen == {"p1": 4, "p2": 9}
```

  Before running, confirm: `ItemKind.SYSTEM` and `ItemKind.API_ERROR` exist (`grep -n "SYSTEM\|API_ERROR" src/twicc/core/enums.py`); the `loop` fixture of `tests/test_cli_sessions_wait_reply.py` shows the patch target of `wait_for_replies` (the plural imports it inside `main`, so patching `twicc.cli._wait_reply.wait_for_replies` works); the API-error test mirrors `tests/test_cli_session_wait.py:385-392`.

  Rename (assertions unchanged, spec §7 "Default cursor"):
  - `tests/test_cli_session_wait.py::test_the_cursor_defaults_to_the_session_s_last_line` → `test_a_session_whose_compute_is_not_current_starts_at_its_last_line`; its docstring says the fixture row has no `compute_version`, so the compute-window fallback applies.
  - `tests/test_cli_sessions_wait_reply.py::test_each_session_starts_above_its_own_last_line` → `test_sessions_whose_compute_is_not_current_start_above_their_own_last_line`; same docstring note. `test_the_batch_survives_a_broken_wait` (`:521`) keeps its name; its docstring's last clause ("Losing the payload would take the cursors with it, and a re-run reads each current last line — skipping whatever arrived in between.") states the retired resume rule: rewrite it to "Losing the payload would take the cursors with it; a re-run resumes, except for a session whose compute is not current — which is what this test's rows are, so the cursors it returns are the ones to resume from."

- [ ] **Step 2: Run, verify they fail.** `uv run pytest tests/test_wait_default_cursor.py -q` — FAIL (`ImportError: cannot import name 'default_wait_cursors'`, which fails the whole module). Once the helper exists, `test_a_re_messaged_session_not_answered_yet_waits`, `test_explicit_cursors_are_unchanged`, `test_since_is_unchanged_on_a_ready_session` (an explicit cursor bypasses the default either way) and the wait half of `test_a_session_whose_compute_is_not_current_keeps_last_line[null|older]` would also pass under the old default (`last_line` 10 sits above every item): they are regression guards, not proofs of the new default (the latter's `default_wait_cursors` assertion is the proof of the fallback). The proofs are `test_an_answer_given_before_the_wait_is_returned`, `test_a_re_messaged_session_returns_the_new_answer`, `test_a_queued_command_does_not_move_the_anchor`, `test_an_idle_session_ending_on_an_api_error`, `test_the_indexing_lag_case_returns_the_previous_answer`, `test_the_plural_reads_every_cursor_in_one_query`, `test_the_plural_applies_both_rules` (each fails with the helper present but not wired into the two commands). The helper tests (`test_no_user_message_waits_from_zero`, `test_a_codex_null_kind_item_does_not_move_the_anchor`, `test_the_helper_asks_one_query_for_a_batch`) prove the helper itself: they fail only while it is missing.

- [ ] **Step 3: Implement.** In `src/twicc/cli/_wait_reply.py`, next to `degraded_reply`:

```python
def default_wait_cursors(sessions) -> dict[str, int]:
    """The cursor a wait starts from when neither --from nor --since is given.

    Strictly after the session's last user message (0 when it has none): the
    answer to the last thing it was told is returned even if it came before
    the wait. A session whose compute is not current keeps its ``last_line``:
    its ``kind`` values may predate its newest lines (initial sync,
    compute-version bump, Codex legacy rollout), and the anchor would land on
    an older message — a missed answer, never a wrong one. One query for the
    ready ones, on ``idx_session_kind_line``.
    """
    from django.db.models import Max

    from twicc.core.enums import ItemKind
    from twicc.core.models import SessionItem
    from twicc.core.serializers import session_compute_ready

    cursors: dict[str, int] = {}
    ready: list[str] = []
    for session in sessions:
        if session_compute_ready(session):
            cursors[session.id] = 0
            ready.append(session.id)
        else:
            cursors[session.id] = session.last_line
    if ready:
        rows = (
            SessionItem.objects
            .filter(session_id__in=ready, kind=ItemKind.USER_MESSAGE)
            .values("session_id")
            .annotate(last=Max("line_num"))
            .values_list("session_id", "last")
        )
        for session_id, last in rows:
            cursors[session_id] = last
    return cursors
```

  - `session.py` `wait_reply`: `else: cursor = session.last_line` → `else: cursor = default_wait_cursors([session])[session.id]` (import it with the other `_wait_reply` names). Rewrite the docstring's "Omitted, the cursor is …" paragraph (`session.py:586-591`): after the session's last user message; the current `last_line` while its compute is not current; after a send without `--wait-reply`, pass `--from` the `last_line` it returned. Keep its last sentence, with the exact phrase "~5 s flush window": `test_the_documented_numbers_are_read_from_the_code` reads it from `cli_session.wait_reply.__doc__`, and this paragraph holds its only occurrence (`session.py:590`).
  - **"Nobody just messaged" wording.** The usage rule sends callers to these commands right after their own send without `--wait-reply`, so every text saying they are for sessions "nobody just messaged" becomes self-contradictory. Two clauses — the plural has **no `--from`** (`__init__.py:552`, "There is no --from"), so never give it the singular's:
    - **Singular** (`session <id> wait-reply`): keep the example list, then append "— or one you messaged yourself without `--wait-reply` (then pass `--from` the `last_line` the send returned)". So the singular docstring (`__init__.py:760-763`) reads "on a session nobody just messaged: one spawned earlier, steered from the UI, or messaged by someone else — or one you messaged yourself without `--wait-reply` (then pass `--from` the `last_line` the send returned)".
    - **Plural** (`sessions wait-reply`): append "— or ones you messaged with `send-messages` without `--wait-reply` (then pass `--since` an instant taken before the send, or wait on each with `session <id> wait-reply --from <last_line>`)".
    Where: singular — `__init__.py:760-763`, `twicc-session/SKILL.md:14` ("Use it on a session **you did not just message**"), `twicc-session/SKILL.md:238`, `tests/test_cli_session_wait.py:1-7` (module docstring, "nobody just prodded" … "the caller names the line to start above"); plural — `__init__.py:528` ("on sessions nobody just messaged:"), `sessions_wait_reply.py:1` (module docstring title), `twicc-sessions/SKILL.md:24`, `SKILLS-AND-CLI.md:249` (the plural's entry). Plus `session.py:576-580` (`wait_reply` docstring, which does not contain the phrase: "Here nothing was sent: the caller names the line to start above" → "Here nothing was sent by this command: the caller names the cursor, or it defaults to after the last user message"), and its twin in the singular's command docstring, the next sentence at `__init__.py:762-763` (the MCP description of `session_wait_reply`): "Nothing is sent, so the cursor is named rather than read off a send." → "Nothing is sent by this command: pass the cursor, or it defaults to after the last user message." `git grep -n -i "nobody just\|did not just message" -- src tests SKILLS-AND-CLI.md` lists the eight others. Task 12 applies the same two clauses to the docs.
  - **"An idle session reports `ended`" wording.** With the new default, an idle session whose answer already sits past its last user message returns `replied` at once; only an idle session with no answer past the cursor reports `ended` after the flush window. Reword every sentence that says an idle session ends on `ended`: `session.py:588-591` (the `wait_reply` docstring, in the paragraph Step 3 rewrites above — its last sentence keeps the exact phrase "~5 s flush window", now as "an idle session with no answer past the cursor reports ``ended`` rather than hanging — after the loop's ~5 s flush window, …"); `sessions_wait_reply.py:77-81` (the `--state dead` refusal comment; its full new text: "Refused rather than honoured, as `sessions stop` refuses it: `dead` is "no TwiCC process", so those sessions have nothing to say. It is also the one filter that would lift the refusal below while selecting every unarchived session there is: each would be polled, and one with no answer past its cursor waits out the ~5 s flush window before concluding `ended`, to produce a payload that says nothing.") and `:100-103` (the bare-call refusal comment: "the live ones to the deadline, the rest for the flush window" → "the live ones to the deadline, the rest for the flush window — or answered at once when an answer already sits past their cursor"); the module docstrings of `tests/test_cli_session_wait.py:9-12` ("it reports ``ended`` rather than hanging" → "with no answer past the cursor, it reports ``ended`` rather than hanging") and `tests/test_cli_sessions_wait_reply.py:6-9` ("each session starts above its own ``last_line``" → "each session starts after its last user message — above its own ``last_line`` while its compute is not current — or above the instant ``--since`` names"). `test_the_documented_numbers_are_read_from_the_code` requires each "flush window" of the code docstrings it reads to be "~5 s flush window": keep that exact phrase wherever "flush window" stays, **on one source line** — the test counts with `.count()`, which does not see across a line break, so a wrap between "~5 s flush" and "window" fails it (`0 >= 1`). The same holds for the skills and `SKILLS-AND-CLI.md` (Task 12).
  - `_wait_reply.py` module docstring (`:32-40`, "The caller owns the cursor: …" lists where each caller's cursor comes from): add the two wait commands — "`session <id> wait-reply` takes `--from` / `--since`, `sessions wait-reply` takes `--since` only (a line number belongs to one transcript); both default to :func:`default_wait_cursors` (after the last user message, the current `last_line` while the compute is not current)."
  - `sessions_wait_reply.py`: add `default_wait_cursors` to the existing local import `from twicc.cli._wait_reply import degraded_reply, wait_for_replies` (`main`, `:66`); before the `for sid in targets` loop, `defaults = default_wait_cursors(rows.values()) if instant is None else {}`; the assignment becomes `cursors[sid] = _cursor_at(session, instant) if instant is not None else defaults[sid]`. Rewrite the module docstring's "The cursors" paragraph (`:8-13`) the same way.
  - `__init__.py` texts, per spec §6 "Code" and "The usage rule":
    - plural `--since` (`:454-455`): "Start each session above the instant instead of after its last user message: only a line written strictly after it counts, …" (keep "strictly after" and "not a boundary": `tests/test_session_wait_documentation.py` and `tests/test_sessions_wait_reply_documentation.py` pin them); append "After `send-messages` without --wait-reply, pass an instant taken before the send.";
    - plural docstring `:550-553` → "Each session starts after its own last user message — so an answer already given is returned — (its current last line while its compute is not current), or above the instant --since names, translated per session. There is no --from: line 42 is a different place in every transcript. After `send-messages` without --wait-reply, pass --since an instant taken before the send." (the usage rule in the command help, spec §6; `--wait-reply` is already allowed in this prose, `NAMED_IN_PROSE`, `tests/test_sessions_wait_reply_documentation.py:48`); `:571-576` → R ("Re-running resumes, except for a session whose compute is not current; `--since <the instant the batch started>` resumes in every case.") followed by the per-session alternative (each block's `since_line_num` handed to `session <ID> wait-reply --from`);
    - singular `--from` help (`:723-724`): replace "Omitted, it is the session's current last line — \"tell me the next thing it says\"." with "Omitted, the wait starts after the session's last user message (its current last line while its compute is not current). After `send-message` without --wait-reply, pass the `last_line` it returned.";
    - singular docstring `:787`: the same sentence.
    - `send-message` `--wait-reply` help: append "To wait for the answer, pass --wait-reply. A separate wait must pass --from with the `last_line` this command returns." `send-messages` `--wait-reply` help: append "To wait for the answers, pass --wait-reply. A separate wait must pass `sessions wait-reply` an instant taken before this command (--since), or `session <id> wait-reply` the `last_line` of each entry (--from)." Keep the existing "previous turn's closing message" sentences (spec §6).
  - `--since` wherever it appears: an ISO 8601 instant, no offset = UTC, a bare date = its midnight UTC (both helps already say so; keep).

- [ ] **Step 4: Run, verify they pass.**
Run: `uv run pytest tests/test_wait_default_cursor.py tests/test_cli_session_wait.py tests/test_cli_sessions_wait_reply.py tests/test_wait_reply.py tests/test_session_wait_documentation.py tests/test_sessions_wait_reply_documentation.py tests/test_process_commands_removal.py -q`
Expected: PASS. `tests/test_session_wait_documentation.py` counts `--wait-reply` mentions per prose source, and `_prose_sources()` labels each option's help separately (`f"{param.opts[0]} help"`, `:182`). So:
  - the new `--from` help sentence creates a new source: add `"--from help": {"--wait-reply": 1, "--transition": 0}` to `SANCTIONED` (`:53-57`);
  - the singular docstring is counted under `"session wait-reply --help"`: two new sentences mention `--wait-reply` — the "nobody just messaged" clause (`:760-763`) and the cursor sentence (`:787`) — so its count goes from 1 to 3 (a literal review run confirmed 3). Recount after writing: the rule is exactly the number of mentions added.
  Re-read each new sentence first and confirm each mention is an intended cross-reference (the test's own rule).

- [ ] **Step 5: Lint.** `uvx ruff check src/twicc/cli/_wait_reply.py src/twicc/cli/session.py src/twicc/cli/sessions_wait_reply.py src/twicc/cli/__init__.py src/twicc/cli/send_message/command.py src/twicc/cli/send_messages.py tests/test_wait_default_cursor.py tests/test_cli_session_wait.py tests/test_cli_sessions_wait_reply.py tests/test_session_wait_documentation.py`

**Docs:** Task 12.

---

### Task 11: Skills and reference docs — page size, session payload, `whoami`, lookups, `peers`, `sessions stop` (§1-§4, §7)

**Files (skills under `src/twicc/agent/plugin/twicc/skills/`):** `twicc-session/SKILL.md`, `twicc-sessions/SKILL.md`, `twicc-whoami/SKILL.md`, `twicc-projects/SKILL.md`, `twicc-workspaces/SKILL.md`, `twicc-peers/SKILL.md`, `twicc-artifacts/SKILL.md`, `twicc-search/SKILL.md`, `twicc-share/SKILL.md`, `twicc-topology/SKILL.md`, `twicc-process/SKILL.md`, `twicc-processes/SKILL.md`, `twicc-create-session/SKILL.md`, `twicc-send-message/SKILL.md`, `twicc-update-session/SKILL.md`, `twicc-update-sessions/SKILL.md`, `twicc-orchestration/SKILL.md`, `twicc-orchestration/control-cookbook.md`, `twicc-orchestration-leader/SKILL.md`, `twicc-orchestration-manager/SKILL.md`; root `SKILLS-AND-CLI.md`, `ORCHESTRATION.md`; and one code docstring, `src/twicc/cli/session.py:415-416` (the `plan` docstring, Step 3; lint it with `uvx ruff check src/twicc/cli/session.py`).

**Behaviour:** documentation only. The skills describe both sides with the date, as the existing slim / pagination texts do.

- [ ] **Step 1: Read** `src/twicc/agent/plugin/README.md`, then `twicc-sessions/SKILL.md` and `twicc-session/SKILL.md` end to end.
- [ ] **Step 2: Page size** — apply every bullet of spec §7 "Page size → Texts": "(default: 20; 50 with `--paginated`)" → "(default: 20)" (`twicc-artifacts:42`, `twicc-search:37`, `twicc-sessions:78`, `twicc-projects:37`, `twicc-workspaces:37`); "the page size becomes **50**" → "is **20**" (`twicc-artifacts:44`, `twicc-search:39`, `twicc-sessions:83`, `twicc-projects:39`, `twicc-workspaces:39`, `twicc-share:52`, `twicc-session:182,188` incl. the example's `"limit": 50` → 20; `twicc-session:144` has its own wording, "caps the page at **50** when no `--limit` is given" → "caps the page at **20** …"); in `SKILLS-AND-CLI.md:17` the sentence reads "Without an explicit `--limit` the page size is **50**, whatever the command's own default: the flag promises a page, so it supplies one (`--tail` keeps its own window)." — it becomes "Without an explicit `--limit` the page size is **20**, the page size every listing uses from 2026-10-01: the flag promises a page, so it supplies one (`--tail` keeps its own window)." (not "the same default every listing uses": before the date `session content` / `messages` return everything by default); in the same bullet, "While the flag is opt-in, a call without it returns exactly what it always did" → add "— except `share`, whose default page is 20 now (it was 50), and the session commands' rows, which gain new keys and values now (see *Slim listings*)"; and "Accepted by `projects`, `workspaces`, `sessions`, `session content` / `messages` / `agents` / `workflows`, `artifacts`, `share` and `search`" gains "and, as `{items}` only (no `pagination`: one entry per id asked, or every approved peer), by `sessions get`, `projects get`, `workspaces get` and `peers`"; the `content` / `messages` cap (`twicc-session:179,225,226`); `share`'s default (`twicc-share:50`, `SKILLS-AND-CLI.md:331`). Leave the explicit `--limit 50` example arguments unchanged. The `--paginated` bullet of six skills ends "a call without it is unchanged; from that date the envelope is the only shape…" (`twicc-artifacts:44`, `twicc-projects:39`, `twicc-search:39`, `twicc-sessions:83`, `twicc-share:52`, `twicc-workspaces:39`): it speaks of the shape and stays, except in `twicc-share/SKILL.md:52`, where it becomes "a call without it keeps its shape (its default page is 20 now, it was 50)", and in `twicc-sessions/SKILL.md:83`, where it becomes "a call without it keeps its shape (its rows gain new keys and values now — see the row fields below)".
- [ ] **Step 3: Session payload and projection** — apply the two tables of spec §7 "`session <id>` shape and lookup" (`twicc-session/SKILL.md` rows `:4`, `:11`, `:48-54`, `:56-101`, the line after the example, `:103-118`; `twicc-sessions/SKILL.md` rows `:80`, `:113-147`, `:149`, `:154` / `:160` unchanged, `:158`, `:201`), then its "Other texts" (`SKILLS-AND-CLI.md:18`, `:40-41` (the spec says `:38-39`; the "Accepted by …" lines are at `:40-41` — each gains `session <ID>` and its subcommands, and also `sessions stop` / `sessions wait-reply` as explicit ids, which already resolve both keywords today through `resolve_explicit_ids`, `_session_selection.py:48-56`; `sessions stop self` answers `skipped_self`), `:255` — the whole "Default (no sub-command)" bullet, not only "both exit 1 here": "the full session row" and "reach for that one … for the reduced projection" become false too; rewrite it with the target of the `twicc-session/SKILL.md:48-54` row (the row, full until 2026-10-01 and reduced from that date, `--slim` / `--full` before or after the id, `self` / `parent`, exit 1 when no row has that id or when `self` / `parent` cannot be resolved; `sessions get` for several ids). The same correction applies to the spec's `twicc-session/SKILL.md:48-54` target itself: "exits 1 only when no session row has that id" is incomplete after Task 6 — write "exits 1 when no session row has that id, or when `self` / `parent` cannot be resolved (a structured `validation_error` on stdout)". Same for the `twicc-sessions/SKILL.md:201` row ("exit 1 when no row has that id") and every other "exit 1 if missing" / "exits 1 only when" text on `session <id>` (`git grep -n "exit 1 if missing\|exits 1 only\|exit 1 when no row" -- src/twicc/agent/plugin SKILLS-AND-CLI.md` after the edits); `twicc-process/SKILL.md:14` (besides the "exits `1` on a session not indexed yet" rewording, this migration row gains "from 2026-10-01 the row is reduced and its `process` block is `{state}`; `--full` for `pid` and timestamps", the same clause the `processes` row at `twicc-processes/SKILL.md:14` already carries); `twicc-create-session/SKILL.md:230`; `twicc-topology/SKILL.md:44,129` per the corrected `_TOPOLOGY_FULL_LEAD` text of Task 5, Step 3). The spec misses six "See also" lines that describe the bare call as the full payload; rewrite each to "one session's row (reduced from 2026-10-01; `--full` for every field)", keeping the rest of the line:
  - `twicc-send-message/SKILL.md:172` ("full session metadata"),
  - `twicc-artifacts/SKILL.md:147` ("full metadata for one session"),
  - `twicc-search/SKILL.md:127` ("full session metadata"),
  - `twicc-topology/SKILL.md:164` ("inspect full metadata for one node" → "inspect one node's session row (reduced from 2026-10-01; `--full` for every field)"),
  - `twicc-update-session/SKILL.md:222` ("full metadata"),
  - `twicc-create-session/SKILL.md:248` ("full metadata"). The `SLIM_HELP` description (Task 5) replaces every "about 60% lighter" / "paths and agent-settings bundle dropped" wording, with no size figure. In the same `SKILLS-AND-CLI.md:18` bullet, "Until then, a call with neither flag returns what it always did" gets the qualifier the guide gets in Task 13: "…returns what it always did — plus the keys added now (`project_directory`, `scratch_dir`, `orchestration_scratch_dir`, `question_widget`), with `artifacts_dir` always a path and the agent settings effective —"; and its "a flagless `sessions` or `session agents` call therefore prints two notices" gains `sessions get` (spec §7 "Other texts"). `twicc-topology/SKILL.md:44` ("otherwise call `$TWICC session <ID>`" to get a node's other fields) → "`$TWICC session <ID> --full`", matching the `topology.py` comment of Task 6. The reduced projection drops `plan_paths`, so four texts that send the reader to "the default view's `plan_paths` field" become false from 2026-10-01: `twicc-session/SKILL.md:381` and `:393`, `SKILLS-AND-CLI.md:263`, and the `plan` docstring in code, `src/twicc/cli/session.py:415-416` ("the same entries the default session view carries in ``plan_paths``, minus ``abs_path``") → "the `plan_paths` field of `session <ID> --full`". Exact replacements: `twicc-session/SKILL.md:381`, "— the default view's `plan_paths` field tells you up front." → "— `plan --list`, or `plan_paths` in `session <ID> --full`, tells you up front."; `:393`, "The default view's `plan_paths` field carries the same entries (without `abs_path`)" → "`plan_paths` in `session <ID> --full` carries the same entries (without `abs_path`)"; `SKILLS-AND-CLI.md:263`, "same entries as the default view's `plan_paths` field (minus `abs_path`)" → "same entries as `plan_paths` in `session <ID> --full` (minus `abs_path`)". Do **not** point to `has_plan` as the substitute: it reflects only the native Claude plan file (`src/twicc/providers/claude_code/helpers.py:385-405`) and is always `false` on Codex (the base class in `src/twicc/providers/helpers.py:484-494`, which Codex inherits without override), while `plan_paths` also holds pattern-detected documents on both providers. `git grep -n "default.*view.*plan_paths\|default view" -- src SKILLS-AND-CLI.md` lists these four plus `twicc-sessions/SKILL.md:149`, which the spec table row `:149` already rewrites. Wherever this lot's docs say "`has_artifacts` says whether it holds anything" (the spec's `twicc-session/SKILL.md:103-118` row, and any copy of the claim in `twicc-sessions/SKILL.md`, `SKILLS-AND-CLI.md` or the guide's "Changed now, without a notice" section of Task 13), qualify it the same way: that holds over MCP / RPC (the backend runs the command); from a terminal `has_artifacts` is always `false` (`session_has_artifacts`, `src/twicc/artifacts_watcher.py:212-219`), so check the folder itself. After the edits, `git grep -n "has_artifacts" -- src/twicc/agent/plugin SKILLS-AND-CLI.md frontend/public/help` must show no unqualified "holds anything" claim.
- [ ] **Step 4: `whoami`** — spec §7 "`whoami` output": `twicc-whoami/SKILL.md:8` (key list — including its clause that the `session` sub-object is "what `$TWICC session <ID>` returns", which becomes "the serializer payload of the session", as in `whoami.py` Task 7; the frontmatter `description` (`:3`, "discover your own TwiCC session_id") stays true) and `:45-49` (the `jq` recipes). Its `### Exit codes` section (`:37-40`) gains "`2` — `--slim` and `--full` passed together (checked before the lookup, so also outside a session)." Every recipe that reads a key whose place changes passes a flag, so it gives the same answer on both sides of the date — a flagless `whoami` keeps its old object (`session_id`, `agent_settings`) until 2026-10-01, and `.id` would print `null` until then:
  - `$TWICC whoami --slim | jq -r .id` (was `.session_id`);
  - `$TWICC whoami --slim | jq -r .selected_model` (was `.agent_settings.selected_model`);
  - `$TWICC whoami --full | jq -r .process.pid`;
  - `.artifacts_dir` / `.scratch_dir` stay flagless (same key on both sides).
  State once that the flagless call keeps the old object until the date. (`SKILLS-AND-CLI.md` holds no `jq` recipe for `whoami`; its lines are handled just below.) Then document `--slim` / `--full` (usable now), the date, the notice (terminal only) and the key-mapping table of spec §2. `SKILLS-AND-CLI.md:43` ("`twicc whoami` is the explicit way for an agent to discover its own `session_id`, …") → "…its own id (`session_id` in the flagless output until 2026-10-01; `id` with `--slim` / `--full`, and from that date), …"; `SKILLS-AND-CLI.md:90-92` (the key list) follows the same two-sided wording; `:34` → "`twicc info` also returns the canonical invocation under `twicc_executable`". `twicc-orchestration/SKILL.md:213`, `twicc-orchestration-worker/SKILL.md:56` and `src/twicc/agent/system_prompt.py` stay (still true).
- [ ] **Step 5: Lookups and `peers`** — spec §7 "Batch lookups and `peers`": `twicc-sessions/SKILL.md:103,106,171`, `twicc-projects/SKILL.md:46,49,85`, `twicc-workspaces/SKILL.md:45,48,72` (the spec also cites `twicc-sessions:115`, `twicc-projects:56` and `twicc-workspaces:55`: those are the opening `[` of the **listing** examples, which keep their listing shape — leave them), `twicc-peers/SKILL.md:34,39,56` (the spec's `:35` is at `:34`), `SKILLS-AND-CLI.md:187,214,243,345-347` (at `:346` the peer shape also gains the `broken_reason` field that `peers.py:31` emits and the doc omits: `{id, name, state, broken_reason, last_contact_at}`; "No arguments." → "Takes `--paginated`."; the same field is missing from the `twicc-peers/SKILL.md` examples at `:40-41` and `:56`, which this step rewrites anyway: add `"broken_reason"` there too, with the value `peers.py:31` emits (`peer.broken_reason`; `tests/test_peer_cli.py:56` shows `""` when there is none)). `twicc-peers/SKILL.md:50`, "`64` — Bad CLI usage", is false (a usage error exits `2`, as Click reports it; `twicc peers --bogus` exits 2) and becomes reachable now that the command takes an option: write "`2` — Bad CLI usage (unknown option)". The same wrong `64` line exists in 14 other skills at HEAD, outside this lot's commands: do not edit them here; list them in the Task 14 report (`git grep -n "\`64\` — Bad" -- src/twicc/agent/plugin`). Each gets `[--paginated]` in its usage line, the current shape, the `{"items": …}` shape from the date, and the notice. `sessions get`'s usage line reads `sessions get <SESSION_ID>... [--slim | --full] [--paginated]`. On these four commands `--paginated` wraps in `{items}` **without** `pagination` (one entry per id asked; every approved peer): describe it with the `LOOKUP_ENVELOPE_HELP` / `PEERS_ENVELOPE_HELP` meaning, never by copying the listing's `--paginated` bullet (`{items, pagination}`, `limit`, `has_more`), which sits a few lines above in the same skills.
- [ ] **Step 6: `sessions stop`** — spec §7 "`sessions stop`": `twicc-sessions/SKILL.md:44,48` (status list gains `skipped_self`; output `{summary, results}`), `twicc-processes/SKILL.md:16` (its "What changes" cell also gains "returns `{summary, results}` instead of an array"; and the `:15` row, `processes get` → `sessions get`, gains "returns `{items}` from 2026-10-01 (`--paginated` now)"). Their mirrors in `SKILLS-AND-CLI.md` get the same clauses: `:362` (`processes get` → `sessions get`) gains "returns `{items}` from 2026-10-01 (`--paginated` now)"; `:363` (`processes stop` → `sessions stop`, below) gains "returns `{summary, results}`"; and `:369` (`process <id>` → `session <id>`) gains the clause Step 3 gives `twicc-process/SKILL.md:14` ("from 2026-10-01 the row is reduced and its `process` block is `{state}`; `--full` for `pid` and timestamps"). Then `twicc-orchestration/SKILL.md:176`, `twicc-orchestration/control-cookbook.md:83-86`, `twicc-orchestration-leader/SKILL.md:56`, `twicc-orchestration-manager/SKILL.md:41`, `twicc-update-sessions/SKILL.md:158`; `SKILLS-AND-CLI.md:250,363` (`:398` stays); `ORCHESTRATION.md:93`. `twicc-sessions/SKILL.md:50` ("For a single session, `$TWICC session <ID> stop` does the same thing.") → "For a single session, `$TWICC session <ID> stop` does the same thing — and it is the way to stop your own session (`session self stop`), which `sessions stop` never does." The exit-code sentences next to them — `twicc-sessions/SKILL.md:48` ("it never fails as a whole: exit 0, one entry per target …") and `SKILLS-AND-CLI.md:250` ("exit 0 whatever the per-id outcome") — gain "exit `1` on a local refusal before anything is stopped: a bare call, `--state dead`, `--timeout` ≤ 0 or conflicting scopes are refused even with no backend; an unknown `--workspace` / `--provider` / `--state` value, a malformed `--annotation` or a `self` / `parent` that cannot be resolved are checked after the backend check (`sessions_stop.py:74-77` runs before `resolve_explicit_ids` and `build_filtered_queryset`), so with no backend they exit `2`; exit `2` when no backend runs", and "one entry per target" becomes "`{summary, results}`, one `results` entry per target". The parenthetical "(`--spawn-tree self` includes you)" / "(… includes the caller)" in those same warnings (`twicc-sessions/SKILL.md:44`, `twicc-orchestration/SKILL.md:176`, `SKILLS-AND-CLI.md:250`) becomes "(it selects you, reported `skipped_self`)". Target: a bare call is refused; `sessions stop` never stops the caller, which gets `skipped_self`; `parent`, `--spawn-tree` and `--siblings` still reach beyond the caller's children; `--annotation` alone still selects across every tree.
- [ ] **Step 7: Consistency check.**
  `grep -rni "60%\|50 with\|becomes \*\*50\|page size is \*\*50\|at \*\*50\|default 50\|always in full\|full metadata\|full session metadata\|No arguments\|no guardrail\|stops every running session\|stops everything\|one entry per target\|can stop you\|both exit 1 here\|\.session_id\|{\"peers\"\|default view\|is unchanged\|pages at 50\|\"limit\": 50\|50-page\|includes you\|includes the caller" src/twicc/agent/plugin/twicc/skills SKILLS-AND-CLI.md ORCHESTRATION.md` (case-insensitive) — re-read each hit; only a sentence still true after this lot may stay (e.g. a "full metadata" that names `--full` explicitly).
  `grep -rn "TWICC_LISTING_CUTOVER" src/twicc/agent/plugin SKILLS-AND-CLI.md ORCHESTRATION.md frontend/public/help` — expected: no output.
- [ ] **Step 8: Run the doc tests.** `uv run pytest tests/test_session_wait_documentation.py tests/test_sessions_wait_reply_documentation.py tests/test_pending_question_documentation.py tests/test_twicc_share_skill.py tests/test_twicc_peer_message_skill.py tests/test_process_commands_removal.py -q` — PASS.

---

### Task 12: Skills and reference docs — default cursor, usage rule, busy-message limit (§5, §6)

**Files:** the skills of the spec §6 table "The cursor, everywhere it is described" (`twicc-session`, `twicc-sessions`, `twicc-create-session`, `twicc-process`, `twicc-processes`, `twicc-send-messages`, `twicc-orchestration` + `control-cookbook.md` + `examples/*.md` + `patterns/*.md`, `twicc-orchestration-leader`, `twicc-orchestration-manager`), plus `twicc-send-message/SKILL.md`, `SKILLS-AND-CLI.md`, `ORCHESTRATION.md`, `tests/test_session_wait_documentation.py` (`SANCTIONED`).

- [ ] **Step 1: Apply every row** of the spec §6 skills table (target text verbatim; R = "Re-running resumes, except for a session whose compute is not current; `--since <the instant the batch started>` resumes in every case."). Then its "Reference docs" (`SKILLS-AND-CLI.md:249,259`; `ORCHESTRATION.md:58,89`). `twicc-session/SKILL.md:262` stays.

  **Compute-window qualifier (a spec gap).** Several target texts of the §6 table say, with no condition, that the default returns an answer already given: `twicc-session/SKILL.md:247` ("an answer already given is returned"), `twicc-orchestration/SKILL.md:95` ("so an answer already given is returned"), `twicc-process/SKILL.md:16` ("which returns an answer already given"), `twicc-orchestration/control-cookbook.md:54-58` ("the default returns a winner that finished before the call") and `twicc-orchestration/patterns/worker-pool.md` ("Fresh workers need no `--since`"). Spec §5 "The compute window" makes that false while a session's compute is not current (right after a TwiCC restart until the background compute reaches it, after a compute-version bump, on a Codex legacy rollout): the default then falls back to the current last line and an early answer is missed with `ended`. Qualify it once per document, next to the first such sentence: "(except while a session's compute is not current — e.g. right after a TwiCC restart: then pass `--since` an instant before the spawn or the send)". The same qualifier goes with the equivalent sentences of Task 12 Step 1's "Reference docs" and of the guide's "Waiting" section (Task 13).
- [ ] **Step 2: Usage rule** — state it (spec §6 "The usage rule") in `twicc-send-message`, `twicc-send-messages`, `twicc-session`, `twicc-sessions`, `twicc-orchestration/SKILL.md` where it describes waiting, and `SKILLS-AND-CLI.md` in the same places. Say which commands take `--since` / `--from` and that `--since` is an ISO 8601 instant (no offset = UTC, bare date = midnight UTC) — spec §6 last paragraph.
- [ ] **Step 3: Busy-message limit** — state it once per document, in the terms of spec §5 "Known limit" (rare, accepted, every answer-wait including `--wait-reply`), in `twicc-session`, `twicc-sessions`, `twicc-send-message`, `twicc-send-messages` and `SKILLS-AND-CLI.md` next to the waits. No help text mentions it. Keep the "previous turn's closing message" sentences listed in spec §6.
  **Idle-session wording.** The same fact as in Task 10, in the docs: `twicc-session/SKILL.md:14` ("omitted, it is the session's current last line" — its cursor row in the spec §6 table — and the rest of that bullet), `twicc-session/SKILL.md:266` ("An idle session ends rather than hanging, but only after a ~5 s flush window: below that, `--wait-timeout` can only report `timeout`.") and `SKILLS-AND-CLI.md:259` ("an idle session reports `ended` rather than hanging … a shorter `--wait-timeout` can only ever report `timeout`"): an answer already past the cursor is returned at once; an idle session with **no** answer past the cursor reports `ended` after the ~5 s flush window, and below that a shorter `--wait-timeout` can only report `timeout`. Keep exactly one "~5 s flush window" per sentence that mentions the window.

  **Flush-window wording.** `test_the_documented_numbers_are_read_from_the_code` requires every "flush window" in `twicc-session/SKILL.md` and `SKILLS-AND-CLI.md` to read "~5 s flush window". When you copy the spec's edge case ("the wait ends `ended` after its flush window", §5 "Other edges"), write "after its ~5 s flush window".
- [ ] **Step 4: Sweep** with the spec's own search:
  `grep -rn "2000-01-01\|last line\|already given\|same \`--since\`\|--since <INSTANT>\|next thing\|nobody just\|did not just message\|is missed\|rather than hanging" src/twicc/cli src/twicc/agent/plugin/twicc/skills SKILLS-AND-CLI.md ORCHESTRATION.md tests/test_cli_session_wait.py tests/test_cli_sessions_wait_reply.py` (the spec's own search plus the fragments of the clauses that outlive a partial edit, e.g. "tell me the next thing each of them says" at `twicc-sessions/SKILL.md:32`, whose spec row quotes only the first half of the sentence)
  Every remaining hit is intended (the compute-window fallback "current last line", `twicc-send-messages/SKILL.md:131` with "an instant taken before the send", "an answer already given is returned" with its compute-window qualifier, and the `--since` rule "the last line at or before the instant" at `src/twicc/cli/session.py:704` and `tests/test_cli_session_wait.py:626`). Re-read each.

  **The `twicc-send-messages/SKILL.md:119` recipe** (spec §6 row `:119`) keeps a plural wait with no id and no filter, ``$TWICC sessions wait-reply --since <the instant the batch started>``, which is refused as a bare call (exit 1, `sessions_wait_reply.py:104-109`: `--since` is not a filter) — already at HEAD. While rewriting that clause, fix the recipe: ``$TWICC sessions wait-reply <SESSION_ID>... --since <the instant the batch started>``. It is the only id-less plural recipe (`git grep -n "sessions wait-reply --" -- src/twicc/agent/plugin SKILLS-AND-CLI.md ORCHESTRATION.md frontend/public/help src/twicc/cli`).
- [ ] **Step 5: Run the doc tests.** `uv run pytest tests/test_session_wait_documentation.py tests/test_sessions_wait_reply_documentation.py tests/test_process_commands_removal.py -q`. When a `SANCTIONED` count fails for `twicc-session/SKILL.md` or `SKILLS-AND-CLI.md`, re-read each added `--wait-reply` mention, confirm it is a deliberate cross-reference (usage rule or limit), and raise the count by that exact number. PASS.

---

### Task 13: Migration guide and plugin bump (§6 "The migration guide", "Skills, reference docs, plugin")

**Files:**
- Modify: `frontend/public/help/cli-rpc-migration-2026-10-01.md`
- Modify: `src/twicc/agent/plugin/twicc/.claude-plugin/plugin.json` (`"version": "0.102.1"` → `"0.103.0"`)

- [ ] **Step 1: Re-read** the guide top to bottom (its line numbers are those of `a8df5236`).
- [ ] **Step 2: Apply every bullet** of spec §6 "The migration guide": introduction (`:5` "three changes" → "these changes", `:34`); section 1 (page-size table: 20 everywhere; `share` 20 **now**; the envelope example `"limit": 20`; `:45`, `:70` ("the 50-item page"; the spec says `:71`), `:76`; the `:82-83` "Unchanged: the batch lookups…" line replaced by the new section); section 2 (`:88` "about 60% lighter" goes; `:89-90` and `:124` replaced: `session <id>` and `whoami` join the reduced commands, with their flags, `session self` / `parent`, and the `whoami` key mapping; `:99-114` follow the grown projection); a new section "Batch lookups and `peers`" (`items`, opt-in `--paginated`); a new section "Changed now, without a notice" (the rule of spec "What is released", the three owner exceptions — `share` 20, `artifacts_dir` always the path, agent settings effective with the subagent exception — then the additive changes: `project_directory`, `scratch_dir`, `orchestration_scratch_dir`, `question_widget`; `session self` / `parent`; `session <id>` answering for a row with no user message; `--slim` / `--full` on `session <id>` and `whoami`); section 3 "Reading state" (`:150-152`, "`session <id>` exits `0` and reports `"process": {"state": "dead", …}`" → "…reports `"process": {"state": "dead"}` (from 2026-10-01 without `--full`; `--full` adds `id`, the timestamps and `pid`)"; `:153-157` → "exits `1` when there is no row"; `:169-170` → "with `--full`"); "Waiting" (`--wait-reply` first; default cursor after the last user message, current last line while the compute is not current; R; `--from` / `--since` after a send without `--wait-reply` and which commands take them; `--since` as a date-time; `:212-214` rewritten, not deleted — it is the replacement for `processes wait <ids> user_turn dead --timeout N` (with the `--timeout` → `--wait-timeout` rename): it becomes "→ `sessions wait-reply <ids> --wait-timeout N`", dropping only `--since 2000-01-01` and the "(see "The cursor" below before reusing it on older sessions)" pointer; the "**The cursor.**" bullet at `:227-236` is rewritten in the new terms (default after the last user message, current last line while the compute is not current, `--since` / `--from` after a send without `--wait-reply`, `--from <since_line_num>` to resume one session); `:263-264` → "repeat the call", then R; the usage rule; the busy-message limit); "Stopping" (`:185-187` rewritten; output `{summary, results}`); in the `whoami` part of section 2, advise passing `--slim` or `--full` now, so a script reads the same keys on both sides of the date (a flagless `whoami` keeps `session_id` / `agent_settings` until then); checklist (items 2, 3, 5, item 3 `:296` → 20, and a new item for lookups, `peers`, `whoami`).
  Three general sentences of the guide become false once the "now, without a notice" changes and the new default wait cursor ship; qualify each and point to the new "Changed now, without a notice" section:
  - `:16-18` "Every new behaviour is available now through a flag, and each flag keeps working after the date." → true of every dated change; the changes of "Changed now, without a notice" apply without a flag.
  - `:33` "Until the date, an affected call still returns what it always did" → except the changes listed in "Changed now, without a notice" (`share` page size, `artifacts_dir`, effective agent settings, the new keys).
  - `:40` "A call that prints none is not affected." → "…is not affected by a dated change; the changes of «Changed now, without a notice» print no notice."
  The wait commands and `sessions stop` are unreleased, so their new behaviour is not a change for a released script; the "Waiting" and "Stopping" sections already describe it.
- [ ] **Step 3: Check** `grep -n "50\|60%\|2000-01-01\|TWICC_LISTING_CUTOVER" frontend/public/help/cli-rpc-migration-2026-10-01.md`: every `50` left is an explicit `--limit 50` argument or a released-value mention ("was 50"); no `60%`, no `2000-01-01`, no `TWICC_LISTING_CUTOVER`. Then `grep -n -i "three changes\|unchanged\|still returns everything\|on both\|always carries them\|stops every running session\|is missed\|same \`--since\`\|next thing\|last line" frontend/public/help/cli-rpc-migration-2026-10-01.md` (the other claims Step 2 rewrites: `:5`, `:82`, `:90`, `:124`, `:155` — "exits `1` / on both" is split over `:154-155`, hence the short fragment —, `:170`, `:185`, `:228`, `:264`): re-read each remaining hit; only a sentence still true after this lot may stay (e.g. `:164`, "`provider`, `project_id` → unchanged, top level", in the `processes get` mapping, stays).
- [ ] **Step 4: Bump** `plugin.json` to `0.103.0` (minor: new flags on existing skills). Check that nothing else in this checkout pins the old value: `git grep -n '0\.102\.1' -- ':!docs/plans'` (tracked files only; a plain `grep -r .` would also scan the other checkouts under `.worktrees/`). Expected: no hit other than released CHANGELOG sections, which you must not edit.
- [ ] **Step 5:** No build is needed for the help page (served from `frontend/public/`); the copies under `src/twicc/static/` follow the next build and are not edited.

---

### Task 14: Final verification

- [ ] **Step 1: Guard the override source.** `grep -sc '^TWICC_LISTING_CUTOVER' "${TWICC_DATA_DIR:-$HOME/.twicc}/.env" || true` → `0`, or nothing when the file does not exist (both fine) (the suite loads the `.env` of the data dir `TWICC_DATA_DIR` names, else `~/.twicc/`). If not `0`, stop and ask the owner.
- [ ] **Step 2: Full suite, real clock.** `cd /home/twidi/dev/twicc-poc && uv run pytest -q` (long: run it in the background). Expected: no failure beyond the baseline recorded before Task 1 (Global Constraints) — minus `test_processes_empty_scope_reports_the_same_window`, which Task 2 fixes.
- [ ] **Step 3: Full suite, after side.** `cd /home/twidi/dev/twicc-poc && TWICC_LISTING_CUTOVER=2000-01-01 uv run pytest -q`. Expected: same as Step 2.
- [ ] **Step 4: Full suite, before side.** `cd /home/twidi/dev/twicc-poc && TWICC_LISTING_CUTOVER=2200-01-01 uv run pytest -q`. Expected: same as Step 2.
- [ ] **Step 5: Triage any failure of Steps 2-4 that is not in the baseline.** For each such failing test: (a) it asserts a dated behaviour without pinning its side → add the `before` / `after` fixture or the explicit flag (Global Constraints), never weaken the assertion; (b) it reads an import-time value → make it two-sided on `_output.listing_cutover_passed()`; (c) otherwise it is a code bug → fix it in the file its task owns and re-run Steps 2-4. These three runs replace the two constant-patching `-p` plugin runs of the slim design (spec § Tests "Clock"): `tests/test_pagination_cutover.py::test_the_mcp_descriptions_match_the_side_of_the_cutover_we_are_on`, `tests/test_process_commands_removal.py::test_the_help_texts_match_the_side_of_the_cutover_we_are_on` and the `tests/test_mcp_tools.py` registry checks must pass in all three runs. Update the test docstrings that describe the old way (the override moves the import-time values too, so "the real clock" is now "the effective cutover — the real clock, or `TWICC_LISTING_CUTOVER`"):
  - `tests/test_process_commands_removal.py:379-380` — "Evaluated at import: read the real clock. A known false positive under a plugin that forces the constant, like the pagination descriptions test." → "Evaluated at import: read the effective cutover (the real clock, or `TWICC_LISTING_CUTOVER`, which moves the import-time values too)."
  - `tests/test_process_commands_removal.py:5-8` (module docstring, "those assertions read the real clock") and `:253` ("The registry is built at import from the real clock") → "the effective cutover".
  - `tests/test_slim_cutover.py:375` (`test_the_help_texts_match_the_side_of_the_cutover_we_are_on`, "read the real clock") → "read the effective cutover".
  - `tests/test_mcp_tools.py:21-22` — "Read on the real / clock: MCP_EXCLUDED_ROOTS is evaluated at import." (split over two lines) → "Read on the effective cutover: …".
  - `tests/test_process_commands_removal.py:375` — the section comment `# --- help texts, on the real side of the cutover` → `# --- help texts, on the effective side of the cutover`.
  Find others with `git grep -n -i "real clock\|real side\|the real$" -- tests` (the phrase may be split across two lines: read each hit's neighbour).
- [ ] **Step 6: Lint the touched Python files.** `cd /home/twidi/dev/twicc-poc && uvx ruff check $(git diff --name-only -- '*.py') $(git ls-files --others --exclude-standard -- 'src/*.py' 'tests/*.py')`. The project has no ruff baseline: fix every finding on a line this lot added or changed; leave pre-existing findings on untouched lines.
- [ ] **Step 7: Frontend tests** are unaffected (no `frontend/src` change). Run `cd /home/twidi/dev/twicc-poc/frontend && npm test` only if a `frontend/src` file changed.
- [ ] **Step 8: Report to the owner:** the three suite runs, the lint result, and the reminders: restart the backend via `devctl.py` (help texts and MCP tool descriptions are evaluated at import); no migration; no package to install; plugin bumped to `0.103.0`. Also flag, without editing it (CHANGELOG entries are the owner's call), that the `[Unreleased]` "**CLI and RPC listings**" entry of `CHANGELOG.md` becomes false in three places once this lot ships: "batch lookups such as `sessions get` … keep returning a plain list" (they return `{items}` from the date), "`session content` and `session messages` also start paging at 50" (20), and "Until then nothing changes unless you ask for it" (`share` pages at 20 now, `artifacts_dir` and the agent settings change now). Re-read the top of `CHANGELOG.md` first: if that entry has been promoted into a dated release section, say so instead (released sections are frozen). Also report, as a pre-existing defect outside this lot, the "`64` — Bad CLI usage" exit-code line of the 14 other skills that carry it (`git grep -n "\`64\` — Bad" -- src/twicc/agent/plugin`): a Click usage error exits `2`. Do not commit unless asked.

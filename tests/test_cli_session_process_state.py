"""The ``process`` block the three session-listing commands join onto their rows.

`ProcessRun` lives in its own table, so the query-free session serializer cannot
read it: the join belongs to the CLI, exactly as ``topology`` already does it.
What the tests below pin is less the join than the four answers it must keep
apart — a live row, a table read that found nothing (``dead``), and the two
cases where no state exists at all (a subagent, no running backend), which share
one ``null``.

``dead`` is a statement about TwiCC, not about the world: most sessions TwiCC
indexes it never started, and they read ``dead`` while perfectly alive.
"""

from __future__ import annotations

import orjson
import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from datetime import timedelta

from django.utils import timezone

from twicc.agent.states import AgentState
from twicc.cli import session as cli_session
from twicc.cli import sessions as cli_sessions
from twicc.cli import sessions_get as cli_sessions_get
from twicc.cli._process_state import load_process_rows
from twicc.core.models import ProcessRun, Project, Session, SessionType


TWICC_PID = 4242


@pytest.fixture(autouse=True)
def fresh_placeholder_template(monkeypatch):
    """Drop ``sessions_get``'s cached placeholder between tests.

    It is a module global built ONCE per process from ``Session.objects.first()``
    — and this file is the only place in the suite that calls ``sessions get``
    with an empty ``Session`` table, where the build degrades to ``{"id": None}``
    (``sessions_get.py:38-42``). Without this reset that degraded template
    leaks into every later test in the process, and a test in ANOTHER file
    fails with an unrelated message. Found by an adversarial review, not by the
    suite, which passes only because of file ordering.
    """
    monkeypatch.setattr("twicc.cli.sessions_get._PLACEHOLDER_TEMPLATE", None)


@pytest.fixture
def project(db):
    return Project.objects.create(id="proc-state-project", directory="/tmp/proc-state")


@pytest.fixture
def live_backend(monkeypatch):
    """A running backend at :data:`TWICC_PID`.

    ``settings_test`` isolates the DB and the provider homes but NOT the data
    dir, so an unpatched ``resolve_live_twicc`` reads the developer's own
    ``~/.twicc/twicc.info.json`` and every assertion below would depend on
    whether they happen to have TwiCC running.
    """
    monkeypatch.setattr(
        "twicc.cli._twicc_info.resolve_live_twicc",
        lambda: type("I", (), {"pid": TWICC_PID})(),
    )


@pytest.fixture
def no_backend(monkeypatch):
    monkeypatch.setattr("twicc.cli._twicc_info.resolve_live_twicc", lambda: None)


def make_session(project, sid="s1", **kwargs):
    return Session.objects.create(
        id=sid, project=project, provider="claude_code", file_path=f"{sid}.jsonl",
        created_at="2026-09-17T10:00:00Z", user_message_count=1, **kwargs,
    )


def make_run(session_id, state=AgentState.ASSISTANT_TURN, *, twicc_pid=TWICC_PID, **kwargs):
    kwargs.setdefault("started_at", timezone.now())
    kwargs.setdefault("last_state_change_at", timezone.now())
    return ProcessRun.objects.create(
        session_id=session_id, provider="claude_code", twicc_pid=twicc_pid,
        state=state.value if hasattr(state, "value") else state, **kwargs,
    )


def read(capsysbinary):
    payload = orjson.loads(capsysbinary.readouterr().out)
    return payload["items"] if isinstance(payload, dict) else payload


# ---------------------------------------------------------------------------
# The shape, in both projections
# ---------------------------------------------------------------------------


def test_slim_carries_the_state_alone(project, live_backend, capsysbinary):
    make_session(project)
    make_run("s1")

    cli_sessions.main(project=project.id, slim=True)

    assert read(capsysbinary)[0]["process"] == {"state": "assistant_turn"}


def test_the_full_block_carries_the_five_non_redundant_fields(project, live_backend, capsysbinary):
    """Five, not nine: the session payload already names the session.

    ``provider``, ``session_id``, ``session_title`` and ``project_id`` — which
    ``serialize_process_row`` returns for the ``processes`` family — would be
    duplicates here, in both projections.
    """
    make_session(project)
    make_run("s1", agent_pid=777)

    cli_sessions.main(project=project.id)

    block = read(capsysbinary)[0]["process"]
    assert set(block) == {"id", "state", "started_at", "last_state_change_at", "pid"}
    assert block["pid"] == 777
    assert block["state"] == "assistant_turn"


def test_the_flag_removes_the_key_rather_than_nulling_it(project, live_backend, capsysbinary):
    """An absent key cannot be mistaken for a fact; ``null`` is a fact here."""
    make_session(project)
    make_run("s1")

    cli_sessions.main(project=project.id, include_processes=False)

    assert "process" not in read(capsysbinary)[0]


# ---------------------------------------------------------------------------
# The four answers
# ---------------------------------------------------------------------------


def test_a_read_table_with_no_row_is_dead(project, live_backend, capsysbinary):
    make_session(project)

    cli_sessions.main(project=project.id)

    block = read(capsysbinary)[0]["process"]
    assert block["state"] == "dead"
    assert [block[f] for f in ("id", "started_at", "last_state_change_at", "pid")] == [None] * 4


def test_no_backend_is_dead_like_any_other_absence(project, no_backend, capsysbinary):
    """"TwiCC is down" and "TwiCC runs nothing here" are the same fact.

    An agent does not outlive its TwiCC instance, so there is nothing to be
    agnostic about here. An earlier version answered ``None``, which only gave
    that value a second meaning — and would make a ``--state dead`` filter
    contradict the block it decorates.
    """
    make_session(project)
    make_run("s1")  # a row from a previous instance: no live pid, no match

    cli_sessions.main(project=project.id, slim=True)

    assert read(capsysbinary)[0]["process"] == {"state": "dead"}


def test_a_blocked_agent_surfaces_as_awaiting_user_input(project, live_backend, capsysbinary):
    """The non-obvious stop.

    The stored column stays ``ASSISTANT_TURN`` while a dialog is pending; only
    the virtual projection says the agent is waiting on a human. A caller
    asking "is this worker still working?" gets the wrong answer without it.
    """
    make_session(project)
    make_run("s1", AgentState.ASSISTANT_TURN, awaiting_user_input=True)

    cli_sessions.main(project=project.id, slim=True)

    assert read(capsysbinary)[0]["process"] == {"state": "awaiting_user_input"}


def test_a_subagent_has_no_process_of_its_own(project, live_backend, capsysbinary):
    """It runs inside its parent's process, so any state would be invented."""
    parent = make_session(project)
    make_session(project, "sub1", type=SessionType.SUBAGENT, parent_session=parent)
    make_run("sub1")  # a row that must NOT be read back

    cli_sessions_get.main(["sub1"], slim=True)

    assert read(capsysbinary)[0]["process"] is None


# ---------------------------------------------------------------------------
# The cursor on which instance wrote the row
# ---------------------------------------------------------------------------


def test_a_row_from_another_instance_is_ignored(project, live_backend, capsysbinary):
    """Boot cleanup only runs at the NEXT startup.

    Until then the table holds the previous backend's rows, frozen mid-turn.
    Without the pid filter they read as live agents.
    """
    make_session(project)
    make_run("s1", twicc_pid=TWICC_PID + 1)

    cli_sessions.main(project=project.id, slim=True)

    assert read(capsysbinary)[0]["process"] == {"state": "dead"}


@pytest.mark.django_db
def test_an_unknown_pid_matches_nothing_rather_than_legacy_rows(project):
    """``twicc_pid`` is nullable — "unknown for rows imported from older schemas".

    A ``None`` reaching the ORM renders ``twicc_pid IS NULL`` and matches
    exactly those rows. No CLI path passes one today; the guard is in the
    loader because that failure is silent.
    """
    make_session(project)
    make_run("s1", twicc_pid=None)

    assert load_process_rows(["s1"], None) == {}
    assert set(load_process_rows(["s1"], TWICC_PID)) == set()


# ---------------------------------------------------------------------------
# ``sessions get`` — the placeholder, and the module-level cache behind it
# ---------------------------------------------------------------------------


def test_an_unknown_id_keeps_the_shape_of_a_known_one(project, live_backend, capsysbinary):
    make_session(project)

    cli_sessions_get.main(["s1", "nope"], slim=True)

    known, unknown = read(capsysbinary)
    assert set(known) == set(unknown)


def test_a_just_started_session_reports_its_process_before_it_is_known(project, live_backend, capsysbinary):
    """``known: false`` with a live block is correct, not a bug.

    The ProcessRun row is created before the watcher writes the Session row, so
    an id can carry a running process while TwiCC has no session for it yet.
    """
    make_run("brand-new")

    cli_sessions_get.main(["brand-new"], slim=True)

    entry = read(capsysbinary)[0]
    assert entry["known"] is False
    assert entry["process"] == {"state": "assistant_turn"}


def test_the_placeholder_template_is_not_poisoned_across_calls(project, live_backend, capsysbinary):
    """The MCP server runs these commands in-process, so the module global lives on.

    Writing ``process`` into ``_PLACEHOLDER_TEMPLATE`` instead of into each
    entry would leave the key behind for every later ``--no-processes`` call in
    the same backend process. Order matters: the poisoning call must come
    first, the unknown id is what reaches the template at all, and full mode is
    required because the slim projection would filter the stray key out.
    """
    make_session(project)

    cli_sessions_get.main(["unknown-a"])
    capsysbinary.readouterr()
    cli_sessions_get.main(["s1", "unknown-b"], include_processes=False)

    known, unknown = read(capsysbinary)
    assert "process" not in unknown
    assert set(known) == set(unknown)


# ---------------------------------------------------------------------------
# ``session agents`` — always null, and it costs nothing
# ---------------------------------------------------------------------------


def test_subagent_listings_carry_the_key_so_the_projections_stay_aligned(
    project, live_backend, capsysbinary,
):
    parent = make_session(project)
    make_session(project, "sub1", type=SessionType.SUBAGENT, parent_session=parent)

    cli_session.agents("s1", slim=True)

    assert read(capsysbinary)[0]["process"] is None


def test_the_three_listings_share_one_slim_key_set(project, live_backend, capsysbinary):
    parent = make_session(project)
    make_session(project, "sub1", type=SessionType.SUBAGENT, parent_session=parent)

    cli_sessions.main(project=project.id, slim=True)
    listing = set(read(capsysbinary)[0])
    cli_session.agents("s1", slim=True)
    agents = set(read(capsysbinary)[0])
    cli_sessions_get.main(["s1"], slim=True)
    batch = set(read(capsysbinary)[0])

    assert listing == agents
    assert batch == listing | {"known"}


def test_a_subagent_listing_asks_the_database_nothing(project, live_backend, capsysbinary):
    """The answer is known by construction, so no pid is resolved and no row read."""
    parent = make_session(project)
    make_session(project, "sub1", type=SessionType.SUBAGENT, parent_session=parent)

    with CaptureQueriesContext(connection) as queries:
        cli_session.agents("s1", slim=True)

    assert not [q for q in queries.captured_queries if "core_processrun" in q["sql"]]


# ---------------------------------------------------------------------------
# Cost
# ---------------------------------------------------------------------------


def test_the_join_costs_one_query_whatever_the_page_size(project, live_backend, capsysbinary):
    for i in range(5):
        make_session(project, f"many-{i}")
        make_run(f"many-{i}")

    with CaptureQueriesContext(connection) as one:
        cli_sessions.main(project=project.id, limit=1)
    capsysbinary.readouterr()
    with CaptureQueriesContext(connection) as five:
        cli_sessions.main(project=project.id, limit=5)
    capsysbinary.readouterr()

    def process_queries(ctx):
        return len([q for q in ctx.captured_queries if "core_processrun" in q["sql"]])

    assert process_queries(one) == process_queries(five) == 1


# ---------------------------------------------------------------------------
# The MCP surface, which is generated rather than written
# ---------------------------------------------------------------------------


def test_the_flag_reaches_the_mcp_schema_of_all_three_commands():
    """Adding a CLI option auto-adds the MCP parameter — assert it, don't assume.

    ``--x/--no-x`` needs the generator's ``secondary_opt`` branch to render as
    one boolean rather than two options or none.
    """
    from twicc.rpc.generator import build_registry

    registry = build_registry()

    for path in ("sessions", "sessions/get", "session/agents"):
        params = {p.name: p for p in registry[path].params}
        assert "processes" in params, path
        assert params["processes"].is_flag is True, path
        assert params["processes"].secondary_opt == "--no-processes", path


# ---------------------------------------------------------------------------
# Several rows for one session — which one answers
# ---------------------------------------------------------------------------


def test_the_newest_row_answers_even_when_it_is_the_dead_one(project, live_backend, capsysbinary):
    """A session started and stopped twice leaves several rows under one pid.

    Both halves matter and both had a surviving mutant. Ordering oldest-first
    makes a stale row shadow the current one; excluding DEAD rows resurrects an
    older live one, so a stopped agent reports ``assistant_turn`` — the reading
    an orchestrator would act on.
    """
    make_session(project)
    old = make_run("s1", AgentState.ASSISTANT_TURN)
    make_run("s1", AgentState.DEAD, started_at=old.started_at + timedelta(minutes=5))

    cli_sessions.main(project=project.id, slim=True)

    assert read(capsysbinary)[0]["process"] == {"state": "dead"}


def test_an_older_dead_row_does_not_hide_the_live_one(project, live_backend, capsysbinary):
    """The same guard, the other way round, so neither ordering passes by luck."""
    make_session(project)
    old = make_run("s1", AgentState.DEAD)
    make_run("s1", AgentState.USER_TURN, started_at=old.started_at + timedelta(minutes=5))

    cli_sessions.main(project=project.id, slim=True)

    assert read(capsysbinary)[0]["process"] == {"state": "user_turn"}


def test_a_subagent_is_null_in_full_mode_too(project, live_backend, capsysbinary):
    """Full mode reads ``parent_session_id`` from the serializer, slim from the
    listing projection — two different sources, so both need pinning."""
    parent = make_session(project)
    make_session(project, "sub1", type=SessionType.SUBAGENT, parent_session=parent)
    make_run("sub1")

    cli_sessions_get.main(["sub1"])

    assert read(capsysbinary)[0]["process"] is None


def test_subagent_listings_honour_the_flag_too(project, live_backend, capsysbinary):
    parent = make_session(project)
    make_session(project, "sub1", type=SessionType.SUBAGENT, parent_session=parent)

    cli_session.agents("s1", slim=True, include_processes=False)

    assert "process" not in read(capsysbinary)[0]


# ---------------------------------------------------------------------------
# The Typer wiring — the flag has to actually arrive
# ---------------------------------------------------------------------------
#
# Everything above calls ``main()`` directly, so four mutants survived the whole
# suite: the option defaulting to False, and each of the three commands
# forgetting to forward it. The MCP schema test asserts the parameter exists,
# not that its value travels.


@pytest.fixture
def invoke(project, live_backend, monkeypatch):
    from typer.testing import CliRunner

    from twicc.cli import app

    make_session(project)
    parent = Session.objects.get(id="s1")
    make_session(project, "sub1", type=SessionType.SUBAGENT, parent_session=parent)
    make_run("s1")
    runner = CliRunner()

    def run(*args):
        result = runner.invoke(app, list(args))
        assert result.exit_code == 0, result.output
        payload = orjson.loads(result.stdout)
        return payload["items"] if isinstance(payload, dict) else payload

    return run


@pytest.mark.parametrize("argv", [
    ("sessions",),
    ("sessions", "get", "s1"),
    ("session", "s1", "agents"),
])
def test_the_block_is_there_without_asking_for_it(invoke, argv):
    """On by default — the documented behaviour, and a flipped default is silent."""
    assert "process" in invoke(*argv)[0]


@pytest.mark.parametrize("argv", [
    ("sessions", "--no-processes"),
    ("sessions", "get", "--no-processes", "s1"),
    ("session", "s1", "agents", "--no-processes"),
])
def test_the_flag_travels_from_the_command_line(invoke, argv):
    """Each command has to forward it; three separate wirings, three mutants."""
    assert "process" not in invoke(*argv)[0]

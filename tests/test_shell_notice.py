"""Shared pieces of the background shell notice (spec §3.3, §4, §5.1, §5.5, §6).

No agent: the merge and selection are pure functions; the owner lookup reads
the database only.
"""

from datetime import UTC, datetime

import pytest

from twicc.agent.shell_notice import (
    SHELL_NOTICE_DELAY_SECONDS,
    SHELL_NOTICE_RESOLUTION_GRACE_SECONDS,
    ClaudeLiveOwners,
    OwnerFacts,
    ShellInfo,
    ShellLookup,
    ShellNoticeState,
    ShellOwner,
    ShellResolution,
    build_shell_notice,
    code_span,
    earliest_delay_start,
    merge_resolution,
    needs_lookup,
    resolve_shell_owners,
    select_concerned_shells,
)
from twicc.core.agent_runs import AgentRunState
from twicc.core.models import AgentLink, Project, Session, SessionType, ToolResultLink

NOW = 10_000.0


def shell(key="b1", *, owner=ShellOwner.MAIN, started_at=0.0, **extra):
    fields = {
        "key": key, "shell_id": key, "tool_use_id": None, "owner": owner, "owner_ref": None,
        "owner_label": None, "owner_spawner_ref": None, "owner_run_ended_at": None,
        "owner_running": False, "description": None, "command": None, "output_path": None,
        "started_at": started_at,
    }
    fields.update(extra)
    return ShellInfo(**fields)


def state(*shells, any_running=False, last_end=0.0):
    return ShellNoticeState(idle=True, shells=list(shells), any_subagent_running=any_running,
                            last_subagent_run_end=last_end)


# --- selection -------------------------------------------------------------

def test_own_shell_waits_300s_after_idle():
    s = shell(started_at=0.0)
    idle_since = NOW - SHELL_NOTICE_DELAY_SECONDS + 1
    assert select_concerned_shells(state(s), idle_since=idle_since, notified=(), now=NOW) == []
    assert select_concerned_shells(state(s), idle_since=NOW - SHELL_NOTICE_DELAY_SECONDS,
                                   notified=(), now=NOW) == [s]


def test_own_shell_waits_300s_after_its_own_start():
    s = shell(started_at=NOW - 100)
    assert select_concerned_shells(state(s), idle_since=0.0, notified=(), now=NOW) == []


def test_running_owner_is_never_concerned():
    s = shell(owner=ShellOwner.SUBAGENT, owner_ref="a1", owner_running=True)
    assert select_concerned_shells(state(s), idle_since=0.0, notified=(), now=NOW) == []


def test_orphan_delay_starts_at_the_owner_run_end():
    s = shell(owner=ShellOwner.SUBAGENT, owner_ref="a1", owner_run_ended_at=NOW - 200)
    assert select_concerned_shells(state(s), idle_since=0.0, notified=(), now=NOW) == []
    s = s._replace(owner_run_ended_at=NOW - 300)
    assert select_concerned_shells(state(s), idle_since=0.0, notified=(), now=NOW) == [s]


def test_orphan_with_unknown_run_end_counts_it_as_zero():
    s = shell(owner=ShellOwner.SUBAGENT, owner_ref="a1", owner_run_ended_at=None)
    assert earliest_delay_start(s, 5.0) == 5.0
    assert select_concerned_shells(state(s), idle_since=0.0, notified=(), now=NOW) == [s]


def test_unattributed_is_held_back_while_any_subagent_runs():
    s = shell(owner=ShellOwner.UNATTRIBUTED)
    assert select_concerned_shells(state(s, any_running=True), idle_since=0.0, notified=(), now=NOW) == []
    assert select_concerned_shells(state(s, last_end=NOW - 10), idle_since=0.0, notified=(), now=NOW) == []
    assert select_concerned_shells(state(s, last_end=NOW - 300), idle_since=0.0, notified=(), now=NOW) == [s]


def test_unresolved_is_never_reported():
    s = shell(owner=ShellOwner.UNRESOLVED)
    assert select_concerned_shells(state(s), idle_since=0.0, notified=(), now=NOW) == []


def test_notified_keys_are_skipped():
    s = shell()
    assert select_concerned_shells(state(s), idle_since=0.0, notified={"b1"}, now=NOW) == []


# --- code spans --------------------------------------------------------------

def test_code_span_keeps_the_command_verbatim():
    assert code_span('grep -n "a|b" $f') == '`grep -n "a|b" $f`'


def test_code_span_fence_is_longer_than_any_backtick_run():
    assert code_span("a `b` c") == "``a `b` c``"


def test_code_span_pads_when_value_starts_or_ends_with_a_backtick():
    assert code_span("echo `date`") == "`` echo `date` ``"
    assert code_span("`x`") == "`` `x` ``"


def test_code_span_flattens_whitespace_and_cuts_at_300():
    value = "a\n" + "b" * 400
    rendered = code_span(value)
    assert "\n" not in rendered
    inner = rendered.strip("`")
    assert len(inner) == 300
    assert inner.endswith("…")


# --- message -----------------------------------------------------------------

def test_message_for_an_orphan_subagent_shell():
    s = shell(
        "bg15gnr0j", owner=ShellOwner.SUBAGENT, owner_ref="ad3c71271142417e1",
        owner_label="Implement Task 5c: Codex live + parity",
        description="Wait for full suite to finish",
        command="until grep -qE x; do sleep 5; done",
        output_path="/tmp/tasks/bg15gnr0j.output",
        started_at=NOW - 7 * 60,
    )
    text = build_shell_notice([s], now=NOW)
    assert text == (
        ":: notice from TwiCC: background shell(s) still running\n\n"
        'Your subagent `ad3c71271142417e1` ("Implement Task 5c: Codex live + parity") has finished its work, '
        "but it still has a background shell running:\n\n"
        '- shell `bg15gnr0j`: "Wait for full suite to finish", running for 7 min\n'
        "  - command: `until grep -qE x; do sleep 5; done`\n"
        "  - output: `/tmp/tasks/bg15gnr0j.output`\n\n"
        "Ask your subagent what this shell is for, and tell it to stop the shell if it is not needed."
    )


def test_message_for_an_own_shell_alone():
    s = shell("b9t13uhf9", description="Run full backend suite", started_at=NOW - 6 * 60)
    text = build_shell_notice([s], now=NOW)
    assert text == (
        ":: notice from TwiCC: background shell(s) still running\n\n"
        "You still have a background shell of your own:\n\n"
        '- shell `b9t13uhf9`: "Run full backend suite", running for 6 min\n\n'
        "If it is a long-running process you started on purpose, nothing to do. Otherwise, check what it "
        "is doing, make sure it will end, and keep the user informed so they do not wait for nothing."
    )


def test_message_orders_subagents_then_unattributed_then_own():
    own = shell("m1", started_at=NOW - 60)
    sub = shell("s1", owner=ShellOwner.SUBAGENT, owner_ref="a1", started_at=NOW - 60)
    una = shell("u1", owner=ShellOwner.UNATTRIBUTED, started_at=NOW - 60)
    text = build_shell_notice([own, una, sub], now=NOW)
    assert text.index("`s1`") < text.index("`u1`") < text.index("`m1`")
    assert "One of your subagents, which TwiCC cannot identify, still has a background shell running:" in text
    assert "You also still have a background shell of your own:" in text


def test_message_groups_shells_of_one_subagent_and_uses_the_plural():
    a = shell("s1", owner=ShellOwner.SUBAGENT, owner_ref="a1", started_at=NOW - 60)
    b = shell("s2", owner=ShellOwner.SUBAGENT, owner_ref="a1", started_at=NOW - 120)
    text = build_shell_notice([a, b], now=NOW)
    assert text.count("Your subagent `a1`") == 1
    assert "still has 2 background shells running:" in text
    assert "Ask your subagent what these shells are for, and tell it to stop them if they are not needed." in text
    # Oldest first within the block, whatever the input order.
    assert text.index("- shell `s2`") < text.index("- shell `s1`")


def test_message_names_a_nested_owner_and_its_spawner():
    s = shell("s1", owner=ShellOwner.SUBAGENT, owner_ref="a79858f65df89eb0a",
              owner_spawner_ref="a663305d8fe755548", started_at=NOW - 60)
    text = build_shell_notice([s], now=NOW)
    assert "Subagent `a79858f65df89eb0a`, started by subagent `a663305d8fe755548`, has finished its work" in text
    assert "Check with whichever of the two you can reach" in text


def test_message_escapes_labels_but_not_commands():
    s = shell("s1", owner=ShellOwner.SUBAGENT, owner_ref="a1", owner_label="fix *all* `x`",
              command="echo *", started_at=NOW - 60)
    text = build_shell_notice([s], now=NOW)
    assert '("fix \\*all\\* \\`x\\`")' in text
    assert "command: `echo *`" in text


def resolution(**extra):
    fields = {"owner_id": None, "tool_name": None, "spawner_id": None, "title": None, "known": None,
              "running": None, "stopped_at": None, "first_attempt_at": NOW}
    fields.update(extra)
    return ShellResolution(**fields)


LIVE = ClaudeLiveOwners(labels={"a1": "Nested task"}, running=set(), ended={"a1": NOW - 400})


# --- merge -------------------------------------------------------------------

def test_merge_leaves_live_owners_alone():
    s = shell(owner=ShellOwner.MAIN)
    assert merge_resolution(s, resolution(owner_id="x"), now=NOW, claude_live=LIVE) is s


def test_merge_without_resolution_stays_unresolved():
    s = shell(owner=ShellOwner.UNRESOLVED)
    assert merge_resolution(s, None, now=NOW, claude_live=LIVE) is s


def test_merge_drops_a_nested_monitor():
    s = shell(owner=ShellOwner.UNRESOLVED)
    res = resolution(owner_id="a1", tool_name="Monitor")
    assert merge_resolution(s, res, now=NOW, claude_live=LIVE) is None


def test_merge_claude_uses_live_run_state_and_label():
    s = shell(owner=ShellOwner.UNRESOLVED)
    res = resolution(owner_id="a1", tool_name="Bash", spawner_id="a0", title="You are a COMMAND RUNNER")
    merged = merge_resolution(s, res, now=NOW, claude_live=LIVE)
    assert merged.owner is ShellOwner.SUBAGENT
    assert merged.owner_ref == "a1"
    assert merged.owner_label == "Nested task"
    assert merged.owner_spawner_ref == "a0"
    assert merged.owner_running is False
    assert merged.owner_run_ended_at == NOW - 400


def test_merge_claude_owner_resumed_by_a_task_notification_is_running():
    live = LIVE._replace(running={"a1"})
    merged = merge_resolution(shell(owner=ShellOwner.UNRESOLVED), resolution(owner_id="a1", tool_name="Bash"),
                              now=NOW, claude_live=live)
    assert merged.owner_running is True


def test_merge_claude_owner_never_seen_live_is_unattributed():
    merged = merge_resolution(shell(owner=ShellOwner.UNRESOLVED), resolution(owner_id="zz", tool_name="Bash"),
                              now=NOW, claude_live=LIVE)
    assert merged.owner is ShellOwner.UNATTRIBUTED


def test_merge_no_owner_turns_unattributed_after_the_grace():
    s = shell(owner=ShellOwner.UNRESOLVED)
    fresh = resolution(first_attempt_at=NOW - SHELL_NOTICE_RESOLUTION_GRACE_SECONDS + 1)
    old = resolution(first_attempt_at=NOW - SHELL_NOTICE_RESOLUTION_GRACE_SECONDS)
    assert merge_resolution(s, fresh, now=NOW, claude_live=LIVE).owner is ShellOwner.UNRESOLVED
    assert merge_resolution(s, old, now=NOW, claude_live=LIVE).owner is ShellOwner.UNATTRIBUTED


def test_merge_codex_uses_the_stored_run_state():
    s = shell(owner=ShellOwner.UNRESOLVED)
    res = resolution(owner_id="t1", known=True, running=False, stopped_at=NOW - 500, title="Nested")
    merged = merge_resolution(s, res, now=NOW, claude_live=None)
    assert (merged.owner, merged.owner_ref, merged.owner_running, merged.owner_run_ended_at, merged.owner_label) == (
        ShellOwner.SUBAGENT, "t1", False, NOW - 500, "Nested",
    )


def test_merge_codex_unknown_run_waits_then_turns_unattributed():
    s = shell(owner=ShellOwner.UNRESOLVED)
    fresh = resolution(owner_id="t1", known=False, first_attempt_at=NOW - 10)
    old = resolution(owner_id="t1", known=False, first_attempt_at=NOW - SHELL_NOTICE_RESOLUTION_GRACE_SECONDS)
    assert merge_resolution(s, fresh, now=NOW, claude_live=None).owner is ShellOwner.UNRESOLVED
    assert merge_resolution(s, old, now=NOW, claude_live=None).owner is ShellOwner.UNATTRIBUTED


def test_needs_lookup():
    assert needs_lookup(None, codex=False, now=NOW)
    assert not needs_lookup(resolution(owner_id="a1", tool_name="Bash"), codex=False, now=NOW)
    assert not needs_lookup(resolution(owner_id="a1", tool_name="Monitor"), codex=False, now=NOW)
    assert needs_lookup(resolution(first_attempt_at=NOW - 10), codex=False, now=NOW)
    assert not needs_lookup(resolution(first_attempt_at=NOW - SHELL_NOTICE_RESOLUTION_GRACE_SECONDS),
                            codex=False, now=NOW)
    assert needs_lookup(resolution(owner_id="t1", known=True, running=True), codex=True, now=NOW)
    assert needs_lookup(resolution(owner_id="t1", known=False, first_attempt_at=NOW - 10), codex=True, now=NOW)
    assert not needs_lookup(resolution(owner_id="t1", known=False,
                                       first_attempt_at=NOW - SHELL_NOTICE_RESOLUTION_GRACE_SECONDS),
                            codex=True, now=NOW)


# --- database lookup ----------------------------------------------------------

@pytest.fixture
def tree(db):
    project = Project.objects.create(id="-tmp-shell-notice", directory="/tmp/shell-notice")

    def make(session_id, parent=None, provider="claude_code", title=None):
        return Session.objects.create(
            id=session_id, project=project, provider=provider, file_path=f"{session_id}.jsonl",
            type=SessionType.SUBAGENT if parent else SessionType.SESSION, parent_session=parent, title=title,
        )

    root = make("root-1")
    first = make("a663305d8fe755548", parent=root, title="first level")
    nested = make("a79858f65df89eb0a", parent=root, title="You are a COMMAND RUNNER")
    AgentLink.objects.create(session=root, tool_use_line_num=1, tool_use_id="toolu_spawn_first",
                             agent_id=first.id)
    AgentLink.objects.create(session=first, tool_use_line_num=1, tool_use_id="toolu_spawn_nested",
                             agent_id=nested.id)
    for line, error in ((38, None), (45, "failed")):
        ToolResultLink.objects.create(session=nested, tool_use_line_num=30, tool_result_line_num=line,
                                      tool_use_id="toolu_bash", tool_name="Bash", error=error)
    ToolResultLink.objects.create(session=nested, tool_use_line_num=50, tool_result_line_num=51,
                                  tool_use_id="toolu_monitor", tool_name="Monitor")
    return root, first, nested


def test_lookup_claude_nested_shell(tree):
    root, first, nested = tree
    [facts] = resolve_shell_owners(root.id, [ShellLookup("b5lhayncg", "toolu_bash", None, False)])
    assert facts == OwnerFacts(key="b5lhayncg", owner_id=nested.id, tool_name="Bash", spawner_id=first.id,
                               title="You are a COMMAND RUNNER", known=None, running=None, stopped_at=None)


def test_lookup_claude_first_level_owner_has_no_spawner(tree):
    root, first, _ = tree
    ToolResultLink.objects.create(session=first, tool_use_line_num=2, tool_result_line_num=3,
                                  tool_use_id="toolu_first_bash", tool_name="Bash")
    [facts] = resolve_shell_owners(root.id, [ShellLookup("b1", "toolu_first_bash", None, False)])
    assert (facts.owner_id, facts.spawner_id) == (first.id, None)


def test_lookup_claude_nested_monitor(tree):
    root, _, _ = tree
    [facts] = resolve_shell_owners(root.id, [ShellLookup("bst40x3gu", "toolu_monitor", None, False)])
    assert facts.tool_name == "Monitor"


def test_lookup_claude_link_not_synced_yet(tree):
    root, _, _ = tree
    [facts] = resolve_shell_owners(root.id, [ShellLookup("b9", "toolu_missing", None, False)])
    assert facts.owner_id is None


def test_lookup_ignores_links_outside_the_root_tree(tree):
    root, _, _ = tree
    other_root = Session.objects.create(id="root-2", project=root.project, provider="claude_code",
                                        file_path="root-2.jsonl", type=SessionType.SESSION)
    stranger = Session.objects.create(id="a-other", project=root.project, provider="claude_code",
                                      file_path="a-other.jsonl", type=SessionType.SUBAGENT,
                                      parent_session=other_root)
    ToolResultLink.objects.create(session=stranger, tool_use_line_num=1, tool_result_line_num=2,
                                  tool_use_id="toolu_other", tool_name="Bash")
    [facts] = resolve_shell_owners(root.id, [ShellLookup("b1", "toolu_other", None, False)])
    assert facts.owner_id is None


def test_lookup_codex_reads_the_run_model(tree, monkeypatch):
    root, first, nested = tree
    calls = []

    def fake_states(root_session, agent_ids, *args, **kwargs):
        calls.append((root_session.id, set(agent_ids)))
        return {
            nested.id: AgentRunState(known=True, running=False, run_started_at=None, run_background=None,
                                     stopped_at=datetime(2026, 9, 27, 7, 0, tzinfo=UTC), runs=()),
        }

    monkeypatch.setattr("twicc.core.agent_runs.agent_run_states", fake_states)
    [facts] = resolve_shell_owners(root.id, [ShellLookup("t:1", None, nested.id, True)])
    assert calls == [(root.id, {nested.id})]
    assert (facts.owner_id, facts.spawner_id, facts.known, facts.running) == (nested.id, first.id, True, False)
    assert facts.stopped_at == datetime(2026, 9, 27, 7, 0, tzinfo=UTC).timestamp()


def test_lookup_codex_ended_run_without_stop_time(tree, monkeypatch):
    root, _, nested = tree
    monkeypatch.setattr(
        "twicc.core.agent_runs.agent_run_states",
        lambda root_session, agent_ids, *a, **k: {
            nested.id: AgentRunState(known=True, running=False, run_started_at=None, run_background=None,
                                     stopped_at=None, runs=()),
        },
    )
    [facts] = resolve_shell_owners(root.id, [ShellLookup("t:1", None, nested.id, True)])
    assert (facts.known, facts.running, facts.stopped_at) == (True, False, None)
    merged = merge_resolution(shell(owner=ShellOwner.UNRESOLVED),
                              resolution(owner_id=nested.id, known=True, running=False, stopped_at=None),
                              now=NOW, claude_live=None)
    assert merged.owner_run_ended_at is None
    assert select_concerned_shells(state(merged), idle_since=0.0, notified=(), now=NOW) == [merged]

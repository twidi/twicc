"""
Tests for the group logic in compute.py.

These tests cover all 19 cases documented in docs/specs-partial-visibility.md.
Each case is tested in both batch mode (compute_session_metadata) and
live mode (compute_item_metadata_live).

Legend:
- COLL = COLLAPSIBLE item (e.g., tool_use only)
- ALWAYS = ALWAYS item with visible content (user/assistant message)
- ALWAYS[p] = ALWAYS with collapsible prefix
- ALWAYS[s] = ALWAYS with collapsible suffix
- ALWAYS[p,s] = ALWAYS with both prefix and suffix
- DEBUG = DEBUG_ONLY item (e.g., system message)
- gh = group_head, gt = group_tail
"""

import json
import queue

import orjson
import pytest

from twicc.compute import (
    apply_session_complete,
    compute_item_metadata_live,
    compute_session_metadata,
)
from twicc.core.models import Project, Session, SessionItem


# =============================================================================
# JSON Content Builders
# =============================================================================


def make_collapsible() -> str:
    """Create a COLLAPSIBLE item (assistant with only tool_use)."""
    return json.dumps({
        "type": "assistant",
        "message": {
            "content": [
                {"type": "tool_use", "name": "some_tool"}
            ]
        }
    })


def make_always(prefix: bool = False, suffix: bool = False) -> str:
    """
    Create an ALWAYS item (user message with text).

    Args:
        prefix: If True, add a tool_result before the text (collapsible prefix)
        suffix: If True, add a tool_result after the text (collapsible suffix)
    """
    content = []

    if prefix:
        content.append({"type": "tool_result", "content": "result"})

    content.append({"type": "text", "text": "Hello world"})

    if suffix:
        content.append({"type": "tool_result", "content": "result"})

    return json.dumps({
        "type": "user",
        "message": {
            "content": content
        }
    })


def make_debug() -> str:
    """Create a DEBUG_ONLY item (system message)."""
    return json.dumps({
        "type": "system",
        "subtype": "info",
        "message": "Some system info"
    })


def apply_compute_results(result_queue) -> None:
    """
    Apply compute results from the queue to the database.

    Consumes all messages from the queue and applies corresponding DB operations.
    Used by tests to apply results after calling compute_session_metadata().
    Delegates to apply_session_complete() for the actual DB writes, so tests
    exercise the same code path as the background process.

    Args:
        result_queue: Queue containing compute result messages
    """
    from queue import Empty

    while True:
        try:
            raw_msg = result_queue.get_nowait()
        except Empty:
            break

        msg = orjson.loads(raw_msg)
        msg_type = msg.get('type')

        if msg_type == 'session_complete':
            apply_session_complete(msg)

            # Recalculate activity counters for affected days
            # (in the background process this is batched, but for tests we do it immediately)
            affected_days = msg.get('affected_days')
            project_id = msg.get('project_id')
            if project_id and affected_days:
                from datetime import date as date_cls
                from twicc.core.models import PeriodicActivity
                days = {date_cls.fromisoformat(d) for d in affected_days}
                PeriodicActivity.recalculate_for_days(project_id, days)


# =============================================================================
# Test Fixtures
# =============================================================================


@pytest.fixture
def test_session(db):
    """Create a test project and session."""
    project = Project.objects.create(id="test-project")
    session = Session.objects.create(id="test-session", project=project)
    return session


def create_items(session: Session, contents: list[str]) -> list[SessionItem]:
    """Create SessionItem objects from a list of JSON content strings."""
    items = []
    for i, content in enumerate(contents, start=1):
        item = SessionItem.objects.create(
            session=session,
            line_num=i,
            content=content,
        )
        items.append(item)
    return items


def get_item_state(item: SessionItem) -> tuple[int | None, int | None]:
    """Get (group_head, group_tail) from a SessionItem."""
    item.refresh_from_db()
    return (item.group_head, item.group_tail)


def run_batch(session_id: str):
    """Run batch processing."""
    result_queue = queue.Queue()
    compute_session_metadata(session_id, result_queue)
    apply_compute_results(result_queue)


def run_live(session_id: str, items: list[SessionItem]):
    """Run live processing for each item in order."""
    for item in items:
        compute_item_metadata_live(session_id, item, item.content)
        item.save()


# =============================================================================
# Case 1: A=COLL, B=COLL, C=COLL
# =============================================================================


class TestCase1:
    """
    Case 1: A=COLL, B=COLL, C=COLL

    All three items are COLLAPSIBLE, forming one continuous group.

    Evolution:
        After A: A(gh=1, gt=1)
        After B: A(gh=1, gt=2), B(gh=1, gt=2)
        After C: A(gh=1, gt=3), B(gh=1, gt=3), C(gh=1, gt=3)

    Result: One group A→C
    """

    def test_batch(self, test_session):
        items = create_items(test_session, [
            make_collapsible(),  # A
            make_collapsible(),  # B
            make_collapsible(),  # C
        ])

        run_batch(test_session.id)

        assert get_item_state(items[0]) == (1, 3)  # A
        assert get_item_state(items[1]) == (1, 3)  # B
        assert get_item_state(items[2]) == (1, 3)  # C

    def test_live(self, test_session):
        items = create_items(test_session, [
            make_collapsible(),  # A
            make_collapsible(),  # B
            make_collapsible(),  # C
        ])

        run_live(test_session.id, items)

        assert get_item_state(items[0]) == (1, 3)  # A
        assert get_item_state(items[1]) == (1, 3)  # B
        assert get_item_state(items[2]) == (1, 3)  # C


# =============================================================================
# Case 2: A=COLL, B=COLL, C=ALWAYS (no prefix, no suffix)
# =============================================================================


class TestCase2:
    """
    Case 2: A=COLL, B=COLL, C=ALWAYS (no prefix, no suffix)

    C is ALWAYS without prefix, so it doesn't join the group A→B.
    The group is terminated when C arrives.

    Evolution:
        After A: A(gh=1, gt=1)
        After B: A(gh=1, gt=2), B(gh=1, gt=2)
        After C: A(gh=1, gt=2), B(gh=1, gt=2), C(gh=null, gt=null)
                 Group terminated, C isolated

    Result: Group A→B, C isolated
    """

    def test_batch(self, test_session):
        items = create_items(test_session, [
            make_collapsible(),  # A
            make_collapsible(),  # B
            make_always(),       # C
        ])

        run_batch(test_session.id)

        assert get_item_state(items[0]) == (1, 2)        # A
        assert get_item_state(items[1]) == (1, 2)        # B
        assert get_item_state(items[2]) == (None, None)  # C: isolated

    def test_live(self, test_session):
        items = create_items(test_session, [
            make_collapsible(),  # A
            make_collapsible(),  # B
            make_always(),       # C
        ])

        run_live(test_session.id, items)

        assert get_item_state(items[0]) == (1, 2)        # A
        assert get_item_state(items[1]) == (1, 2)        # B
        assert get_item_state(items[2]) == (None, None)  # C: isolated


# =============================================================================
# Case 3: A=COLL, B=COLL, C=ALWAYS[p] (prefix, no suffix)
# =============================================================================


class TestCase3:
    """
    Case 3: A=COLL, B=COLL, C=ALWAYS[p] (prefix, no suffix)

    C has a prefix that joins the existing group A→B.
    The group extends to include C's prefix (tail becomes C).
    C itself has gh pointing to the group but gt=null (no suffix).

    Evolution:
        After A: A(gh=1, gt=1)
        After B: A(gh=1, gt=2), B(gh=1, gt=2)
        After C: A(gh=1, gt=3), B(gh=1, gt=3), C(gh=1, gt=null)
                 C's prefix joins, group tail updated to C

    Result: Group A→C (includes prefix of C)
    """

    def test_batch(self, test_session):
        items = create_items(test_session, [
            make_collapsible(),       # A
            make_collapsible(),       # B
            make_always(prefix=True), # C
        ])

        run_batch(test_session.id)

        assert get_item_state(items[0]) == (1, 3)     # A
        assert get_item_state(items[1]) == (1, 3)     # B
        assert get_item_state(items[2]) == (1, None)  # C: prefix connected, no suffix

    def test_live(self, test_session):
        items = create_items(test_session, [
            make_collapsible(),       # A
            make_collapsible(),       # B
            make_always(prefix=True), # C
        ])

        run_live(test_session.id, items)

        assert get_item_state(items[0]) == (1, 3)     # A
        assert get_item_state(items[1]) == (1, 3)     # B
        assert get_item_state(items[2]) == (1, None)  # C: prefix connected, no suffix


# =============================================================================
# Case 4: A=COLL, B=COLL, C=ALWAYS[s] (suffix, no prefix)
# =============================================================================


class TestCase4:
    """
    Case 4: A=COLL, B=COLL, C=ALWAYS[s] (suffix, no prefix)

    C has no prefix, so it doesn't join group A→B.
    C has a suffix but nothing follows, so it's orphan (gt=null).

    Evolution:
        After A: A(gh=1, gt=1)
        After B: A(gh=1, gt=2), B(gh=1, gt=2)
        After C: A(gh=1, gt=2), B(gh=1, gt=2), C(gh=null, gt=null)
                 Group terminated (C has no prefix), C's suffix is orphan

    Result: Group A→B, C has orphan suffix (rendered inline by frontend)
    """

    def test_batch(self, test_session):
        items = create_items(test_session, [
            make_collapsible(),       # A
            make_collapsible(),       # B
            make_always(suffix=True), # C
        ])

        run_batch(test_session.id)

        assert get_item_state(items[0]) == (1, 2)        # A
        assert get_item_state(items[1]) == (1, 2)        # B
        assert get_item_state(items[2]) == (None, None)  # C: orphan suffix

    def test_live(self, test_session):
        items = create_items(test_session, [
            make_collapsible(),       # A
            make_collapsible(),       # B
            make_always(suffix=True), # C
        ])

        run_live(test_session.id, items)

        assert get_item_state(items[0]) == (1, 2)        # A
        assert get_item_state(items[1]) == (1, 2)        # B
        assert get_item_state(items[2]) == (None, None)  # C: orphan suffix


# =============================================================================
# Case 5: A=COLL, B=COLL, C=ALWAYS[p,s] (prefix and suffix)
# =============================================================================


class TestCase5:
    """
    Case 5: A=COLL, B=COLL, C=ALWAYS[p,s] (prefix and suffix)

    C has prefix that joins group A→B.
    C also has suffix but nothing follows, so suffix is orphan.

    Evolution:
        After A: A(gh=1, gt=1)
        After B: A(gh=1, gt=2), B(gh=1, gt=2)
        After C: A(gh=1, gt=3), B(gh=1, gt=3), C(gh=1, gt=null)
                 C's prefix joins (tail→C), suffix orphan for now

    Result: Group A→C (prefix of C), suffix of C orphan
            (suffix would connect if D=COLL or D=ALWAYS[p] followed)
    """

    def test_batch(self, test_session):
        items = create_items(test_session, [
            make_collapsible(),                    # A
            make_collapsible(),                    # B
            make_always(prefix=True, suffix=True), # C
        ])

        run_batch(test_session.id)

        assert get_item_state(items[0]) == (1, 3)     # A
        assert get_item_state(items[1]) == (1, 3)     # B
        assert get_item_state(items[2]) == (1, None)  # C: prefix connected, suffix orphan

    def test_live(self, test_session):
        items = create_items(test_session, [
            make_collapsible(),                    # A
            make_collapsible(),                    # B
            make_always(prefix=True, suffix=True), # C
        ])

        run_live(test_session.id, items)

        assert get_item_state(items[0]) == (1, 3)     # A
        assert get_item_state(items[1]) == (1, 3)     # B
        assert get_item_state(items[2]) == (1, None)  # C: prefix connected, suffix orphan


# =============================================================================
# Case 6: A=COLL, B=ALWAYS (no prefix, no suffix), C=COLL
# =============================================================================


class TestCase6:
    """
    Case 6: A=COLL, B=ALWAYS, C=COLL

    B is ALWAYS without prefix/suffix, terminating group A.
    C starts a new group.

    Evolution:
        After A: A(gh=1, gt=1)
        After B: A(gh=1, gt=1), B(gh=null, gt=null)
                 Group A terminated, B isolated
        After C: A(gh=1, gt=1), B(gh=null, gt=null), C(gh=3, gt=3)
                 C starts new group

    Result: Group A alone, B isolated, Group C (will extend if more COLL follow)
    """

    def test_batch(self, test_session):
        items = create_items(test_session, [
            make_collapsible(),  # A
            make_always(),       # B
            make_collapsible(),  # C
        ])

        run_batch(test_session.id)

        assert get_item_state(items[0]) == (1, 1)        # A: group closed by B
        assert get_item_state(items[1]) == (None, None)  # B: isolated
        assert get_item_state(items[2]) == (3, 3)        # C: new group

    def test_live(self, test_session):
        items = create_items(test_session, [
            make_collapsible(),  # A
            make_always(),       # B
            make_collapsible(),  # C
        ])

        run_live(test_session.id, items)

        assert get_item_state(items[0]) == (1, 1)        # A: group closed by B
        assert get_item_state(items[1]) == (None, None)  # B: isolated
        assert get_item_state(items[2]) == (3, 3)        # C: new group


# =============================================================================
# Case 7: A=COLL, B=ALWAYS[p] (prefix, no suffix), C=COLL
# =============================================================================


class TestCase7:
    """
    Case 7: A=COLL, B=ALWAYS[p], C=COLL

    B's prefix joins group A, extending it to B.
    B has no suffix, so C starts a new group.

    Evolution:
        After A: A(gh=1, gt=1)
        After B: A(gh=1, gt=2), B(gh=1, gt=null)
                 B's prefix joins, B terminates group
        After C: A(gh=1, gt=2), B(gh=1, gt=null), C(gh=3, gt=3)
                 C starts new group (B had no suffix)

    Result: Group A→B (includes prefix of B), C starts new group
    """

    def test_batch(self, test_session):
        items = create_items(test_session, [
            make_collapsible(),       # A
            make_always(prefix=True), # B
            make_collapsible(),       # C
        ])

        run_batch(test_session.id)

        assert get_item_state(items[0]) == (1, 2)     # A
        assert get_item_state(items[1]) == (1, None)  # B: prefix connected, no suffix
        assert get_item_state(items[2]) == (3, 3)     # C: new group

    def test_live(self, test_session):
        items = create_items(test_session, [
            make_collapsible(),       # A
            make_always(prefix=True), # B
            make_collapsible(),       # C
        ])

        run_live(test_session.id, items)

        assert get_item_state(items[0]) == (1, 2)     # A
        assert get_item_state(items[1]) == (1, None)  # B: prefix connected, no suffix
        assert get_item_state(items[2]) == (3, 3)     # C: new group


# =============================================================================
# Case 8: A=COLL, B=ALWAYS[s] (suffix, no prefix), C=COLL
# =============================================================================


class TestCase8:
    """
    Case 8: A=COLL, B=ALWAYS[s], C=COLL

    B has no prefix, so group A is terminated.
    B has suffix, C connects to it, forming group B→C.

    Evolution:
        After A: A(gh=1, gt=1)
        After B: A(gh=1, gt=1), B(gh=null, gt=null)
                 Group A terminated, B's suffix pending
        After C: A(gh=1, gt=1), B(gh=null, gt=3), C(gh=2, gt=3)
                 C connects to B's suffix, forming group B→C

    Result: Group A alone, Group B→C (suffix of B + C)
    """

    def test_batch(self, test_session):
        items = create_items(test_session, [
            make_collapsible(),       # A
            make_always(suffix=True), # B
            make_collapsible(),       # C
        ])

        run_batch(test_session.id)

        assert get_item_state(items[0]) == (1, 1)     # A: group closed by B
        assert get_item_state(items[1]) == (None, 3)  # B: no prefix, suffix connected to C
        assert get_item_state(items[2]) == (2, 3)     # C: joins B's suffix group

    def test_live(self, test_session):
        items = create_items(test_session, [
            make_collapsible(),       # A
            make_always(suffix=True), # B
            make_collapsible(),       # C
        ])

        run_live(test_session.id, items)

        assert get_item_state(items[0]) == (1, 1)     # A: group closed by B
        assert get_item_state(items[1]) == (None, 3)  # B: no prefix, suffix connected to C
        assert get_item_state(items[2]) == (2, 3)     # C: joins B's suffix group


# =============================================================================
# Case 9: A=COLL, B=ALWAYS[p,s] (prefix and suffix), C=COLL
# =============================================================================


class TestCase9:
    """
    Case 9: A=COLL, B=ALWAYS[p,s], C=COLL

    B's prefix joins group A, B's suffix starts new group with C.
    B participates in TWO groups: prefix-group (A→B) and suffix-group (B→C).

    Evolution:
        After A: A(gh=1, gt=1)
        After B: A(gh=1, gt=2), B(gh=1, gt=null)
                 B's prefix joins, suffix pending
        After C: A(gh=1, gt=2), B(gh=1, gt=3), C(gh=2, gt=3)
                 C connects to B's suffix

    Result: Group A→B (prefix), Group B→C (suffix)
            B has gh=1 (prefix group) and gt=3 (suffix group)
    """

    def test_batch(self, test_session):
        items = create_items(test_session, [
            make_collapsible(),                    # A
            make_always(prefix=True, suffix=True), # B
            make_collapsible(),                    # C
        ])

        run_batch(test_session.id)

        assert get_item_state(items[0]) == (1, 2)  # A: prefix group
        assert get_item_state(items[1]) == (1, 3)  # B: gh=prefix group, gt=suffix group
        assert get_item_state(items[2]) == (2, 3)  # C: suffix group

    def test_live(self, test_session):
        items = create_items(test_session, [
            make_collapsible(),                    # A
            make_always(prefix=True, suffix=True), # B
            make_collapsible(),                    # C
        ])

        run_live(test_session.id, items)

        assert get_item_state(items[0]) == (1, 2)  # A: prefix group
        assert get_item_state(items[1]) == (1, 3)  # B: gh=prefix group, gt=suffix group
        assert get_item_state(items[2]) == (2, 3)  # C: suffix group


# =============================================================================
# Case 10: A=ALWAYS[s], B=COLL, C=COLL
# =============================================================================


class TestCase10:
    """
    Case 10: A=ALWAYS[s], B=COLL, C=COLL

    A's suffix starts a group, B and C join it.

    Evolution:
        After A: A(gh=null, gt=null)
                 Suffix pending
        After B: A(gh=null, gt=2), B(gh=1, gt=2)
                 B connects to A's suffix
        After C: A(gh=null, gt=3), B(gh=1, gt=3), C(gh=1, gt=3)
                 C extends the group

    Result: Group A→C (suffix of A + B + C)
    """

    def test_batch(self, test_session):
        items = create_items(test_session, [
            make_always(suffix=True), # A
            make_collapsible(),       # B
            make_collapsible(),       # C
        ])

        run_batch(test_session.id)

        assert get_item_state(items[0]) == (None, 3)  # A: suffix connected
        assert get_item_state(items[1]) == (1, 3)     # B: in suffix group
        assert get_item_state(items[2]) == (1, 3)     # C: in suffix group

    def test_live(self, test_session):
        items = create_items(test_session, [
            make_always(suffix=True), # A
            make_collapsible(),       # B
            make_collapsible(),       # C
        ])

        run_live(test_session.id, items)

        assert get_item_state(items[0]) == (None, 3)  # A: suffix connected
        assert get_item_state(items[1]) == (1, 3)     # B: in suffix group
        assert get_item_state(items[2]) == (1, 3)     # C: in suffix group


# =============================================================================
# Case 11: A=ALWAYS[s], B=COLL, C=ALWAYS (no prefix)
# =============================================================================


class TestCase11:
    """
    Case 11: A=ALWAYS[s], B=COLL, C=ALWAYS

    A's suffix + B form a group.
    C has no prefix, so group is terminated, C is isolated.

    Evolution:
        After A: A(gh=null, gt=null)
                 Suffix pending
        After B: A(gh=null, gt=2), B(gh=1, gt=2)
                 B connects to A's suffix
        After C: A(gh=null, gt=2), B(gh=1, gt=2), C(gh=null, gt=null)
                 Group terminated, C isolated

    Result: Group A→B (suffix of A + B), C isolated
    """

    def test_batch(self, test_session):
        items = create_items(test_session, [
            make_always(suffix=True), # A
            make_collapsible(),       # B
            make_always(),            # C
        ])

        run_batch(test_session.id)

        assert get_item_state(items[0]) == (None, 2)     # A: suffix connected to B
        assert get_item_state(items[1]) == (1, 2)        # B: in suffix group
        assert get_item_state(items[2]) == (None, None)  # C: isolated

    def test_live(self, test_session):
        items = create_items(test_session, [
            make_always(suffix=True), # A
            make_collapsible(),       # B
            make_always(),            # C
        ])

        run_live(test_session.id, items)

        assert get_item_state(items[0]) == (None, 2)     # A: suffix connected to B
        assert get_item_state(items[1]) == (1, 2)        # B: in suffix group
        assert get_item_state(items[2]) == (None, None)  # C: isolated


# =============================================================================
# Case 12: A=ALWAYS[s], B=COLL, C=ALWAYS[p]
# =============================================================================


class TestCase12:
    """
    Case 12: A=ALWAYS[s], B=COLL, C=ALWAYS[p]

    A's suffix + B + C's prefix form one group.

    Evolution:
        After A: A(gh=null, gt=null)
                 Suffix pending
        After B: A(gh=null, gt=2), B(gh=1, gt=2)
                 B connects to A's suffix
        After C: A(gh=null, gt=3), B(gh=1, gt=3), C(gh=1, gt=null)
                 C's prefix joins, extending group tail to C

    Result: Group A→C (suffix of A + B + prefix of C)
    """

    def test_batch(self, test_session):
        items = create_items(test_session, [
            make_always(suffix=True), # A
            make_collapsible(),       # B
            make_always(prefix=True), # C
        ])

        run_batch(test_session.id)

        assert get_item_state(items[0]) == (None, 3)  # A: suffix connected
        assert get_item_state(items[1]) == (1, 3)     # B: in group
        assert get_item_state(items[2]) == (1, None)  # C: prefix connected, no suffix

    def test_live(self, test_session):
        items = create_items(test_session, [
            make_always(suffix=True), # A
            make_collapsible(),       # B
            make_always(prefix=True), # C
        ])

        run_live(test_session.id, items)

        assert get_item_state(items[0]) == (None, 3)  # A: suffix connected
        assert get_item_state(items[1]) == (1, 3)     # B: in group
        assert get_item_state(items[2]) == (1, None)  # C: prefix connected, no suffix


# =============================================================================
# Case 13: A=ALWAYS[s], B=ALWAYS[p], C=COLL
# =============================================================================


class TestCase13:
    """
    Case 13: A=ALWAYS[s], B=ALWAYS[p], C=COLL

    A's suffix + B's prefix form a group (two ALWAYS items connected).
    B has no suffix, so C starts a new group.

    Evolution:
        After A: A(gh=null, gt=null)
                 Suffix pending
        After B: A(gh=null, gt=2), B(gh=1, gt=null)
                 B's prefix connects to A's suffix
        After C: A(gh=null, gt=2), B(gh=1, gt=null), C(gh=3, gt=3)
                 C starts new group (B had no suffix)

    Result: Group A→B (suffix A + prefix B), C starts new group
    """

    def test_batch(self, test_session):
        items = create_items(test_session, [
            make_always(suffix=True), # A
            make_always(prefix=True), # B
            make_collapsible(),       # C
        ])

        run_batch(test_session.id)

        assert get_item_state(items[0]) == (None, 2)  # A: suffix connected to B
        assert get_item_state(items[1]) == (1, None)  # B: prefix connected, no suffix
        assert get_item_state(items[2]) == (3, 3)     # C: new group

    def test_live(self, test_session):
        items = create_items(test_session, [
            make_always(suffix=True), # A
            make_always(prefix=True), # B
            make_collapsible(),       # C
        ])

        run_live(test_session.id, items)

        assert get_item_state(items[0]) == (None, 2)  # A: suffix connected to B
        assert get_item_state(items[1]) == (1, None)  # B: prefix connected, no suffix
        assert get_item_state(items[2]) == (3, 3)     # C: new group


# =============================================================================
# Case 14: A=ALWAYS[s], B=ALWAYS[p,s], C=COLL
# =============================================================================


class TestCase14:
    """
    Case 14: A=ALWAYS[s], B=ALWAYS[p,s], C=COLL

    A's suffix + B's prefix form first group.
    B's suffix + C form second group.
    B participates in both groups.

    Evolution:
        After A: A(gh=null, gt=null)
                 Suffix pending
        After B: A(gh=null, gt=2), B(gh=1, gt=null)
                 B's prefix connects to A's suffix, B's suffix pending
        After C: A(gh=null, gt=2), B(gh=1, gt=3), C(gh=2, gt=3)
                 C connects to B's suffix

    Result: Group A→B (suffix A + prefix B), Group B→C (suffix B + C)
    """

    def test_batch(self, test_session):
        items = create_items(test_session, [
            make_always(suffix=True),              # A
            make_always(prefix=True, suffix=True), # B
            make_collapsible(),                    # C
        ])

        run_batch(test_session.id)

        assert get_item_state(items[0]) == (None, 2)  # A: suffix connected to B
        assert get_item_state(items[1]) == (1, 3)     # B: prefix→A, suffix→C
        assert get_item_state(items[2]) == (2, 3)     # C: in B's suffix group

    def test_live(self, test_session):
        items = create_items(test_session, [
            make_always(suffix=True),              # A
            make_always(prefix=True, suffix=True), # B
            make_collapsible(),                    # C
        ])

        run_live(test_session.id, items)

        assert get_item_state(items[0]) == (None, 2)  # A: suffix connected to B
        assert get_item_state(items[1]) == (1, 3)     # B: prefix→A, suffix→C
        assert get_item_state(items[2]) == (2, 3)     # C: in B's suffix group


# =============================================================================
# Case 15: A=ALWAYS, B=COLL, C=COLL
# =============================================================================


class TestCase15:
    """
    Case 15: A=ALWAYS, B=COLL, C=COLL

    A has no prefix/suffix, so it's isolated.
    B and C form their own group.

    Evolution:
        After A: A(gh=null, gt=null)
                 Isolated
        After B: A(gh=null, gt=null), B(gh=2, gt=2)
                 B starts new group
        After C: A(gh=null, gt=null), B(gh=2, gt=3), C(gh=2, gt=3)
                 C joins B's group

    Result: A isolated, Group B→C
    """

    def test_batch(self, test_session):
        items = create_items(test_session, [
            make_always(),       # A
            make_collapsible(),  # B
            make_collapsible(),  # C
        ])

        run_batch(test_session.id)

        assert get_item_state(items[0]) == (None, None)  # A: isolated
        assert get_item_state(items[1]) == (2, 3)        # B: new group
        assert get_item_state(items[2]) == (2, 3)        # C: in B's group

    def test_live(self, test_session):
        items = create_items(test_session, [
            make_always(),       # A
            make_collapsible(),  # B
            make_collapsible(),  # C
        ])

        run_live(test_session.id, items)

        assert get_item_state(items[0]) == (None, None)  # A: isolated
        assert get_item_state(items[1]) == (2, 3)        # B: new group
        assert get_item_state(items[2]) == (2, 3)        # C: in B's group


# =============================================================================
# Case 16: A=ALWAYS, B=ALWAYS[p], C=COLL
# =============================================================================


class TestCase16:
    """
    Case 16: A=ALWAYS, B=ALWAYS[p], C=COLL

    A has no suffix, so B's prefix has nothing to connect to (orphan prefix).
    C starts its own group.

    Evolution:
        After A: A(gh=null, gt=null)
                 Isolated
        After B: A(gh=null, gt=null), B(gh=null, gt=null)
                 B's prefix is orphan (A has no suffix)
        After C: A(gh=null, gt=null), B(gh=null, gt=null), C(gh=3, gt=3)
                 C starts new group

    Result: A isolated, B isolated (orphan prefix rendered inline), C group
    """

    def test_batch(self, test_session):
        items = create_items(test_session, [
            make_always(),            # A
            make_always(prefix=True), # B
            make_collapsible(),       # C
        ])

        run_batch(test_session.id)

        assert get_item_state(items[0]) == (None, None)  # A: isolated
        assert get_item_state(items[1]) == (None, None)  # B: orphan prefix
        assert get_item_state(items[2]) == (3, 3)        # C: new group

    def test_live(self, test_session):
        items = create_items(test_session, [
            make_always(),            # A
            make_always(prefix=True), # B
            make_collapsible(),       # C
        ])

        run_live(test_session.id, items)

        assert get_item_state(items[0]) == (None, None)  # A: isolated
        assert get_item_state(items[1]) == (None, None)  # B: orphan prefix
        assert get_item_state(items[2]) == (3, 3)        # C: new group


# =============================================================================
# Case 17: A=ALWAYS, B=ALWAYS[s], C=COLL
# =============================================================================


class TestCase17:
    """
    Case 17: A=ALWAYS, B=ALWAYS[s], C=COLL

    A is isolated (no prefix/suffix).
    B's suffix connects to C.

    Evolution:
        After A: A(gh=null, gt=null)
                 Isolated
        After B: A(gh=null, gt=null), B(gh=null, gt=null)
                 B's suffix pending
        After C: A(gh=null, gt=null), B(gh=null, gt=3), C(gh=2, gt=3)
                 C connects to B's suffix

    Result: A isolated, Group B→C (suffix of B + C)
    """

    def test_batch(self, test_session):
        items = create_items(test_session, [
            make_always(),            # A
            make_always(suffix=True), # B
            make_collapsible(),       # C
        ])

        run_batch(test_session.id)

        assert get_item_state(items[0]) == (None, None)  # A: isolated
        assert get_item_state(items[1]) == (None, 3)     # B: suffix connected to C
        assert get_item_state(items[2]) == (2, 3)        # C: in B's suffix group

    def test_live(self, test_session):
        items = create_items(test_session, [
            make_always(),            # A
            make_always(suffix=True), # B
            make_collapsible(),       # C
        ])

        run_live(test_session.id, items)

        assert get_item_state(items[0]) == (None, None)  # A: isolated
        assert get_item_state(items[1]) == (None, 3)     # B: suffix connected to C
        assert get_item_state(items[2]) == (2, 3)        # C: in B's suffix group


# =============================================================================
# Case 18: A=ALWAYS, B=ALWAYS[s], C=ALWAYS
# =============================================================================


class TestCase18:
    """
    Case 18: A=ALWAYS, B=ALWAYS[s], C=ALWAYS

    A is isolated.
    B has suffix but C has no prefix, so B's suffix is orphan.
    C is isolated.

    Evolution:
        After A: A(gh=null, gt=null)
                 Isolated
        After B: A(gh=null, gt=null), B(gh=null, gt=null)
                 B's suffix pending
        After C: A(gh=null, gt=null), B(gh=null, gt=null), C(gh=null, gt=null)
                 C has no prefix, B's suffix stays orphan, C isolated

    Result: All three isolated (B's suffix rendered inline by frontend)
    """

    def test_batch(self, test_session):
        items = create_items(test_session, [
            make_always(),            # A
            make_always(suffix=True), # B
            make_always(),            # C
        ])

        run_batch(test_session.id)

        assert get_item_state(items[0]) == (None, None)  # A: isolated
        assert get_item_state(items[1]) == (None, None)  # B: orphan suffix
        assert get_item_state(items[2]) == (None, None)  # C: isolated

    def test_live(self, test_session):
        items = create_items(test_session, [
            make_always(),            # A
            make_always(suffix=True), # B
            make_always(),            # C
        ])

        run_live(test_session.id, items)

        assert get_item_state(items[0]) == (None, None)  # A: isolated
        assert get_item_state(items[1]) == (None, None)  # B: orphan suffix
        assert get_item_state(items[2]) == (None, None)  # C: isolated


# =============================================================================
# Case 19: A=ALWAYS[s], B=ALWAYS[p,s], C=ALWAYS[p]
# =============================================================================


class TestCase19:
    """
    Case 19: A=ALWAYS[s], B=ALWAYS[p,s], C=ALWAYS[p]

    Three ALWAYS items forming two groups via prefix/suffix chains.
    A's suffix + B's prefix = first group.
    B's suffix + C's prefix = second group.

    Evolution:
        After A: A(gh=null, gt=null)
                 Suffix pending
        After B: A(gh=null, gt=2), B(gh=1, gt=null)
                 B's prefix connects to A's suffix, B's suffix pending
        After C: A(gh=null, gt=2), B(gh=1, gt=3), C(gh=2, gt=null)
                 C's prefix connects to B's suffix

    Result: Group A→B (suffix A + prefix B), Group B→C (suffix B + prefix C)
    """

    def test_batch(self, test_session):
        items = create_items(test_session, [
            make_always(suffix=True),              # A
            make_always(prefix=True, suffix=True), # B
            make_always(prefix=True),              # C
        ])

        run_batch(test_session.id)

        assert get_item_state(items[0]) == (None, 2)  # A: suffix connected to B
        assert get_item_state(items[1]) == (1, 3)     # B: prefix→A, suffix→C
        assert get_item_state(items[2]) == (2, None)  # C: prefix connected, no suffix

    def test_live(self, test_session):
        items = create_items(test_session, [
            make_always(suffix=True),              # A
            make_always(prefix=True, suffix=True), # B
            make_always(prefix=True),              # C
        ])

        run_live(test_session.id, items)

        assert get_item_state(items[0]) == (None, 2)  # A: suffix connected to B
        assert get_item_state(items[1]) == (1, 3)     # B: prefix→A, suffix→C
        assert get_item_state(items[2]) == (2, None)  # C: prefix connected, no suffix


# =============================================================================
# Additional Tests: DEBUG_ONLY items
# =============================================================================


class TestDebugOnlyItems:
    """
    Tests for DEBUG_ONLY items.

    DEBUG_ONLY items are "transparent" to groups:
    - They don't participate in any group (gh=null, gt=null)
    - They don't break existing groups
    - Groups "see through" them to connect
    """

    def test_debug_between_collapsibles_batch(self, test_session):
        """
        A=COLL, D=DEBUG, B=COLL

        DEBUG_ONLY between COLLAPSIBLE items doesn't break the group.
        The group A→B forms as if D wasn't there.

        Evolution:
            After A: A(gh=1, gt=1)
            After D: A(gh=1, gt=1), D(gh=null, gt=null)
            After B: A(gh=1, gt=3), D(gh=null, gt=null), B(gh=1, gt=3)
                     B joins A's group, skipping D
        """
        items = create_items(test_session, [
            make_collapsible(),  # A
            make_debug(),        # D
            make_collapsible(),  # B
        ])

        run_batch(test_session.id)

        assert get_item_state(items[0]) == (1, 3)        # A
        assert get_item_state(items[1]) == (None, None)  # D: no group
        assert get_item_state(items[2]) == (1, 3)        # B: in A's group

    def test_debug_between_collapsibles_live(self, test_session):
        """Same as batch version but using live processing."""
        items = create_items(test_session, [
            make_collapsible(),  # A
            make_debug(),        # D
            make_collapsible(),  # B
        ])

        run_live(test_session.id, items)

        assert get_item_state(items[0]) == (1, 3)        # A
        assert get_item_state(items[1]) == (None, None)  # D: no group
        assert get_item_state(items[2]) == (1, 3)        # B: in A's group

    def test_debug_between_always_suffix_and_collapsible_batch(self, test_session):
        """
        A=ALWAYS[s], D=DEBUG, B=COLL

        DEBUG_ONLY between ALWAYS[s] and COLL doesn't break the suffix connection.
        B connects to A's suffix, forming group A→B.

        Evolution:
            After A: A(gh=null, gt=null) - suffix pending
            After D: A(gh=null, gt=null), D(gh=null, gt=null)
            After B: A(gh=null, gt=3), D(gh=null, gt=null), B(gh=1, gt=3)
                     B connects to A's suffix, skipping D
        """
        items = create_items(test_session, [
            make_always(suffix=True),  # A
            make_debug(),              # D
            make_collapsible(),        # B
        ])

        run_batch(test_session.id)

        assert get_item_state(items[0]) == (None, 3)     # A: suffix connected
        assert get_item_state(items[1]) == (None, None)  # D: no group
        assert get_item_state(items[2]) == (1, 3)        # B: in A's suffix group

    def test_debug_between_always_suffix_and_collapsible_live(self, test_session):
        """Same as batch version but using live processing."""
        items = create_items(test_session, [
            make_always(suffix=True),  # A
            make_debug(),              # D
            make_collapsible(),        # B
        ])

        run_live(test_session.id, items)

        assert get_item_state(items[0]) == (None, 3)     # A: suffix connected
        assert get_item_state(items[1]) == (None, None)  # D: no group
        assert get_item_state(items[2]) == (1, 3)        # B: in A's suffix group

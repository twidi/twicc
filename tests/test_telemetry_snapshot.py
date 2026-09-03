from datetime import date, datetime, timedelta, UTC
from decimal import Decimal

import orjson
import pytest

from twicc.core.models import (
    ArtifactBookmark,
    DailyActivity,
    Peer,
    PeerMessage,
    PeerMessageDirection,
    PeerMessageStatus,
    PeerState,
    PinMode,
    Project,
    Session,
    SessionCron,
    SessionType,
    Share,
    Workflow,
)
from twicc.telemetry import snapshot

DAY = date(2026, 7, 10)


def _at(hour: int) -> datetime:
    return datetime(DAY.year, DAY.month, DAY.day, hour, 0, tzinfo=UTC)


@pytest.fixture
def project(transactional_db):
    return Project.objects.create(id="-tmp-telemetry", directory="/tmp/telemetry-snapshot-test")


def _make_session(project, session_id, **overrides):
    defaults = {
        "id": session_id,
        "project": project,
        "provider": "claude_code",
        "file_path": f"{session_id}.jsonl",
        "type": SessionType.SESSION,
        "created_at": _at(9),
    }
    defaults.update(overrides)
    return Session.objects.create(**defaults)


def _make_peer(state=PeerState.ACTIVE):
    return Peer.objects.create(base_url="https://peer.example/", state=state)


_peer_message_seq = iter(range(1, 10_000))


def _make_peer_message(peer, *, direction, author=None, reply_to="", origin=None, created_at=None):
    """One peer message, backdated to ``DAY`` (``created_at`` is auto_now_add)."""
    message_id = f"pm_{next(_peer_message_seq):04d}"
    if origin is None:
        origin = {"sent_at": _at(9).isoformat(), "author": author}
    message = PeerMessage.objects.create(
        peer=peer,
        direction=direction,
        message_id=message_id,
        reply_to=reply_to,
        thread_id=reply_to or message_id,
        title="subject",
        payload={"text": "body", "images": [], "documents": []},
        origin=origin,
        status=PeerMessageStatus.PENDING,
    )
    PeerMessage.objects.filter(pk=message.pk).update(created_at=created_at or _at(9))
    return message


def test_build_day_block(project):
    s1 = _make_session(
        project, "s1", provider="claude_code", selected_model="opus",
        effort="high", permission_mode="bypassPermissions",
    )
    _make_session(
        project, "s2", provider="claude_code", selected_model="opus",
        effort="high", permission_mode="bypassPermissions", spawned_by=s1,
    )
    _make_session(
        project, "s3", provider="codex", selected_model="gpt-terra",
        effort="high", permission_mode="yolo",
    )
    _make_session(project, "sub1", type=SessionType.SUBAGENT)

    DailyActivity.objects.create(project=None, provider="claude_code", date=DAY, user_message_count=7, cost=Decimal("5.5"))
    DailyActivity.objects.create(project=None, provider="codex", date=DAY, user_message_count=3, cost=Decimal("7.0"))

    # created_at/updated_at are auto_now_add/auto_now: any value passed at
    # creation is overridden by Django at save() time, so backdate to DAY
    # with a bare update() after the row exists.
    share = Share.objects.create(kind="session", token="t" * 32, session=s1)
    Share.objects.filter(pk=share.pk).update(created_at=_at(9))
    bookmark = ArtifactBookmark.objects.create(
        session=s1, project=project, relative_path="demo/index.html", name="Demo", scope=PinMode.PROJECT,
    )
    ArtifactBookmark.objects.filter(pk=bookmark.pk).update(created_at=_at(9))

    block = snapshot.build_day_block(DAY, {"presence_minutes": 45, "peak_agents": 3})

    assert block["date"] == DAY.isoformat()
    # Nested provider -> family -> version -> effort counts; resolve the
    # current latest version from the registry so the test survives new
    # model releases.
    from twicc.providers.helpers import get_provider_helpers

    opus = get_provider_helpers("claude_code").find_model("opus")
    terra = get_provider_helpers("codex").find_model("gpt-terra")
    assert block["sessions_by_model_effort"] == {
        "claude_code": {"opus": {opus.version: {"high": 2}}},
        "codex": {"gpt-terra": {terra.version: {"high": 1}}},
    }
    assert block["sessions_by_permission_mode"] == {
        "claude_code": {"bypassPermissions": 2},
        "codex": {"yolo": 1},
    }
    assert block["messages_sent"] == 10
    assert block["subagents"] == 1
    assert block["sessions_spawned"] == 1
    assert block["shares_created"] == 1
    assert block["bookmarks_created"] == 1
    assert block["cost_bucket"] == "10-50"
    assert block["presence_bucket"] == "30-120"
    assert block["peak_agents"] == 3


def test_build_day_block_counts_workflow_runs_and_crons_created(project):
    session = _make_session(project, "wf-session")
    Workflow.objects.create(session=session, run_id="wf_test_1", raw_json="{}")
    Workflow.objects.filter(run_id="wf_test_1").update(updated_at=_at(9))
    SessionCron.objects.create(
        provider="claude_code", cron_id="cron-1", session_id=session.id,
        cron_expr="* * * * *", recurring=False, prompt="do it",
        created_at=_at(9), next_fire=_at(10),
    )

    block = snapshot.build_day_block(DAY, {})

    assert block["workflow_runs"] == 1
    assert block["crons_created"] == 1


def test_build_day_block_counts_peer_messages_by_direction_kind_author(project):
    peer = _make_peer()
    _make_peer_message(peer, direction=PeerMessageDirection.OUT, author="agent")
    _make_peer_message(peer, direction=PeerMessageDirection.OUT, author="human")
    _make_peer_message(peer, direction=PeerMessageDirection.OUT, author="agent", reply_to="pm_remote")
    _make_peer_message(peer, direction=PeerMessageDirection.IN, author="agent", reply_to="pm_ours")
    _make_peer_message(peer, direction=PeerMessageDirection.IN, author="agent", reply_to="pm_ours")
    # Outside the day: never counted.
    _make_peer_message(peer, direction=PeerMessageDirection.OUT, author="agent", created_at=_at(9) - timedelta(days=1))

    block = snapshot.build_day_block(DAY, {})

    assert block["peer_messages_by_direction_kind_author"] == {
        "out": {"new": {"agent": 1, "human": 1}, "reply": {"agent": 1}},
        "in": {"reply": {"agent": 2}},
    }


def test_build_day_block_omits_peer_messages_without_traffic(project):
    # Sparse like sessions_by_model_effort: no traffic, no keys.
    assert snapshot.build_day_block(DAY, {})["peer_messages_by_direction_kind_author"] == {}


def test_build_day_block_clamps_unknown_peer_author(project):
    # The author is sender-declared. Whatever a remote instance puts there, it
    # must never become a payload key (§3.3).
    peer = _make_peer()
    _make_peer_message(peer, direction=PeerMessageDirection.IN, author="arbitrary-remote-string")
    _make_peer_message(peer, direction=PeerMessageDirection.IN, origin={})

    block = snapshot.build_day_block(DAY, {})

    assert block["peer_messages_by_direction_kind_author"] == {"in": {"new": {"unknown": 2}}}
    assert "arbitrary-remote-string" not in orjson.dumps(block).decode()


def test_instance_block_counts_active_peers_only(project):
    _make_peer(state=PeerState.ACTIVE)
    _make_peer(state=PeerState.ACTIVE)
    _make_peer(state=PeerState.PENDING_SENT)
    _make_peer(state=PeerState.BROKEN)
    _make_peer(state=PeerState.REVOKED)

    assert snapshot.build_instance_block()["peers_active_bucket"] == "2-5"


def test_instance_block_reports_peer_messaging_gate(project, monkeypatch):
    # An empty peerBaseUrl disables the whole feature; the boolean is that gate.
    monkeypatch.setattr(snapshot, "peer_base_url", lambda: "")
    assert snapshot.build_instance_block()["peer_messaging"] is False

    monkeypatch.setattr(snapshot, "peer_base_url", lambda: "https://peer.example")
    assert snapshot.build_instance_block()["peer_messaging"] is True


def test_build_day_block_defaults_missing_day_state_to_zero(project):
    block = snapshot.build_day_block(DAY, {})
    assert block["presence_bucket"] == "0"
    assert block["peak_agents"] == 0
    assert block["cost_bucket"] == "0"
    assert block["messages_sent"] == 0


def test_bucket_edges():
    assert snapshot.bucket(0, snapshot.WORKSPACE_BUCKETS) == "0"
    assert snapshot.bucket(1, snapshot.WORKSPACE_BUCKETS) == "1"
    assert snapshot.bucket(2, snapshot.WORKSPACE_BUCKETS) == "2-5"
    assert snapshot.bucket(5, snapshot.WORKSPACE_BUCKETS) == "2-5"
    assert snapshot.bucket(6, snapshot.WORKSPACE_BUCKETS) == "6-20"
    assert snapshot.bucket(20, snapshot.WORKSPACE_BUCKETS) == "6-20"
    assert snapshot.bucket(21, snapshot.WORKSPACE_BUCKETS) == "21+"

    assert snapshot.bucket(20, snapshot.PROJECT_BUCKETS) == "6-20"
    assert snapshot.bucket(21, snapshot.PROJECT_BUCKETS) == "21-50"
    assert snapshot.bucket(50, snapshot.PROJECT_BUCKETS) == "21-50"
    assert snapshot.bucket(51, snapshot.PROJECT_BUCKETS) == "51-100"
    assert snapshot.bucket(100, snapshot.PROJECT_BUCKETS) == "51-100"
    assert snapshot.bucket(101, snapshot.PROJECT_BUCKETS) == "101+"

    assert snapshot.bucket(Decimal(0), snapshot.COST_BUCKETS) == "0"
    assert snapshot.bucket(Decimal("0.5"), snapshot.COST_BUCKETS) == "<1"
    assert snapshot.bucket(Decimal(1), snapshot.COST_BUCKETS) == "1-10"
    assert snapshot.bucket(Decimal(10), snapshot.COST_BUCKETS) == "10-50"
    assert snapshot.bucket(Decimal(50), snapshot.COST_BUCKETS) == "50-100"
    assert snapshot.bucket(Decimal(100), snapshot.COST_BUCKETS) == "100-250"
    assert snapshot.bucket(Decimal(250), snapshot.COST_BUCKETS) == "250-500"
    assert snapshot.bucket(Decimal(500), snapshot.COST_BUCKETS) == "500-1000"
    assert snapshot.bucket(Decimal(1000), snapshot.COST_BUCKETS) == "1000+"

    assert snapshot.bucket(0, snapshot.PRESENCE_BUCKETS) == "0"
    assert snapshot.bucket(29, snapshot.PRESENCE_BUCKETS) == "<30"
    assert snapshot.bucket(30, snapshot.PRESENCE_BUCKETS) == "30-120"
    assert snapshot.bucket(120, snapshot.PRESENCE_BUCKETS) == "120-360"
    assert snapshot.bucket(360, snapshot.PRESENCE_BUCKETS) == "360-720"
    assert snapshot.bucket(720, snapshot.PRESENCE_BUCKETS) == "720+"


def test_instance_block_contains_no_forbidden_fields(project):
    block = snapshot.build_instance_block()
    serialized = orjson.dumps(block).decode()

    import socket

    assert socket.gethostname() not in serialized
    assert project.directory not in serialized
    assert "/tmp/telemetry-snapshot-test" not in serialized


def test_day_block_contains_no_forbidden_fields(project):
    # A bogus/unresolvable selected_model must collapse to "unknown" — the raw
    # string is a per-session identifier and must never reach the payload,
    # which is exactly what would happen if it leaked as a dict key (§3.3).
    bogus_model = "definitely-not-a-real-model-id-xyz789"
    _make_session(project, "bogus-model-session", selected_model=bogus_model)

    block = snapshot.build_day_block(DAY, {})
    serialized = orjson.dumps(block).decode()

    import socket

    assert bogus_model not in serialized
    assert block["sessions_by_model_effort"] == {"claude_code": {"unknown": {"unknown": {"unknown": 1}}}}
    assert socket.gethostname() not in serialized
    assert project.directory not in serialized
    assert "/tmp/telemetry-snapshot-test" not in serialized


def test_model_family_falls_back_to_raw_sdk_model_id(project):
    # Sessions not created through TwiCC (external CLI runs, benchmarks) have
    # no agent-settings bundle (selected_model NULL) but carry the raw SDK
    # model id in Session.model — the family must resolve from it.
    _make_session(project, "external-session", selected_model=None, model="claude-opus-4-8")

    block = snapshot.build_day_block(DAY, {})

    assert block["sessions_by_model_effort"] == {"claude_code": {"opus": {"4.8": {"unknown": 1}}}}


def test_build_payload_returns_none_without_complete_unsent_day(project):
    today = snapshot.datetime.now(UTC).date()
    state = {
        "instance_id": "abc-123",
        "last_sent_date": today.isoformat(),
        "days": {},
    }
    assert snapshot.build_payload(state) is None


def test_build_payload_covers_complete_days_after_last_sent(project):
    today = snapshot.datetime.now(UTC).date()
    complete_day = today - timedelta(days=1)  # yesterday: the most recent complete UTC day
    last_sent = complete_day - timedelta(days=1)

    _make_session(
        project, "payload-session",
        created_at=datetime(complete_day.year, complete_day.month, complete_day.day, 9, tzinfo=UTC),
        selected_model="opus", effort="high", permission_mode="bypassPermissions",
    )

    state = {
        "instance_id": "abc-123",
        "last_sent_date": last_sent.isoformat(),
        "days": {complete_day.isoformat(): {"presence_minutes": 10, "peak_agents": 1}},
    }
    payload = snapshot.build_payload(state)

    assert payload is not None
    assert payload["schema"] == snapshot.SCHEMA_VERSION
    assert payload["instance_id"] == "abc-123"
    assert payload["days"][-1]["date"] == complete_day.isoformat()
    from twicc.providers.helpers import get_provider_helpers

    opus = get_provider_helpers("claude_code").find_model("opus")
    assert payload["days"][-1]["sessions_by_model_effort"] == {
        "claude_code": {"opus": {opus.version: {"high": 1}}}
    }
    # "today" itself is never a complete day.
    assert today.isoformat() not in [d["date"] for d in payload["days"]]

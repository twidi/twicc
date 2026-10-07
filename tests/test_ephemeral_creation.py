"""Trusted ephemeral creation and admission rollback tests."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from twicc.agent import ephemeral
from twicc.core.models import Project, Session
from twicc.core.services.session_creation import create_session_from_payload


@pytest.fixture(autouse=True)
def reset_admissions():
    ephemeral.clear()
    yield
    ephemeral.clear()


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize(
    "extra,code",
    [
        ({"hybrid": True}, "ephemeral_hybrid_conflict"),
        ({"worktree_path": "/tmp/no"}, "ephemeral_worktree_unsupported"),
        ({"spawned_by_session_id": "parent"}, "ephemeral_spawn_unsupported"),
        ({"text": "/goal test"}, "ephemeral_command_unsupported"),
    ],
)
def test_conflicts_do_not_stash_or_leave_admission(extra, code):
    payload = {"session_id": "draft", "provider": "codex", "project_id": "p", "text": "go", "ephemeral": True, **extra}
    with patch("twicc.core.services.session_creation.set_pending_agent_settings") as stash:
        result = asyncio.run(create_session_from_payload(payload, allow_ephemeral=True))
    assert result.errors[0].code == code
    stash.assert_not_called()
    assert ephemeral.pending_snapshot() == []
    assert ephemeral.is_known("draft")


@pytest.mark.parametrize("mode", [None, False, True])
def test_known_id_rejected_even_on_other_provider(mode):
    ephemeral.reserve("draft", "claude_code", "p")
    result = asyncio.run(
        create_session_from_payload(
            {"session_id": "draft", "provider": "codex", "project_id": "p", "text": "go", "ephemeral": mode},
            allow_ephemeral=True,
        )
    )
    assert result.errors[0].code == "ephemeral_readonly"
    assert len(ephemeral.pending_snapshot()) == 1  # The rejected request never settles its owner's admission.


@pytest.mark.django_db(transaction=True)
def test_existing_persistent_row_releases_reservation(tmp_path):
    project = Project.objects.create(id="p", directory=str(tmp_path))
    Session.objects.create(id="existing", project=project, provider="claude_code")
    result = asyncio.run(
        create_session_from_payload(
            {"session_id": "existing", "project_id": "p", "provider": "claude_code", "text": "go", "ephemeral": True},
            allow_ephemeral=True,
        )
    )
    assert result.errors[0].code == "ephemeral_existing_session"
    assert not ephemeral.is_known("existing")
    ephemeral.check_readonly("existing")


@pytest.mark.django_db(transaction=True)
def test_untrusted_caller_ignores_flag_and_keeps_normal_creation(tmp_path):
    Project.objects.create(id="p", directory=str(tmp_path))
    manager = SimpleNamespace(create_session=AsyncMock(return_value="draft"))
    with (
        patch("twicc.core.services.session_creation.ensure_provider_running"),
        patch(
            "twicc.agent.registry.get_agent_manager_registry",
            return_value=SimpleNamespace(get=lambda provider: manager),
        ),
    ):
        result = asyncio.run(
            create_session_from_payload(
                {
                    "session_id": "draft",
                    "project_id": "p",
                    "provider": "claude_code",
                    "text": "go",
                    "layout": {},
                    "ephemeral": True,
                }
            )
        )
    assert result.success
    assert not manager.create_session.call_args.kwargs.get("ephemeral")
    assert not ephemeral.is_known("draft")
    ephemeral.drain_buffers("draft")


@pytest.mark.django_db(transaction=True)
def test_normal_creation_claim_prevents_ephemeral_factory_and_buffer_collision(tmp_path):
    from twicc.pending_session_attributes import get_pending_session_attributes

    Project.objects.create(id="mixed", directory=str(tmp_path))

    async def scenario():
        started = asyncio.Event()
        release = asyncio.Event()

        async def factory(*args, **kwargs):
            started.set()
            await release.wait()
            return "shared"

        normal_manager = SimpleNamespace(create_session=AsyncMock(side_effect=factory))
        ephemeral_manager = SimpleNamespace(create_session=AsyncMock(return_value="unexpected"))
        registry = SimpleNamespace(
            get=lambda provider: normal_manager if provider.value == "claude_code" else ephemeral_manager
        )
        normal_payload = {
            "session_id": "shared",
            "project_id": "mixed",
            "provider": "claude_code",
            "text": "normal",
            "layout": {},
        }
        with (
            patch("twicc.core.services.session_creation.ensure_provider_running"),
            patch("twicc.agent.registry.get_agent_manager_registry", return_value=registry),
        ):
            first = asyncio.create_task(create_session_from_payload(normal_payload))
            await started.wait()
            original = get_pending_session_attributes("shared")
            second = await create_session_from_payload(
                {**normal_payload, "provider": "codex", "text": "ephemeral", "ephemeral": True}, allow_ephemeral=True
            )
            assert not second.success
            assert second.errors[0].code == "agent_starting"
            assert get_pending_session_attributes("shared") is original
            ephemeral_manager.create_session.assert_not_called()
            release.set()
            assert (await first).success
        assert not ephemeral.is_known("shared")
        ephemeral.check_readonly("shared")
        ephemeral.drain_buffers("shared")

    asyncio.run(scenario())


@pytest.mark.django_db(transaction=True)
def test_ephemeral_refs_needing_artifacts_fail_before_any_stash(tmp_path):
    """The whole plan is refused (with the file names) before a stash or a manager call."""
    from twicc.core.services.attachments import planner
    from twicc.core.services.attachments import target as plan_target
    from twicc.core.services.attachments.types import PlanTarget
    from twicc.pending_agent_settings import _pending as pending_agent_settings
    from twicc.pending_session_attributes import get_pending_session_attributes
    from twicc.pending_titles import get_pending_title

    Project.objects.create(id="p", directory=str(tmp_path))
    manager = SimpleNamespace(create_session=AsyncMock(return_value="draft"))

    def plan(refs, target, *, text):
        assert target.ephemeral is True
        raise planner.AttachmentPlanError(
            "attachment_requires_artifacts", "Ephemeral sessions only accept native attachments",
            names=("clip.mp4",),
        )

    async def target(**kwargs):
        return PlanTarget("claude_code", False, kwargs["ephemeral"], "opus", False, "first_party")

    with (
        patch("twicc.core.services.session_creation.ensure_provider_running"),
        patch("twicc.agent.registry.get_agent_manager_registry", return_value=SimpleNamespace(get=lambda p: manager)),
        patch.object(planner, "plan_attachments", plan),
        patch.object(plan_target, "resolve_plan_target", target),
    ):
        result = asyncio.run(create_session_from_payload(
            {
                "session_id": "draft", "project_id": "p", "provider": "claude_code", "text": "go",
                "title": "T", "ephemeral": True,
                "attachments": [{"bucket": "b", "id": "6f1c1f0e-8a8e-4c55-9d1e-0b0c8f6c1a01"}],
            },
            allow_ephemeral=True,
        ))
    assert [e.code for e in result.errors] == ["attachment_requires_artifacts"]
    assert result.errors[0].message == "Ephemeral sessions only accept native attachments: clip.mp4"
    assert result.error_names == ("clip.mp4",)
    manager.create_session.assert_not_called()
    assert get_pending_title("draft") is None
    assert "draft" not in pending_agent_settings
    assert get_pending_session_attributes("draft") is None
    assert ephemeral.pending_snapshot() == []

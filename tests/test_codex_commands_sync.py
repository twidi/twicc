"""Codex command discovery waits for remote plugins before replacing the catalogue."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from asgiref.sync import sync_to_async

from twicc.core.enums import Provider
from twicc.core.models import Command, Project
from twicc.providers.codex.commands_task import _sync_to_database
from twicc.providers.db_writer import _apply_desired_commands_job


pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def catalogue(monkeypatch, tmp_path):
    Project.objects.create(id="skills-project", directory=str(tmp_path))
    state = SimpleNamespace(ready=False, failure=None, remote_enabled=True)
    client = AsyncMock()
    client.__aenter__.return_value = client

    async def request(method, params, *, response_model):
        if method == "plugin/reconcile":
            # Completion, rather than dispatch, makes remote skills available.
            await asyncio.sleep(0)
            if isinstance(state.failure, Exception):
                raise state.failure
            if state.failure:
                return response_model.model_validate({
                    "changedPlugins": [],
                    "failedRemotePluginIds": [],
                    "failedMaterializationRemotePluginIds": [],
                    **state.failure,
                })
            state.ready = True
            return response_model.model_validate({
                "changedPlugins": [],
                "failedRemotePluginIds": [],
                "failedMaterializationRemotePluginIds": [],
            })
        assert method == "skills/list"
        assert params == {"cwds": [str(tmp_path.resolve())], "forceReload": True}
        names = ["local-skill"]
        if state.ready and state.remote_enabled:
            names.append("superpowers:brainstorming")
        return response_model.model_validate({
            "data": [{
                "cwd": str(tmp_path.resolve()),
                "errors": [],
                "skills": [{
                    "name": name,
                    "description": f"Description of {name}",
                    "enabled": True,
                    "scope": "user",
                    "path": str(tmp_path / name / "SKILL.md"),
                } for name in names],
            }],
        })

    client.request.side_effect = request
    monkeypatch.setattr("openai_codex.async_client.AsyncCodexClient", lambda **kwargs: client)
    monkeypatch.setattr("twicc.providers.codex.bin.make_codex_config", AsyncMock())
    # Keep the real database writer operation; replace only its queue transport.
    monkeypatch.setattr(
        "twicc.providers.db_writer.submit_async_job",
        sync_to_async(_apply_desired_commands_job),
    )
    return state


def _seed_remote_command():
    return Command.objects.create(
        provider=Provider.CODEX,
        activation_char="$",
        name="brainstorming",
        plugin_name="superpowers",
        description="Previously discovered remote skill",
    )


def test_sync_includes_remote_skills_after_reconciliation(catalogue):
    stats = asyncio.run(_sync_to_database())

    assert stats == {"created": 2, "updated": 0, "deleted": 0, "unchanged": 0}
    assert list(Command.objects.order_by("name").values_list("name", "plugin_name")) == [
        ("brainstorming", "superpowers"),
        ("local-skill", None),
    ]


@pytest.mark.parametrize("failure", [
    RuntimeError("remote service unavailable"),
    TimeoutError("remote service timeout"),
    {"failedRemotePluginIds": ["superpowers-id"]},
    {"failedMaterializationRemotePluginIds": ["superpowers-id"]},
])
def test_failed_reconciliation_preserves_catalogue(catalogue, failure):
    command = _seed_remote_command()
    catalogue.failure = failure

    with pytest.raises((RuntimeError, TimeoutError)):
        asyncio.run(_sync_to_database())

    command.refresh_from_db()
    assert command.description == "Previously discovered remote skill"
    assert list(Command.objects.values_list("name", "plugin_name")) == [("brainstorming", "superpowers")]


def test_successful_reconciliation_removes_uninstalled_remote_skills(catalogue):
    _seed_remote_command()
    catalogue.remote_enabled = False

    stats = asyncio.run(_sync_to_database())

    assert stats == {"created": 1, "updated": 0, "deleted": 1, "unchanged": 0}
    assert list(Command.objects.values_list("name", "plugin_name")) == [("local-skill", None)]


def test_sync_recovers_on_the_next_cycle(catalogue):
    _seed_remote_command()
    catalogue.failure = {"failedRemotePluginIds": ["superpowers-id"]}
    with pytest.raises(RuntimeError):
        asyncio.run(_sync_to_database())

    catalogue.failure = None
    stats = asyncio.run(_sync_to_database())

    assert stats == {"created": 1, "updated": 1, "deleted": 0, "unchanged": 0}
    assert Command.objects.get(name="brainstorming").description == "Description of superpowers:brainstorming"

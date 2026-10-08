"""New provider addenda teach the main-session inline authoring contract."""

import asyncio
import re
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from twicc.agent.system_prompt import compose_addendum
from twicc.core.enums import Provider
from twicc.core.models import Project
from twicc.core.services.session_creation import create_session_from_payload
from twicc.inline_artifacts.publications import parse_inline_artifact_blocks
from twicc.pending_agent_settings import pop_pending_agent_settings
from twicc.pending_session_attributes import get_pending_session_attributes, pop_pending_session_attributes
from twicc.providers.helpers import AgentSettings


@pytest.fixture(params=[Provider.CLAUDE_CODE, Provider.CODEX])
def provider_addendum(request, db):
    return compose_addendum(
        provider=request.param.value,
        project_id="inline-prompt-project",
        resolved_settings=AgentSettings(),
        session_id="inline-prompt-session" if request.param == Provider.CLAUDE_CODE else None,
    )


@pytest.mark.parametrize("guidance", [
    'Use the same ID and folder for corrections, then insert the tag again.',
    'Use window.twicc.data only when saved data serves the widget.',
    'Do not save every interface change automatically.',
    'Only the main session agent creates and publishes inline artifacts.',
    'Do not delegate inline artifact generation or publication to native subagents.',
    'If you are a native subagent, do not create or publish inline artifacts.',
    'Use distinct IDs for independent widgets.',
    'Do not add a Submit to discussion control.',
])
def test_both_provider_addenda_document_inline_publication(provider_addendum, guidance):
    # These sentences are the approved authoring contract, including native exclusion.
    assert guidance in provider_addendum


def test_examples_publish_single_file_and_folder_with_assets(provider_addendum):
    samples = re.findall(r"```(?:text)?\n(.*?)\n```", provider_addendum, re.DOTALL)
    descriptors = [block.descriptor for sample in samples for block in parse_inline_artifact_blocks(sample)]
    assert {descriptor["src"] for descriptor in descriptors} == {
        "inline-artifacts/calculator/calculator.html", "inline-artifacts/preferences/index.html",
    }
    assert all(descriptor["height"] == 360 for descriptor in descriptors)
    assert 'inline-artifacts/preferences/app.js' in provider_addendum
    assert 'inline-artifacts/preferences/style.css' in provider_addendum
    assert 'double-quoted' in provider_addendum
    assert '[a-z][a-z0-9_-]{0,63}' in provider_addendum
    assert '160–900 CSS pixels' in provider_addendum
    assert '200 characters' in provider_addendum


def test_guidance_separates_optional_data_from_retained_memory(provider_addendum):
    assert 'window.twicc.data.set' in provider_addendum
    assert 'independent iframe memory' in provider_addendum
    assert 'refresh, Reload, code correction, cache eviction, or view teardown' in provider_addendum
    assert 'relative asset paths' in provider_addendum
    assert 'finalized assistant reply' in provider_addendum
    assert 'standalone top-level Markdown block' in provider_addendum


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("provider", [Provider.CLAUDE_CODE, Provider.CODEX])
def test_new_session_creation_freezes_inline_guidance(provider, tmp_path):
    project = Project.objects.create(id="inline-prompt-project", directory=str(tmp_path))
    draft_id = "inline-prompt-draft"
    manager = SimpleNamespace(create_session=AsyncMock(return_value=draft_id))
    try:
        with (
            patch("twicc.core.services.session_creation.ensure_provider_running"),
            patch("twicc.agent.registry.get_agent_manager_registry", return_value=SimpleNamespace(get=lambda p: manager)),
        ):
            result = asyncio.run(create_session_from_payload({
                "session_id": draft_id, "project_id": project.id, "provider": provider.value,
                "text": "Create a calculator", "layout": {},
            }))
        assert result.success, result.errors
        frozen = get_pending_session_attributes(draft_id).system_prompt_addendum
        assert 'Only the main session agent creates and publishes inline artifacts.' in frozen
        assert '<twicc:inline-artifact id="calculator"' in frozen
    finally:
        pop_pending_agent_settings(draft_id)
        pop_pending_session_attributes(draft_id)

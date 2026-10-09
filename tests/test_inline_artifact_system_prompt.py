"""The authoring skill holds the artifact contract; the addendum only routes to it."""

import asyncio
import re
from pathlib import Path
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

SKILL = (
    Path(__file__).resolve().parents[1] / "src/twicc/agent/plugin/twicc/skills/twicc-artifact-authoring/SKILL.md"
).read_text()


@pytest.fixture(params=[Provider.CLAUDE_CODE, Provider.CODEX])
def provider_addendum(request, db):
    return compose_addendum(
        provider=request.param.value,
        project_id="inline-prompt-project",
        resolved_settings=AgentSettings(),
        session_id="inline-prompt-session" if request.param == Provider.CLAUDE_CODE else None,
    )


def test_both_provider_addenda_route_artifact_authoring_to_the_skill(provider_addendum):
    # The addendum keeps what and when, the delegation ban, and the mandatory skill load.
    assert "you MUST load the\n`twicc-artifact-authoring` skill" in provider_addendum
    assert "never delegate them to native\nsubagents" in provider_addendum
    assert "<twicc:inline-artifact" not in provider_addendum
    assert "window.twicc.data" not in provider_addendum


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
def test_skill_documents_inline_publication(guidance):
    # These sentences are the approved authoring contract, including native exclusion.
    assert guidance in SKILL


def test_examples_publish_single_file_and_folder_with_assets():
    samples = re.findall(r"```(?:text)?\n(.*?)\n```", SKILL, re.DOTALL)
    descriptors = [block.descriptor for sample in samples for block in parse_inline_artifact_blocks(sample)]
    assert {descriptor["src"] for descriptor in descriptors} == {
        "inline-artifacts/calculator/calculator.html", "inline-artifacts/preferences/index.html",
    }
    assert all(descriptor["height"] == 360 for descriptor in descriptors)
    assert 'inline-artifacts/preferences/app.js' in SKILL
    assert 'inline-artifacts/preferences/style.css' in SKILL
    assert 'double-quoted' in SKILL
    assert '[a-z][a-z0-9_-]{0,63}' in SKILL
    assert '160–900 CSS pixels' in SKILL
    assert '200 characters' in SKILL


def test_guidance_separates_optional_data_from_retained_memory():
    assert 'window.twicc.data.set' in SKILL
    assert 'independent iframe memory' in SKILL
    assert 'refresh, Reload, code correction, cache eviction, or view teardown' in SKILL
    assert 'relative asset paths' in SKILL
    assert 'finalized assistant reply' in SKILL
    assert 'standalone top-level Markdown block' in SKILL


def test_skill_documents_light_dark_and_inline_transparency():
    assert ':root { color-scheme: light dark; }' in SKILL
    assert 'data-twicc-display="inline"' in SKILL
    assert ':root[data-twicc-display="inline"] { background: transparent; }' in SKILL


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
        assert '`twicc-artifact-authoring` skill' in frozen
        assert 'Only the main\nsession agent publishes inline HTML artifacts' in frozen
    finally:
        pop_pending_agent_settings(draft_id)
        pop_pending_session_attributes(draft_id)

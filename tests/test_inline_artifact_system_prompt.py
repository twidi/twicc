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

ROOT = Path(__file__).resolve().parents[1]
SKILL_DIR = ROOT / "src/twicc/agent/plugin/twicc/skills/twicc-artifact-authoring"
INDEX = (SKILL_DIR / "SKILL.md").read_text()
TOPICS = {name: (SKILL_DIR / name).read_text() for name in ("html.md", "design.md", "inline.md")}
# The whole contract, index and topic files together.
SKILL = "\n".join([INDEX, *TOPICS.values()])
THEME = (ROOT / "frontend/src/artifact-theme/theme.css").read_text()
KIT = (ROOT / "frontend/src/artifact-theme/kit.css").read_text()


def _expand(name):
    """Expand the skill's `{a|b}` shorthand into every literal token name."""
    match = re.search(r"\{([^}]*)\}", name)
    if not match:
        return [name]
    return [n for option in match.group(1).split("|")
            for n in _expand(name[:match.start()] + option + name[match.end():])]


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
    samples = re.findall(r"```text\n(.*?)\n```", SKILL, re.DOTALL)
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


def test_every_token_the_skill_names_exists_in_the_theme():
    # The --twicc-* names are a public contract: the skill and theme.css must agree both ways.
    documented = {n for raw in re.findall(r"--twicc-[a-z0-9{}|-]+", SKILL) for n in _expand(raw)}
    defined = set(re.findall(r"^\s*(--twicc-[a-z0-9-]+):", THEME, re.MULTILINE))
    # Set at runtime by the shim from the user's settings (appPreferences.js), not by theme.css.
    runtime = {"--twicc-root-font-size"}
    assert documented == defined | runtime


def test_every_kit_class_the_skill_names_exists_in_the_kit():
    look = TOPICS["design.md"].split("## TwiCC look", 1)[1].split("\n## ", 1)[0]
    classes = set(re.findall(r"`(twicc-[a-z]+)`", look))
    classes |= {name for value in re.findall(r'class="([^"]+)"', look) for name in value.split()}
    assert classes
    for name in classes:
        assert f".{name}" in KIT, name
    assert "/_twicc/artifact-theme/kit.css" in SKILL


def test_skill_documents_user_preferences():
    assert "--twicc-root-font-size" in SKILL and "rem" in SKILL
    assert "data-twicc-reduce-motion" in SKILL and "data-twicc-reduce-effects" in SKILL
    assert "var(--twicc-root-font-size, 100%)" in KIT


def test_kit_root_font_size_is_only_the_user_setting():
    # A second font-size on the root (e.g. a rem token) resolves against the browser default
    # and silently overrides the user's setting.
    root_rule = re.search(r":where\(:root\) \{(.*?)\}", KIT, re.DOTALL).group(1)
    assert re.findall(r"^\s*font-size:\s*(.+?);", root_rule, re.MULTILINE) == ["var(--twicc-root-font-size, 100%)"]


def test_index_routes_html_work_to_the_topic_files():
    # An agent that only writes an image or Markdown reads the index alone.
    for name in TOPICS:
        assert f"`{name}`" in INDEX
    assert "ALWAYS READ THE TOPIC'S FILE" in INDEX
    for detail in ("<twicc:inline-artifact", "--twicc-accent", "window.twicc.data.set", "data-twicc-display"):
        assert detail not in INDEX, detail
    assert "<twicc:inline-artifact" in TOPICS["inline.md"]
    assert "--twicc-canvas" in TOPICS["design.md"]
    assert "window.twicc.data.set" in TOPICS["html.md"]


def test_addendum_encourages_inline_artifacts_without_a_quota(provider_addendum):
    assert "use\nthem readily, on your own initiative, without waiting for the user to ask." in provider_addendum
    assert "There is no quota" in provider_addendum
    assert "not when a Markdown reply does the job" not in provider_addendum


def test_index_says_where_to_publish():
    choice = INDEX.split("## Inline or Artifacts tab", 1)[1].split("\n## ", 1)[0]
    assert "When unsure, publish **inline**" in choice
    assert "genuinely helps" not in INDEX

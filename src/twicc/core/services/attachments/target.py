"""Resolution of the target a composer attachment plan is made for (spec §6.2).

The target carries what the planner needs and nothing else: provider, hybrid and ephemeral flags,
the resolved model, whether the Claude CLI runs with a 1M context, and the Claude platform.

- ``context_1m``: true only when the requested effective settings **and**, if a live agent will
  receive the message, its current settings are both 1M-capable and set to 1M. An idle settings
  change applies only at the next user turn, so either side can be the one the CLI runs with.
- ``platform``: the backend purges every ``CLAUDE_CODE*`` variable from its own environment at
  startup, so the CLI it launches gets a platform switch only from Claude's settings files. The
  inherited environment is never read here. ``third_party`` when one of :data:`PLATFORM_FLAGS` is
  true, by the CLI's own boolean parsing, in the ``env`` block of the user settings (in the
  resolved Claude config dir) or, only for a trusted project (an untrusted one loads the user
  settings only), of the project's ``.claude/settings.json`` / ``.claude/settings.local.json``.

``hybrid`` is decided by the caller (``Session.hybrid`` or the creation payload).
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING

import orjson
from asgiref.sync import sync_to_async

from twicc.core.services.attachments.types import PlanTarget
from twicc.providers.helpers import AgentSettings

if TYPE_CHECKING:
    from twicc.agent.base_agent import BaseAgent

logger = logging.getLogger(__name__)

__all__ = [
    "PLATFORM_FIRST_PARTY",
    "PLATFORM_FLAGS",
    "PLATFORM_THIRD_PARTY",
    "resolve_existing_session_plan_target",
    "resolve_plan_target",
    "resolve_session_hybrid",
]

PLATFORM_FIRST_PARTY = "first_party"
PLATFORM_THIRD_PARTY = "third_party"

PROVIDER_CLAUDE_CODE = "claude_code"
CONTEXT_1M = 1_000_000

# The variables the Claude CLI reads to leave the first-party API (spec §6.2).
PLATFORM_FLAGS = (
    "CLAUDE_CODE_USE_BEDROCK",
    "CLAUDE_CODE_USE_VERTEX",
    "CLAUDE_CODE_USE_FOUNDRY",
    "CLAUDE_CODE_USE_ANTHROPIC_AWS",
    "CLAUDE_CODE_USE_ANTHROPIC_GOOGLE_CLOUD",
    "CLAUDE_CODE_USE_MANTLE",
)
# The CLI's boolean parsing: the value, trimmed and lower-cased, is one of these.
_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})
_PROJECT_SETTINGS_FILES = ("settings.json", "settings.local.json")


def _is_true(value: object) -> bool:
    """The CLI's boolean parsing of one ``env`` value.

    Settings values are coerced to strings by the CLI, so a JSON ``true`` or ``1`` counts like
    ``"true"`` / ``"1"``. Any other type is never true.
    """
    if isinstance(value, bool):
        value = "true" if value else "false"
    elif isinstance(value, int):
        value = str(value)
    elif not isinstance(value, str):
        return False
    return value.strip().lower() in _TRUE_VALUES


def _settings_env(path: Path) -> dict:
    """The ``env`` block of a Claude settings file; empty when absent or malformed."""
    try:
        data = orjson.loads(path.read_bytes())
    except FileNotFoundError:
        return {}
    except (OSError, orjson.JSONDecodeError):
        logger.debug("Unreadable Claude settings file %s", path, exc_info=True)
        return {}
    env = data.get("env") if isinstance(data, dict) else None
    return env if isinstance(env, dict) else {}


def _project_is_trusted(directory: str) -> bool:
    """True when the project at *directory* resolves trusted (sync, DB reads).

    Unknown trust and a directory with no project count as untrusted, as at agent launch.
    """
    from twicc.trust import effective_trust, load_trust_rows

    if not directory:
        return False
    candidates = {directory, os.path.realpath(directory)}
    rows = load_trust_rows()
    row = next((r for r in rows if r.directory in candidates), None)
    return row is not None and effective_trust(row, rows).state is True


def _claude_platform(directory: str) -> str:
    """``third_party`` when a settings file the CLI loads enables one of :data:`PLATFORM_FLAGS`."""
    from twicc.provider_homes import claude_config_dir

    sources = [claude_config_dir().path / "settings.json"]
    if _project_is_trusted(directory):
        sources += [Path(directory) / ".claude" / name for name in _PROJECT_SETTINGS_FILES]
    for path in sources:
        env = _settings_env(path)
        if any(_is_true(env.get(flag)) for flag in PLATFORM_FLAGS):
            return PLATFORM_THIRD_PARTY
    return PLATFORM_FIRST_PARTY


def _runs_with_1m(helpers, settings: AgentSettings) -> bool:
    return settings.context_max == CONTEXT_1M and helpers.selected_model_supports_1m(settings.selected_model)


def _resolve(provider: str, effective_settings: AgentSettings, directory: str, hybrid: bool, ephemeral: bool,
             live_settings: AgentSettings | None) -> PlanTarget:
    from twicc.providers.helpers import get_provider_helpers

    helpers = get_provider_helpers(provider)
    settings = helpers.enforce_agent_settings_consistency(helpers.resolve_agent_settings(effective_settings))
    model = settings.selected_model or ""
    if provider != PROVIDER_CLAUDE_CODE:
        return PlanTarget(provider, False, ephemeral, model, False, PLATFORM_FIRST_PARTY)
    context_1m = _runs_with_1m(helpers, settings) and (live_settings is None or _runs_with_1m(helpers, live_settings))
    return PlanTarget(provider, hybrid, ephemeral, model, context_1m, _claude_platform(directory))


async def resolve_plan_target(
    *,
    provider: str,
    effective_settings: AgentSettings,
    directory: str,
    hybrid: bool,
    ephemeral: bool,
    live_agent: BaseAgent | None,
) -> PlanTarget:
    """The :class:`PlanTarget` of a message for *provider* in the project at *directory*.

    *effective_settings* are the requested settings; they are resolved and enforced again here
    (idempotent), so the model and context are those the agent will be built with. *live_agent*
    is the running agent (a ``BaseAgent``) that will receive the message, if any; its current
    ``agent_settings`` also gate ``context_1m``. *hybrid* (Claude only) and *ephemeral* come from
    the caller. Reads the settings files and the project trust off the event loop.
    """
    live_settings = live_agent.agent_settings if live_agent is not None else None
    return await sync_to_async(_resolve)(
        str(provider), effective_settings, directory, bool(hybrid), bool(ephemeral), live_settings
    )


async def _read_session_hybrid(session_id: str) -> bool:
    """``Session.hybrid`` from the database (``False`` without a row)."""
    from twicc.core.models import Session

    hybrid = await sync_to_async(
        lambda: Session.objects.filter(id=session_id).values_list("hybrid", flat=True).first()
    )()
    return bool(hybrid)


async def resolve_session_hybrid(session_id: str) -> bool:
    """Whether a message to the existing *session_id* targets the hybrid CLI.

    ``Session.hybrid`` only. The caller holds the session's send lane, and a switch to hybrid
    writes the flag inside that lane, so the flag read here is the one the manager's agent
    factory reads when it builds the agent that delivers the message.
    """
    return await _read_session_hybrid(session_id)


async def resolve_existing_session_plan_target(
    *,
    session_id: str,
    provider: str,
    effective_settings: AgentSettings,
    directory: str,
    ephemeral: bool,
    live_agent: BaseAgent | None,
) -> PlanTarget:
    """The composer attachment ``PlanTarget`` of a message to the existing *session_id*.

    ``hybrid`` comes from :func:`resolve_session_hybrid`. Shared by the WS handler and the
    send service (phase 2 design §4.5.4), both inside the session's send lane. See
    :func:`resolve_plan_target` for the other fields.
    """
    return await resolve_plan_target(
        provider=provider,
        effective_settings=effective_settings,
        directory=directory,
        hybrid=await resolve_session_hybrid(session_id),
        ephemeral=ephemeral,
        live_agent=live_agent,
    )

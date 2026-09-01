"""
Daily async task that detects retired model versions and auto-upgrades.

One loop per provider — each orchestrator starts its own, the same way
``background_compute_task`` is a shared module driven per provider. A
provider that ships no ``retirement_date`` simply never finds anything.

When a retirement is detected:
1. Global default is updated in synced settings (if affected)
2. Active processes are updated via the existing apply_live_settings machinery
   → No database mass-update of the other sessions (corrected at render/send time)

Nothing is pushed to the frontends. Every registry entry ships its
``retirement_date`` in the bootstrap payload, so a frontend decides on its own
that a model is retired: it drops it from the pickers and resolves any stored
value to the replacement. A notification would carry no information the
frontend does not already hold. The global-default change of step 1 travels
through its own ``synced_settings_updated`` broadcast.
"""

import asyncio
import logging
from datetime import date

from twicc.core.enums import Provider

logger = logging.getLogger(__name__)

RETIREMENT_CHECK_INTERVAL = 24 * 60 * 60  # 24 hours

# One stop event per provider: the two loops are independent, so a shutdown
# of one must not stop the other.
_retirement_stop_events: dict[Provider, asyncio.Event] = {}


def get_retirement_stop_event(provider: Provider) -> asyncio.Event:
    event = _retirement_stop_events.get(provider)
    if event is None:
        event = asyncio.Event()
        _retirement_stop_events[provider] = event
    return event


def stop_model_retirement_task(provider: Provider) -> None:
    event = _retirement_stop_events.get(provider)
    if event is not None:
        event.set()


async def start_model_retirement_task(provider: Provider) -> None:
    """Run ``provider``'s retirement check loop: once at startup, then every 24 hours."""
    stop_event = get_retirement_stop_event(provider)
    # Reset for hot-restart support: clear the stop event so a relaunched
    # task isn't killed by a stale set() left by the prior shutdown.
    stop_event.clear()

    _log_upcoming_retirements(provider)

    # Initial check on startup
    try:
        await _check_and_retire(provider)
    except Exception:
        logger.exception("Error in initial retirement check for %s", provider.value)

    while not stop_event.is_set():
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=RETIREMENT_CHECK_INTERVAL)
            break  # stop_event was set
        except TimeoutError:
            pass  # Time to check again

        try:
            await _check_and_retire(provider)
        except Exception:
            logger.exception("Error in retirement check cycle for %s", provider.value)


async def _check_and_retire(provider: Provider) -> None:
    """Perform one retirement check cycle for ``provider``."""
    from channels.layers import get_channel_layer

    from twicc.providers.helpers import get_provider_helpers
    from twicc.synced_settings import SYNCED_SETTINGS_DEFAULTS

    helpers = get_provider_helpers(provider)

    # Identify all retired versions. The identifier is built by the provider's
    # own ``selected_model_value`` (bare alias for a family's latest, versioned
    # alias otherwise) — a retiring model may well be its family's latest, as
    # Codex's single-entry ``gpt-mini`` family is.
    retired_models: dict[str, str] = {}  # old selected_model → new selected_model
    for mv in helpers.MODEL_VERSIONS:
        if mv.retirement_date is None:
            continue
        selected = helpers.selected_model_value(mv)
        if helpers.is_model_retired(selected):
            target = helpers.resolve_to_available_model(selected)
            if target != selected:
                retired_models[selected] = target

    if not retired_models:
        return

    logger.info("Retired %s models detected: %s", provider.value, retired_models)

    # 1. Update global default if affected
    settings_changed = False
    from twicc.synced_settings import (
        _settings_lock,
        prepare_settings_for_client,
        read_synced_settings,
        write_synced_settings,
    )

    # Each provider names its own default-model key in the synced settings
    # (``claudeCodeDefaultModel``, ``codexDefaultModel``, …).
    default_model_key = helpers.AGENT_SETTINGS_FIELDS_MAPPING.get("selected_model")

    if default_model_key:
        with _settings_lock:
            current = read_synced_settings()
            default_model = current.get(
                default_model_key, SYNCED_SETTINGS_DEFAULTS.get(default_model_key)
            )
            if default_model in retired_models:
                current[default_model_key] = retired_models[default_model]
                current["_version"] = current.get("_version", 0) + 1
                write_synced_settings(current)
                settings_changed = True
                logger.info(
                    "Updated global default model (%s): %s → %s",
                    default_model_key,
                    default_model,
                    retired_models[default_model],
                )

    # Broadcast global settings update if changed
    if settings_changed:
        channel_layer = get_channel_layer()
        clean, version = prepare_settings_for_client(read_synced_settings())
        await channel_layer.group_send(
            "updates",
            {
                "type": "broadcast",
                "data": {
                    "type": "synced_settings_updated",
                    "settings": clean,
                    "version": version,
                },
            },
        )

    # 2. Update active processes (running sessions)
    # Model change is an "idle" setting: apply_live_settings() calls the SDK's
    # model setter — no process restart needed.
    # - USER_TURN: applied immediately
    # - ASSISTANT_TURN: apply_live_settings skips idle changes, so we also
    #   update the session DB row; _apply_pending_settings will pick it up
    #   at the next USER_TURN transition.
    from twicc.agent.registry import get_agent_manager_registry
    from twicc.providers.db_writer import _RetireSessionsJob, submit_async_job

    manager = get_agent_manager_registry().get(provider)
    # NOTE: the managers don't expose a public accessor returning the agent
    # objects themselves (``get_active_agents`` returns ``AgentInfo``), so we
    # iterate ``_agents`` — the attribute lives on ``BaseAgentManager``, so it
    # is the same for every provider. Collect updates first, then apply them as
    # a single DB-writer-routed batch so this task never races the DB writer on
    # the SQLite write lock.
    updates_per_session: dict[str, dict[str, object]] = {}
    pending_live_applies: list[tuple[object, object, str, str]] = []
    for process in list(manager._agents.values()):
        process_settings = process.agent_settings
        if process_settings.selected_model not in retired_models:
            continue
        old_model = process_settings.selected_model
        new_model = retired_models[old_model]
        # Substitute the new model, then let the helpers cap context_max /
        # demote effort if the new model has lower capabilities.
        adjusted_settings = helpers.enforce_agent_settings_consistency(
            process_settings._replace(selected_model=new_model),
        )
        # Build the DB updates so _apply_pending_settings picks them up if in
        # ASSISTANT_TURN. The actual UPDATE runs in the DB writer (atomic), see
        # below.
        session_updates: dict[str, object] = {"selected_model": new_model}
        if adjusted_settings.effort != process_settings.effort:
            session_updates["effort"] = adjusted_settings.effort
        updates_per_session[process.session_id] = session_updates
        pending_live_applies.append((process, adjusted_settings, old_model, new_model))

    # Apply DB updates as a single DB-writer-routed batch. The DB writer wraps
    # every session UPDATE in one transaction.atomic. On failure we skip the
    # live applies — without the DB row updated, the SDK-side model change
    # would be reverted by the next _apply_pending_settings pass.
    if updates_per_session:
        future = asyncio.get_running_loop().create_future()
        try:
            await submit_async_job(_RetireSessionsJob(
                provider=provider,
                updates=updates_per_session,
                future=future,
            ))
        except Exception:
            logger.exception(
                "Failed to apply retirement DB updates via the DB writer "
                "— skipping live applies; next cycle will retry"
            )
            pending_live_applies = []

    for process, adjusted_settings, old_model, new_model in pending_live_applies:
        try:
            await process.apply_live_settings(adjusted_settings)
            logger.info(
                "Upgraded active process %s: %s → %s",
                process.session_id, old_model, new_model,
            )
        except Exception:
            logger.exception(
                "Failed to apply retirement upgrade to process %s", process.session_id
            )

    # Deliberately no frontend broadcast — see the module docstring. The
    # frontends already carry every entry's ``retirement_date`` and derive the
    # retirement (and its replacement) themselves.


def _log_upcoming_retirements(provider: Provider) -> None:
    """Log a summary of model versions and upcoming retirements at startup."""
    from twicc.providers.helpers import get_provider_helpers

    today = date.today()
    for mv in get_provider_helpers(provider).MODEL_VERSIONS:
        if mv.retirement_date is None:
            continue
        days_left = (mv.retirement_date - today).days
        if days_left <= 0:
            logger.warning("Model %s-%s is RETIRED (since %s)", mv.model, mv.version, mv.retirement_date)
        elif days_left <= 30:
            logger.warning("Model %s-%s retires in %d days (%s)", mv.model, mv.version, days_left, mv.retirement_date)
        else:
            logger.info("Model %s-%s retires on %s (%d days)", mv.model, mv.version, mv.retirement_date, days_left)

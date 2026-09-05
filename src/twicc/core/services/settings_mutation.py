"""Shared synced-settings merge service.

The single rich write path for ``settings.json`` — previously inlined in
``asgi.py::_handle_update_synced_settings``, now a reusable async service so the
WS handler **and** the drop-request CLI path share identical semantics
(optimistic concurrency, per-provider consistency, ``disabledProviders`` safety,
``defaultProvider`` rebind, ``_version`` bump, and the orchestrator transitions).

The merge body (``_merge_and_write``) is sync and runs under ``_settings_lock``;
the async ``update_synced_settings`` wraps it and, when ``broadcast=True``,
applies the orchestrator transitions and broadcasts ``synced_settings_updated``
to every client.

``broadcast=False`` keeps callers (and tests) off the channel layer and the
orchestrator registry entirely — the merge itself never touches them.
"""

import asyncio
import logging
from typing import NamedTuple

from asgiref.sync import sync_to_async
from channels.layers import get_channel_layer

from twicc.agent.registry import get_agent_manager_registry
from twicc.core.enums import Provider
from twicc.core.services.public_origin import (
    ORIGIN_CONFLICT_AMBIGUOUS,
    ORIGIN_CONFLICT_SHARE_EXTERNAL,
    ORIGIN_CONFLICT_SHARE_PEER,
)
from twicc.providers.helpers import get_provider_helpers_registry
from twicc.synced_settings import (
    _settings_lock,
    prepare_settings_for_client,
    read_synced_settings,
    write_synced_settings,
)

logger = logging.getLogger(__name__)


class SettingsDropError(NamedTuple):
    field: str
    code: str
    message: str


class SettingsUpdateResult(NamedTuple):
    status: str  # "accepted" | "rejected"
    version: int
    corrections: dict
    clean: dict  # full clean settings (resync / CLI display)
    errors: tuple[SettingsDropError, ...] = ()


class SettingsDropResult(NamedTuple):
    success: bool
    errors: tuple = ()
    status_extra: dict = {}  # generic passthrough → status file; never mutate in place


_ORIGIN_STRUCTURAL_MESSAGE = "Enter a hostname or an HTTP(S) origin without a path, query, or fragment."
_ORIGIN_ERROR_MESSAGES = {
    ORIGIN_CONFLICT_SHARE_EXTERNAL: "The Share host must use a different hostname from the External address.",
    ORIGIN_CONFLICT_SHARE_PEER: "The Share host must use a different hostname from the Peer address.",
    ORIGIN_CONFLICT_AMBIGUOUS: "The Peer and External addresses must be the same origin or use different authorities.",
}


def _same_json_value(left, right) -> bool:
    """Return true when decoded JSON values have the same JSON type and value."""
    left_is_number = isinstance(left, (int, float)) and not isinstance(left, bool)
    right_is_number = isinstance(right, (int, float)) and not isinstance(right, bool)
    if left_is_number or right_is_number:
        return left_is_number and right_is_number and left == right
    if type(left) is not type(right):
        return False
    if isinstance(left, list):
        return len(left) == len(right) and all(
            _same_json_value(left_item, right_item)
            for left_item, right_item in zip(left, right, strict=True)
        )
    if isinstance(left, dict):
        return left.keys() == right.keys() and all(
            _same_json_value(left[key], right[key]) for key in left
        )
    return left == right


def _is_provider(value: str) -> bool:
    try:
        Provider(value)
    except ValueError:
        return False
    return True


def _merge_and_write(patch: dict, base_version: int | None) -> dict:
    """Merge ``patch`` into the current settings under the settings lock.

    Returns a result dict with ``status`` and ``version``; on ``"accepted"``
    it also carries ``to_start`` / ``to_stop`` (orchestrator deltas) and
    ``corrections``; on ``"rejected"`` it carries ``clean`` (the resync blob).

    Copied verbatim from ``asgi.py::_handle_update_synced_settings`` (the
    ``_merge_and_write`` closure), with ``synced_settings`` renamed to ``patch``.
    The running-set delta logic (``old_key_present`` / ``old_running`` /
    ``new_running``) is preserved exactly — NOT a diff of ``disabledProviders``.
    """
    with _settings_lock:
        existing_settings = read_synced_settings()
        current_version = existing_settings.get("_version", 0)
        previous_peer_base_url = existing_settings.get("peerBaseUrl", "")

        # Reject stale writes (accept if baseVersion is None — safety for rolling upgrades)
        if base_version is not None and base_version < current_version:
            clean, ver = prepare_settings_for_client(existing_settings)
            return {"status": "rejected", "clean": clean, "version": ver}

        from twicc.core.services.public_origin import (
            PUBLIC_ORIGIN_SETTING_KEYS,
            normalize_public_origin,
            validate_origin_settings,
        )

        normalized_patch = dict(patch)
        corrections: dict = {}
        errors: list[SettingsDropError] = []
        changed_origin_fields = {
            key for key in PUBLIC_ORIGIN_SETTING_KEYS
            if key in patch and not _same_json_value(patch[key], existing_settings.get(key))
        }
        # Fields whose changed value is a string, so ``validate_origin_settings``
        # can own their structural + relationship verdict.
        typed_origin_fields = set(changed_origin_fields)
        for key in PUBLIC_ORIGIN_SETTING_KEYS:
            if key not in changed_origin_fields:
                continue
            value = patch[key]
            if not isinstance(value, str):
                # ``normalize_public_origin`` maps ``None`` to the valid empty
                # result because settings READS need that. A write must not: a
                # JSON ``null`` would silently clear the address. Reject here,
                # BEFORE normalizing, and keep the field out of the validated
                # set so the same error is never reported twice.
                errors.append(SettingsDropError(key, "invalid_origin_type", _ORIGIN_STRUCTURAL_MESSAGE))
                typed_origin_fields.discard(key)
                continue
            result = normalize_public_origin(value)
            if not result.error:
                normalized_patch[key] = result.value
            if not result.error and result.value != value:
                corrections[key] = result.value
        if typed_origin_fields:
            merged = {
                key: normalized_patch.get(key, existing_settings.get(key, ""))
                for key in PUBLIC_ORIGIN_SETTING_KEYS
            }
            for field_error in validate_origin_settings(
                merged["publicBaseUrl"], merged["shareBaseUrl"], merged["peerBaseUrl"],
                changed_fields=typed_origin_fields,
            ):
                errors.append(SettingsDropError(
                    field_error.field,
                    field_error.code,
                    _ORIGIN_ERROR_MESSAGES.get(field_error.code, _ORIGIN_STRUCTURAL_MESSAGE),
                ))
        if set(patch) & {"mcpBaseUrl", "externalMcpEnabled", "publicBaseUrl", "shareBaseUrl", "peerBaseUrl"}:
            merged_mcp = {**existing_settings, **normalized_patch}
            raw_mcp = merged_mcp.get("mcpBaseUrl", "")
            parsed_mcp = normalize_public_origin(raw_mcp)
            if ("externalMcpEnabled" in patch and type(patch["externalMcpEnabled"]) is not bool):
                errors.append(SettingsDropError("externalMcpEnabled", "invalid_type", "Expected a boolean."))
            if "mcpBaseUrl" in patch:
                if (
                    not isinstance(raw_mcp, str) or parsed_mcp.error
                    or (parsed_mcp.value and parsed_mcp.scheme != "https")
                    or parsed_mcp.hostname in ("localhost", "127.0.0.1", "::1")
                ):
                    errors.append(SettingsDropError("mcpBaseUrl", "invalid_origin", "Enter a dedicated HTTPS origin."))
                else:
                    normalized_patch["mcpBaseUrl"] = parsed_mcp.value
            if merged_mcp.get("externalMcpEnabled") and not parsed_mcp.value:
                errors.append(SettingsDropError("mcpBaseUrl", "required", "A dedicated MCP origin is required."))
            for key in ("publicBaseUrl", "shareBaseUrl", "peerBaseUrl"):
                other = normalize_public_origin(merged_mcp.get(key, ""))
                if parsed_mcp.value and other.value and other.hostname == parsed_mcp.hostname:
                    errors.append(SettingsDropError("mcpBaseUrl", "origin_conflict", "MCP must use a separate hostname."))
        if errors:
            clean, version = prepare_settings_for_client(existing_settings)
            return {
                "status": "rejected",
                "clean": clean,
                "version": version,
                "errors": tuple(errors),
            }

        # Capture both the previous disabled set AND whether the key
        # was physically present in the file. The latter distinguishes
        # "running everything" from "running nothing because no
        # initial choice has been made yet" — they are semantically
        # different and must produce different orchestrator deltas
        # (see the return block below).
        old_key_present = "disabledProviders" in existing_settings
        old_disabled = set(existing_settings.get("disabledProviders") or [])

        # Accepted — merge, then enforce per-provider consistency rules.
        existing_settings.update(normalized_patch)

        # Let every provider enforce its own rules on the merged dict.
        # ``patch`` is the subset the client just sent, so
        # each provider can short-circuit when none of its keys changed.
        get_provider_helpers_registry().enforce_synced_settings_consistency(
            existing_settings, normalized_patch,
        )

        # Self-healing: refuse to disable a provider that still has live agents.
        new_disabled_raw = existing_settings.get("disabledProviders")
        if isinstance(new_disabled_raw, list):
            new_disabled = set(new_disabled_raw)
            just_disabled = new_disabled - old_disabled
            registry = get_agent_manager_registry()
            refused: set[str] = set()
            for value in just_disabled:
                try:
                    provider = Provider(value)
                except ValueError:
                    continue
                try:
                    manager = registry.get(provider)
                except KeyError:
                    continue
                if manager.get_active_agents():
                    refused.add(value)
            if refused:
                # new_disabled is the post-correction value (refused entries removed).
                new_disabled -= refused
                existing_settings["disabledProviders"] = sorted(new_disabled)
                corrections["disabledProviders"] = sorted(new_disabled)

        # Transition guard: refuse toggles for providers currently in
        # transient states (STARTING / STOPPING). The frontend greys
        # the switch during these windows but a race (e.g. double
        # click before the WS broadcast lands) must not be allowed
        # to corrupt the orchestrator's state machine. We only
        # honour the intent when the state is settled:
        # - disable allowed only from RUNNING (-> stopping -> stopped)
        # - enable allowed only from STOPPED (-> starting -> running)
        from twicc.providers.state import ProviderState, get_provider_state
        final_disabled_set = set(existing_settings.get("disabledProviders") or [])
        just_disabled_now = final_disabled_set - old_disabled
        just_enabled_now = old_disabled - final_disabled_set
        transition_changed = False
        for value in just_disabled_now:
            try:
                provider = Provider(value)
            except ValueError:
                continue
            if get_provider_state(provider) != ProviderState.RUNNING:
                final_disabled_set.discard(value)  # revert: keep enabled
                transition_changed = True
        for value in just_enabled_now:
            try:
                provider = Provider(value)
            except ValueError:
                continue
            if get_provider_state(provider) != ProviderState.STOPPED:
                final_disabled_set.add(value)  # revert: keep disabled
                transition_changed = True
        if transition_changed:
            new_list = sorted(final_disabled_set)
            existing_settings["disabledProviders"] = new_list
            corrections["disabledProviders"] = new_list

        # Default-provider rebind: if the current default is no longer enabled,
        # pick the first enabled provider in Provider enum order.
        registered = {p for p, _ in get_provider_helpers_registry().items()}
        final_disabled_set = set(existing_settings.get("disabledProviders") or [])
        enabled_after = {p.value for p in registered if p.value not in final_disabled_set}
        current_default = existing_settings.get("defaultProvider")
        if enabled_after and current_default not in enabled_after:
            new_default = next(p.value for p in Provider if p.value in enabled_after)
            existing_settings["defaultProvider"] = new_default
            corrections["defaultProvider"] = new_default

        existing_settings["_version"] = current_version + 1
        write_synced_settings(existing_settings)

        # Compute the orchestrator transitions on "what was running"
        # vs "what should run" — NOT on the diff of `disabledProviders`.
        # When the key was previously absent, `start_all` had run with
        # `get_enabled_providers() == set()`, so nothing was started;
        # naïvely diffing the disabled sets would then yield an empty
        # `to_start` (set() - set()) and leave everything stopped after
        # the first dialog validation.
        registered_values = {p.value for p, _ in get_provider_helpers_registry().items()}
        old_running = (registered_values - old_disabled) if old_key_present else set()
        new_running = registered_values - final_disabled_set
        return {
            "status": "accepted",
            "version": current_version + 1,
            "to_start": new_running - old_running,
            "to_stop": old_running - new_running,
            "corrections": corrections,
            "previous_peer_base_url": previous_peer_base_url,
        }


async def _apply_transitions_and_broadcast(patch: dict, result: dict) -> None:
    """Apply the orchestrator transitions, then broadcast to all clients.

    Mirrors the async tail of ``asgi.py::_handle_update_synced_settings`` after
    the merge: the transition broadcasts (``provider_state_changed:starting`` /
    ``:stopping``) must reach clients BEFORE the ``synced_settings_updated``
    broadcast — otherwise the UI flips a toggle to its new state for a frame
    before showing the in-transition spinner. Each transition splits in two:
    the fast half (``begin_*``, just the transition broadcast) is awaited up
    front in one ``gather``; the slow half (``orch.start()`` / ``orch.shutdown()``)
    is scheduled as a fire-and-forget background task.

    A service has no ``self.channel_layer``, so the final broadcast uses
    ``get_channel_layer()`` directly — same group + envelope as asgi.py and
    ``workspaces.py``.
    """
    from twicc.orchestrator import get_orchestrator_registry

    orchestrators = get_orchestrator_registry()
    to_stop_providers = [Provider(v) for v in result["to_stop"] if _is_provider(v)]
    to_start_providers = [Provider(v) for v in result["to_start"] if _is_provider(v)]

    # Broadcast every transition in parallel — these are fast (one
    # ``group_send`` each, no slow body). All broadcasts land in the Channels
    # queues before the ``synced_settings_updated`` broadcast at the bottom.
    await asyncio.gather(
        *(orchestrators.begin_shutdown(p) for p in to_stop_providers),
        *(orchestrators.begin_start(p) for p in to_start_providers),
    )

    # Schedule the slow bodies as fire-and-forget background tasks.
    for p in to_stop_providers:
        orchestrators.schedule_finish_shutdown(p)
    for p in to_start_providers:
        orchestrators.schedule_finish_start(p)

    # Broadcast to all clients, overlaying any server-side corrections so every
    # client converges to the authoritative state.
    broadcast_settings = {**patch, **result["corrections"]}
    channel_layer = get_channel_layer()
    await channel_layer.group_send(
        "updates",
        {
            "type": "broadcast",
            "data": {
                "type": "synced_settings_updated",
                "settings": broadcast_settings,
                "version": result["version"],
            },
        },
    )


async def update_synced_settings(
    patch: dict, *, base_version: int | None = None, broadcast: bool = True,
) -> SettingsUpdateResult:
    """Merge ``patch`` into the synced settings and (optionally) broadcast.

    The optimistic-concurrency check runs only when ``base_version is not None``;
    the CLI passes ``None`` (last-write-wins, like the WS "old client" branch).
    When ``broadcast=True`` and the merge is accepted, the orchestrator
    transitions run and ``synced_settings_updated`` is broadcast to all clients.
    """
    previous_mcp = read_synced_settings()
    result = await sync_to_async(_merge_and_write)(patch, base_version)
    if result["status"] == "accepted" and set(patch) & {"mcpBaseUrl", "externalMcpEnabled"}:
        current_mcp = read_synced_settings()
        if (
            (previous_mcp.get("mcpBaseUrl") and previous_mcp.get("mcpBaseUrl") != current_mcp.get("mcpBaseUrl"))
            or (previous_mcp.get("externalMcpEnabled") and not current_mcp.get("externalMcpEnabled"))
        ):
            from twicc.mcp.oauth.storage import revoke_all, write, changed
            await write(revoke_all)
            if broadcast:
                await changed()
    if result["status"] == "accepted" and "peerBaseUrl" in patch:
        from twicc.core.services.peer_mutation import invalidate_peers_for_local_origin
        from twicc.core.services.public_origin import usable_public_origin

        previous_peer_base_url = usable_public_origin(result["previous_peer_base_url"])
        if previous_peer_base_url:
            await invalidate_peers_for_local_origin(
                previous_peer_base_url,
                usable_public_origin(read_synced_settings().get("peerBaseUrl")),
                broadcast_changes=broadcast,
            )
    if result["status"] == "accepted" and broadcast:
        await _apply_transitions_and_broadcast(patch, result)
    clean = result.get("clean")
    if clean is None:
        clean, _ = prepare_settings_for_client(read_synced_settings())
    return SettingsUpdateResult(
        status=result["status"],
        version=result["version"],
        corrections=result.get("corrections", {}),
        clean=clean,
        errors=result.get("errors", ()),
    )


async def notification_test_from_payload(payload: dict) -> SettingsDropResult:
    """Glue for the ``settings:notification_test`` drop-request kind.

    Sends an Apprise test to the target identified by ``payload["id"]``, then
    persists ``tested=True/False`` back onto that target.  A stale-url guard
    re-reads settings before writing: if the URL was changed concurrently the
    patch is skipped for that target (its ``tested`` field stays untouched).

    Returns a :class:`SettingsDropResult` with ``status_extra`` carrying
    ``tested`` (bool) and ``test_results`` (the raw list from
    ``test_notification_urls``) so the CLI can surface the verdict.
    """
    target_id = payload.get("id")
    settings = read_synced_settings()
    targets = settings.get("externalNotificationTargets") or []
    target = next((t for t in targets if t.get("id") == target_id), None)
    if target is None:
        return SettingsDropResult(
            success=False,
            errors=(SettingsDropError("id", "not_found", f"No notification target {target_id!r}."),),
        )
    url = target.get("url", "")
    from twicc.external_notifications import test_notification_urls

    results = await test_notification_urls([url])
    ok = bool(results and results[0].get("ok"))  # result dicts use key "ok"
    # Stale-url guard: re-read; only persist tested if the url is unchanged.
    patch_targets = [
        {**t, "tested": ok} if (t.get("id") == target_id and t.get("url") == url) else t
        for t in (read_synced_settings().get("externalNotificationTargets") or [])
    ]
    await update_synced_settings(
        {"externalNotificationTargets": patch_targets},
        broadcast=payload.get("broadcast", True),
    )
    return SettingsDropResult(success=True, status_extra={"tested": ok, "test_results": results})


async def update_synced_settings_from_payload(payload: dict) -> SettingsDropResult:
    """Glue for the ``settings:update`` drop-request kind.

    Extracts ``patch`` and ``broadcast`` from the payload, calls the shared
    service, and wraps the result as a :class:`SettingsDropResult` the watcher
    can serialise.  Any server-side corrections (e.g. ``defaultProvider``
    rebind) are forwarded to the CLI via ``status_extra``.
    """
    patch = payload.get("patch") or {}
    r = await update_synced_settings(
        patch,
        base_version=None,
        broadcast=payload.get("broadcast", True),
    )
    extra = {"corrections": r.corrections} if r.corrections else {}
    return SettingsDropResult(
        success=(r.status == "accepted"),
        errors=r.errors,
        status_extra=extra,
    )

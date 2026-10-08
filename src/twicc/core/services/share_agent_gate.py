"""Layer-1 shape contract for agent share mutations (§7.1/§7.2).

The legitimate producer of an agent payload is the ``twicc share`` CLI —
directly, or through MCP, which renders its calls from the same Typer
signature. The contract is therefore stated over the envelope the wrapper
receives (application fields + the transport's ``kind`` + ``caller_session_id``)
and rejects any other key, or a listed key with a wrong JSON type, with
``field_forbidden`` — BEFORE any ORM access. Server-owned keys
(``frozen_at_line``, ``snapshot_at``, ``show_timestamps``, ``notify_on_view``)
are rejected whatever their value: they are simply not listed.

No direct Django/ORM use in this module. It is not transitively Django-free:
``ShareError`` comes from ``share_mutation`` — already imported wherever this
module is used. The ORM-dependent gate steps and Layer-2 value rules
(settings, scope, provenance, debug refusal, password-clear refusal, frozen
default, share host, expiry) live in ``share_mutation.py``'s
``*_from_payload`` wrappers. They import this module lazily to keep the graph
acyclic.
"""

from __future__ import annotations

from twicc.core.services.share_mutation import ShareError

# Kind → synced-settings gate key (§4).
SETTING_KEYS: dict[str, str] = {
    "session": "allowAgentSessionShares",
    "artifact": "allowAgentArtifactShares",
}


def setting_key_for(kind: str) -> str:
    return SETTING_KEYS[kind]


def caller_type_error(payload: dict) -> ShareError | None:
    """§7.1 step 1: ``caller_session_id``, when present, must be a JSON string —
    checked before any ORM access (resolving it is itself an ORM lookup)."""
    if "caller_session_id" not in payload:
        return None
    if not isinstance(payload["caller_session_id"], str):
        return ShareError(
            "caller_session_id",
            "field_forbidden",
            "caller_session_id must be a JSON string",
        )
    return None


# ── type predicates (JSON semantics: bool is NOT an int) ──────────────────


def _is_str(v) -> bool:
    return isinstance(v, str)


def _is_str_or_null(v) -> bool:
    return v is None or isinstance(v, str)


def _is_bool(v) -> bool:
    return isinstance(v, bool)


def _is_int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _is_dict(v) -> bool:
    return isinstance(v, dict)


_CREATE_COMMON_TYPES = {
    "kind": (_is_str, "a JSON string"),
    "caller_session_id": (_is_str, "a JSON string"),
    "kind_target": (_is_str, 'the JSON string "session" or "artifact"'),
    "label": (_is_str, "a JSON string"),
    "password": (_is_str_or_null, "a JSON string or null"),
    "expires_at": (_is_str_or_null, "a JSON string or null"),
    "options": (_is_dict, "a JSON object"),
}
_SESSION_OPTION_TYPES = {
    "mode": (_is_str, "a JSON string"),
    "max_display_mode": (_is_str, "a JSON string"),
    "include_subagents": (_is_bool, "a literal JSON boolean"),
    "include_inline_artifacts": (_is_bool, "a literal JSON boolean"),
    "show_title": (_is_bool, "a literal JSON boolean"),
    "display_title": (_is_str, "a JSON string"),
}
_ARTIFACT_OPTION_TYPES = {
    "show_title": (_is_bool, "a literal JSON boolean"),
    "display_title": (_is_str, "a JSON string"),
}


def _forbidden(key: str, message: str) -> ShareError:
    return ShareError(key, "field_forbidden", message)


def _check_keys_and_types(
    payload: dict, allowed_types: dict, *, context: str
) -> list[ShareError]:
    errors: list[ShareError] = []
    accepted = ", ".join(sorted(allowed_types))
    for key, value in payload.items():
        spec = allowed_types.get(key)
        if spec is None:
            errors.append(
                _forbidden(
                    key, f"unknown key {key!r} in {context}; accepted keys: {accepted}"
                )
            )
            continue
        check, expected = spec
        if not check(value):
            errors.append(_forbidden(key, f"{key} must be {expected}"))
    return errors


def _require(
    payload: dict, errors: list[ShareError], allowed_types: dict, *keys: str
) -> None:
    for key in keys:
        if key not in payload:
            errors.append(
                _forbidden(
                    key, f"{key} is required and must be {allowed_types[key][1]}"
                )
            )


def validate_create(payload: dict) -> list[ShareError]:
    """Layer 1 only for ``share:create`` (§7.2)."""
    kind_target = payload.get("kind_target")
    if kind_target == "session":
        types = {**_CREATE_COMMON_TYPES, "session_id": (_is_str, "a JSON string")}
        option_types = _SESSION_OPTION_TYPES
        target_key = "session_id"
    elif kind_target == "artifact":
        types = {**_CREATE_COMMON_TYPES, "bookmark_id": (_is_int, "a JSON integer")}
        option_types = _ARTIFACT_OPTION_TYPES
        target_key = "bookmark_id"
    else:
        # Missing, wrongly typed, or unknown value: validate the union shape
        # before kind resolution. This still names legacy/extra keys and bad
        # option types; a shape-clean unknown string reaches kind/invalid.
        types = {
            **_CREATE_COMMON_TYPES,
            "session_id": (_is_str, "a JSON string"),
            "bookmark_id": (_is_int, "a JSON integer"),
        }
        option_types = {**_SESSION_OPTION_TYPES, **_ARTIFACT_OPTION_TYPES}
        target_key = None

    errors = _check_keys_and_types(
        payload, types, context=f"share:create ({kind_target})"
    )
    _require(payload, errors, types, "kind", "caller_session_id", "kind_target")
    if isinstance(payload.get("kind"), str) and payload["kind"] != "share:create":
        errors.append(_forbidden("kind", 'kind must be exactly "share:create"'))
    if target_key is not None:
        _require(payload, errors, types, target_key)

    options = payload.get("options")
    if isinstance(options, dict):
        errors += _check_keys_and_types(options, option_types, context="options")
    return errors


_UPDATE_TYPES = {
    "kind": (_is_str, "a JSON string"),
    "caller_session_id": (_is_str, "a JSON string"),
    "share_id": (_is_str, "a JSON string"),
    "fields": (_is_dict, "a JSON object"),
}
_UPDATE_FIELD_TYPES = {
    "label": (_is_str, "a JSON string"),
    "password": (_is_str, "a JSON string"),
    "expires_at": (_is_str_or_null, "a JSON string or null"),
}


def validate_update(payload: dict) -> list[ShareError]:
    """Layer 1 only for ``share:update`` (§7.2)."""
    errors = _check_keys_and_types(payload, _UPDATE_TYPES, context="share:update")
    _require(payload, errors, _UPDATE_TYPES, "kind", "caller_session_id", "share_id")
    if isinstance(payload.get("kind"), str) and payload["kind"] != "share:update":
        errors.append(_forbidden("kind", 'kind must be exactly "share:update"'))
    fields = payload.get("fields")
    if isinstance(fields, dict):
        errors += _check_keys_and_types(fields, _UPDATE_FIELD_TYPES, context="fields")
        if fields.get("expires_at") == "":
            # Not CLI-producible on update (--expires "" normalises to null);
            # null is the explicit clear.
            errors.append(
                _forbidden(
                    "expires_at",
                    'expires_at "" is not accepted on update; use null to clear '
                    "the expiry, or an ISO 8601 datetime",
                )
            )
    return errors


_SIMPLE_TYPES = {
    "kind": (_is_str, "a JSON string"),
    "caller_session_id": (_is_str, "a JSON string"),
    "share_id": (_is_str, "a JSON string"),
}


def validate_simple(payload: dict) -> list[ShareError]:
    """Layer 1 for ``share:revoke`` / ``unrevoke`` / ``delete`` / ``propagate``."""
    errors = _check_keys_and_types(
        payload, _SIMPLE_TYPES, context=str(payload.get("kind"))
    )
    _require(payload, errors, _SIMPLE_TYPES, "kind", "caller_session_id", "share_id")
    allowed = {"share:revoke", "share:unrevoke", "share:delete", "share:propagate"}
    if isinstance(payload.get("kind"), str) and payload["kind"] not in allowed:
        errors.append(
            _forbidden("kind", "kind must be one of: " + ", ".join(sorted(allowed)))
        )
    return errors

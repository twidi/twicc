"""Apply create / update to the ``Project`` model.

Called by :class:`DropRequestsWatcher` when the CLI drops a request file
whose ``kind`` matches one of the supported project actions:

- ``kind="project:create"`` → :func:`create_project_from_payload`.
- ``kind="project:update"`` → :func:`update_project_from_payload`.
- ``kind="project:update_settings"`` → :func:`update_project_agent_settings_from_payload`.
- ``kind="project:trust"`` → :func:`decide_project_trust_from_payload`.

Also home to :func:`clean_project_agent_defaults`, the single validator /
normalizer for the per-project agent defaults (``default_provider`` +
``default_agent_settings``), shared by the HTTP ``PUT /api/projects/<id>/``
endpoint and the drop-file handlers here.

There is no ``project:delete`` by design: a Project row is bound to its
sessions (cost aggregates, workspace memberships, etc.); removing it
from the CLI would orphan that state in confusing ways. Projects are
archived, never deleted.

Both functions validate the payload (shape + types + cross-checks),
delegate the actual write to the matching helper in
:mod:`twicc.projects` (which runs under the DB write lock and emits the
matching ``project_added`` / ``project_updated`` broadcast), and return
a :class:`ProjectMutationResult` the watcher serialises into the status
file.

The drop-file is a trust boundary: the CLI does its own pre-flight
validation for nicer error messages, but every check is re-run here in
case a forged or version-skewed caller submits a malformed payload.
"""

from __future__ import annotations

import asyncio
import logging
import os

from twicc.core.ui_messages import ui_message_text
from django.db import IntegrityError

from twicc.core.models import Project
from twicc.paths import path_to_project_id
from twicc.projects import (
    ProjectMutationError,
    ProjectMutationResult,
    register_project,
    update_project_atomic,
    validate_project_name_format,
)
from twicc.workspaces import clean_browser_url_ops, validate_color


logger = logging.getLogger(__name__)


def _invalid_payload_result(field: str, message: str) -> ProjectMutationResult:
    return ProjectMutationResult(False, None, None, [
        ProjectMutationError(field, "invalid_payload", message),
    ])


def _clean_browser_url_ops(payload: dict, project_id: str) -> tuple[dict, ProjectMutationResult | None]:
    """Project-flavored wrapper around :func:`clean_browser_url_ops`."""
    ops, errors = clean_browser_url_ops(payload)
    if errors:
        return ops, ProjectMutationResult(False, project_id, None, [
            ProjectMutationError(e.field, e.code, e.message) for e in errors
        ])
    return ops, None


def clean_project_agent_defaults(default_provider, default_agent_settings):
    """Validate + normalize the per-project agent defaults.

    Shared by the HTTP ``PUT /api/projects/<id>/`` endpoint and the CLI
    drop-file handlers. Returns ``(provider_or_None, settings_or_None,
    error_or_None)``:

    - ``provider``: ``None`` or a valid :class:`Provider` value.
    - ``settings``: ``None`` or ``{ "<provider>": { "<AgentSettings field>": value } }``
      with hidden fields stripped, unknown providers/fields rejected, ``None``
      values and empty bundles dropped (so storage stays sparse).

    Each argument is validated independently — callers updating only one field
    pass ``None`` for the other.
    """
    from twicc.core.enums import Provider
    from twicc.providers.helpers import (
        AGENT_SETTINGS_HIDDEN_FROM_FRONTEND,
        AgentSettings,
        get_provider_helpers,
    )

    valid_providers = {p.value for p in Provider}

    clean_provider = None
    if default_provider is not None:
        if not isinstance(default_provider, str) or default_provider not in valid_providers:
            return None, None, ui_message_text(f"Invalid default_provider: {default_provider!r}", 'backend.projects.invalid_default_provider', {"provider": repr(default_provider)})
        clean_provider = default_provider or None

    clean_settings = None
    if default_agent_settings is not None:
        if not isinstance(default_agent_settings, dict):
            return None, None, ui_message_text("default_agent_settings must be an object or null", 'backend.projects.defaults_object')
        allowed_fields = set(AgentSettings._fields) - set(AGENT_SETTINGS_HIDDEN_FROM_FRONTEND)
        cleaned: dict = {}
        for prov, bundle in default_agent_settings.items():
            if prov not in valid_providers:
                return None, None, ui_message_text(f"Unknown provider in default_agent_settings: {prov!r}", 'backend.projects.defaults_unknown_provider', {"provider": repr(prov)})
            if not isinstance(bundle, dict):
                return None, None, ui_message_text(f"default_agent_settings[{prov!r}] must be an object", 'backend.projects.defaults_bundle_object', {"provider": repr(prov)})
            prov_bundle: dict = {}
            for field, value in bundle.items():
                if field in AGENT_SETTINGS_HIDDEN_FROM_FRONTEND:
                    continue  # strip hidden fields defensively
                if field == "permission_mode_if_untrusted":
                    # Default-shaping field, not part of the closed bundle: the
                    # permission mode seeded when the project resolves untrusted
                    # (trust design §13.1). Value must be in the provider's
                    # untrusted-allowed set.
                    if value is not None:
                        helpers = get_provider_helpers(prov)
                        if value not in helpers.UNTRUSTED_PERMISSION_MODES:
                            return None, None, (
                                ui_message_text(f"Invalid permission_mode_if_untrusted for {prov!r}: {value!r}", 'backend.projects.defaults_untrusted_mode', {"provider": repr(prov), "value": repr(value)})
                            )
                        prov_bundle[field] = value
                    continue
                if field not in allowed_fields:
                    return None, None, ui_message_text(f"Unknown agent settings field: {field!r}", 'backend.projects.defaults_unknown_field', {"field": repr(field)})
                if value is not None:
                    prov_bundle[field] = value
            if prov_bundle:
                cleaned[prov] = prov_bundle
        clean_settings = cleaned or None

    return clean_provider, clean_settings, None


async def create_project_from_payload(payload: dict) -> ProjectMutationResult:
    """Drop-file glue for ``kind="project:create"``.

    Expected payload shape::

        {
            "kind": "project:create",
            "directory": "/absolute/path",
            "name": str | None,                # optional
            "color": str | None,               # optional (hex)
            "create_directory": bool,          # optional, default False
        }

    There is no ``archived`` field by design: creating an already-archived
    project (with zero sessions, by definition) makes no practical sense;
    archive instead through ``update-project`` once the project has lived.

    The server normalises the directory via :func:`os.path.realpath`,
    derives the project id, ensures the directory exists on disk
    (creating it when ``create_directory`` is True), checks the id is
    not already taken, runs format validation on ``name`` / ``color``,
    enforces ``name`` global uniqueness, then calls
    :func:`twicc.projects.register_project` under the DB write lock —
    the same entry point the HTTP endpoint uses, so workspace auto-add
    and the ``project_added`` broadcast fire as usual.
    """
    from twicc.providers.db_writer import run_under_db_write_lock

    directory = payload.get("directory")
    if not isinstance(directory, str) or not directory:
        return _invalid_payload_result("directory",
                                       "directory must be a non-empty string.")

    name = payload.get("name")
    if name is not None and not isinstance(name, str):
        return _invalid_payload_result("name", "name must be a string or null.")

    color = payload.get("color")
    if color is not None and not isinstance(color, str):
        return _invalid_payload_result("color", "color must be a string or null.")

    create_directory = payload.get("create_directory", False)
    if not isinstance(create_directory, bool):
        return _invalid_payload_result("create_directory",
                                       "create_directory must be a boolean.")

    # Resolve to a realpath and check absoluteness — the project id is
    # derived from this, so any non-canonical input would silently mint
    # a duplicate id.
    resolved = await asyncio.to_thread(os.path.realpath, directory)
    if not os.path.isabs(resolved):
        return ProjectMutationResult(False, None, None, [
            ProjectMutationError("DIRECTORY", "invalid_directory",
                                  f"Directory must be an absolute path (got {directory!r})."),
        ])

    exists = await asyncio.to_thread(os.path.exists, resolved)
    if exists:
        is_dir = await asyncio.to_thread(os.path.isdir, resolved)
        if not is_dir:
            return ProjectMutationResult(False, None, None, [
                ProjectMutationError("DIRECTORY", "invalid_directory",
                                      f"Path {resolved!r} exists but is not a directory."),
            ])
    elif create_directory:
        try:
            await asyncio.to_thread(os.makedirs, resolved, exist_ok=True)
        except OSError as e:
            return ProjectMutationResult(False, None, None, [
                ProjectMutationError("DIRECTORY", "directory_creation_failed",
                                      f"Failed to create directory {resolved!r}: {e}"),
            ])
    else:
        return ProjectMutationResult(False, None, None, [
            ProjectMutationError("DIRECTORY", "directory_not_found",
                                  f"Directory {resolved!r} does not exist. "
                                  "Pass --create-directory to create it."),
        ])

    project_id = path_to_project_id(resolved)

    # ID collision (project already exists for this directory).
    if await Project.objects.filter(id=project_id).aexists():
        return ProjectMutationResult(False, project_id, None, [
            ProjectMutationError("DIRECTORY", "project_already_exists",
                                  f"A project already exists for directory {resolved!r} "
                                  f"(id: {project_id!r})."),
        ])

    # Format checks on name + color.
    errors: list[ProjectMutationError] = []
    errors.extend(validate_project_name_format(name))
    errors.extend(ProjectMutationError(e.field, e.code, e.message)
                  for e in validate_color(color, field="color"))
    if errors:
        return ProjectMutationResult(False, project_id, None, errors)

    # Global name uniqueness.
    normalized_name: str | None = None
    if name is not None:
        trimmed = name.strip()
        normalized_name = trimmed if trimmed else None
    if normalized_name is not None:
        if await Project.objects.filter(name=normalized_name).aexists():
            return ProjectMutationResult(False, project_id, None, [
                ProjectMutationError("name", "duplicate_name",
                                      f"Another project already uses the name {normalized_name!r}."),
            ])

    # Single entry point — fires project_added broadcast + workspace auto-add.
    try:
        project, created = await run_under_db_write_lock(
            lambda: register_project(
                project_id,
                directory=resolved,
                name=normalized_name,
                color=color or None,
            )
        )
    except IntegrityError as e:
        logger.warning("create_project IntegrityError for %s: %s", project_id, e)
        return ProjectMutationResult(False, project_id, None, [
            ProjectMutationError("name", "duplicate_name",
                                  "Another project already uses this name "
                                  "(race-detected at write time)."),
        ])
    if not created:
        # Lost a race with another creator between the exists-check and now.
        return ProjectMutationResult(False, project_id, project, [
            ProjectMutationError("DIRECTORY", "project_already_exists",
                                  f"A project already exists for directory {resolved!r} "
                                  f"(race-detected at write time)."),
        ])

    return ProjectMutationResult(True, project_id, project, None)


async def update_project_from_payload(payload: dict) -> ProjectMutationResult:
    """Drop-file glue for ``kind="project:update"``.

    Expected payload shape (every patch field optional)::

        {
            "kind": "project:update",
            "project_id": str,
            "name": str | None,             # optional rename
            "unset_name": bool,             # optional, clears the custom name
            "color": str | None,            # optional recolor
            "unset_color": bool,            # optional, clears the color
            "archived": bool,               # optional
            "default_provider": str | None, # optional, provider new sessions default to
            "unset_default_provider": bool, # optional, back to inherit
            "worktree_directory": str | None,  # optional, base dir for new worktrees
            "unset_worktree_directory": bool,  # optional, back to the global default
            # Saved Browser-pane URL ops (combinable; applied clear → remove →
            # add → set-default):
            "add_browser_url": {"url": str, "label": str | None,
                                "set_default": bool} | None,   # optional
            "remove_browser_url": str | None,       # optional, idempotent
            "set_default_browser_url": str | None,  # optional, must be listed
            "clear_browser_urls": bool,             # optional, drop every entry
        }

    The CLI ensures at least one patch field is present (``no_op``
    enforced locally) and that mutually-exclusive flags (``name`` vs
    ``unset_name``, ``color`` vs ``unset_color``, ``default_provider`` vs
    ``unset_default_provider``, ``worktree_directory`` vs
    ``unset_worktree_directory``) are not combined.
    """
    project_id = payload.get("project_id")
    if not isinstance(project_id, str) or not project_id:
        return _invalid_payload_result("project_id",
                                       "project_id must be a non-empty string.")

    new_name = payload.get("name")
    if new_name is not None and not isinstance(new_name, str):
        return _invalid_payload_result("name", "name must be a string or null.")
    unset_name = payload.get("unset_name", False)
    if not isinstance(unset_name, bool):
        return _invalid_payload_result("unset_name", "unset_name must be a boolean.")
    if unset_name and new_name is not None:
        return ProjectMutationResult(False, project_id, None, [
            ProjectMutationError("--name", "conflicting_flags",
                                  "--name and --unset-name cannot be used together."),
        ])

    color = payload.get("color")
    if color is not None and not isinstance(color, str):
        return _invalid_payload_result("color", "color must be a string or null.")
    unset_color = payload.get("unset_color", False)
    if not isinstance(unset_color, bool):
        return _invalid_payload_result("unset_color", "unset_color must be a boolean.")
    if unset_color and color is not None:
        return ProjectMutationResult(False, project_id, None, [
            ProjectMutationError("--color", "conflicting_flags",
                                  "--color and --unset-color cannot be used together."),
        ])

    archived = payload.get("archived")
    if archived is not None and not isinstance(archived, bool):
        return _invalid_payload_result("archived", "archived must be a boolean.")

    default_provider = payload.get("default_provider")
    unset_default_provider = payload.get("unset_default_provider", False)
    if not isinstance(unset_default_provider, bool):
        return _invalid_payload_result("unset_default_provider",
                                       "unset_default_provider must be a boolean.")
    if unset_default_provider and default_provider is not None:
        return ProjectMutationResult(False, project_id, None, [
            ProjectMutationError("--default-provider", "conflicting_flags",
                                  "--default-provider and --unset-default-provider "
                                  "cannot be used together."),
        ])
    if default_provider is not None:
        _prov, _unused, err = clean_project_agent_defaults(default_provider, None)
        if err is not None:
            return ProjectMutationResult(False, project_id, None, [
                ProjectMutationError("--default-provider", "invalid_provider", err),
            ])
        default_provider = _prov

    worktree_directory = payload.get("worktree_directory")
    unset_worktree_directory = payload.get("unset_worktree_directory", False)
    if not isinstance(unset_worktree_directory, bool):
        return _invalid_payload_result("unset_worktree_directory",
                                       "unset_worktree_directory must be a boolean.")
    if worktree_directory is not None:
        if not isinstance(worktree_directory, str):
            return _invalid_payload_result("worktree_directory",
                                           "worktree_directory must be a string or null.")
        if unset_worktree_directory:
            return ProjectMutationResult(False, project_id, None, [
                ProjectMutationError("--worktree-directory", "conflicting_flags",
                                      "--worktree-directory and --unset-worktree-directory "
                                      "cannot be used together."),
            ])
        # Same normalization as the HTTP PUT: trimmed, empty means clear.
        worktree_directory = worktree_directory.strip() or None
        if worktree_directory is None:
            unset_worktree_directory = True

    browser_ops, op_error = _clean_browser_url_ops(payload, project_id)
    if op_error is not None:
        return op_error

    # Format checks.
    errors: list[ProjectMutationError] = []
    if not unset_name and new_name is not None:
        errors.extend(validate_project_name_format(new_name, field="--name"))
    if not unset_color and color is not None:
        errors.extend(ProjectMutationError(e.field, e.code, e.message)
                      for e in validate_color(color, field="--color"))
    if errors:
        return ProjectMutationResult(False, project_id, None, errors)

    # Global name uniqueness (excluding self).
    normalized_name: str | None = None
    if not unset_name and new_name is not None:
        trimmed = new_name.strip()
        normalized_name = trimmed if trimmed else None
        if normalized_name is not None:
            collision = await (Project.objects
                               .filter(name=normalized_name)
                               .exclude(id=project_id)
                               .aexists())
            if collision:
                return ProjectMutationResult(False, project_id, None, [
                    ProjectMutationError("--name", "duplicate_name",
                                          f"Another project already uses the name "
                                          f"{normalized_name!r}."),
                ])

    return await update_project_atomic(
        project_id,
        new_name=new_name,
        unset_name=unset_name,
        color=color,
        unset_color=unset_color,
        archived=archived,
        default_provider=default_provider,
        unset_default_provider=unset_default_provider,
        worktree_directory=worktree_directory,
        unset_worktree_directory=unset_worktree_directory,
        **browser_ops,
    )


async def update_project_agent_settings_from_payload(payload: dict) -> ProjectMutationResult:
    """Drop-file glue for ``kind="project:update_settings"``.

    Expected payload shape::

        {
            "kind": "project:update_settings",
            "project_id": str,
            "provider": str,                      # a registered Provider value
            "updates": { "<field>": value|null }  # null = unset (back to inherit)
        }

    Patches one provider's bundle inside ``Project.default_agent_settings``:
    a non-null value sets the field, ``null`` removes it (inherit from the
    parent chain / global default). Untouched fields — and the other
    providers' bundles — are left alone. A provider may be **disabled**: the
    edit UI deliberately lets a project keep defaults for disabled providers
    (deactivation may be temporary), so this handler only requires the
    provider to be *registered*.

    The CLI resolves aliases and validates values against the
    provider's choices pre-flight; this handler re-runs the structural checks
    (field names, ``permission_mode_if_untrusted`` in the untrusted-allowed
    set) through :func:`clean_project_agent_defaults` because the drop-file
    is a trust boundary.
    """
    from twicc.projects import update_project_agent_defaults_atomic

    project_id = payload.get("project_id")
    if not isinstance(project_id, str) or not project_id:
        return _invalid_payload_result("project_id",
                                       "project_id must be a non-empty string.")

    provider = payload.get("provider")
    if not isinstance(provider, str) or not provider:
        return _invalid_payload_result("provider",
                                       "provider must be a non-empty string.")

    updates = payload.get("updates")
    if not isinstance(updates, dict) or not updates:
        return _invalid_payload_result("updates",
                                       "updates must be a non-empty object.")

    # Hidden fields are stripped (not rejected) by the cleaner below — strip
    # them from the applied patch too, so a forged key can't slip past the
    # validation into the stored bundle.
    from twicc.providers.helpers import AGENT_SETTINGS_HIDDEN_FROM_FRONTEND
    updates = {f: v for f, v in updates.items() if f not in AGENT_SETTINGS_HIDDEN_FROM_FRONTEND}
    if not updates:
        return _invalid_payload_result("updates",
                                       "updates must contain at least one supported field.")

    # Structural validation: run the set (non-null) part of the patch through
    # the shared cleaner — unknown provider/field and an out-of-set
    # ``permission_mode_if_untrusted`` are rejected here. The unset (null)
    # keys only need their field name checked, which the cleaner does too
    # since it skips null values after the name check.
    _unused, _cleaned, err = clean_project_agent_defaults(None, {provider: updates})
    if err is not None:
        return ProjectMutationResult(False, project_id, None, [
            ProjectMutationError("updates", "invalid_settings", err),
        ])

    return await update_project_agent_defaults_atomic(
        project_id, provider=provider, updates=updates,
    )


async def decide_project_trust_from_payload(payload: dict) -> ProjectMutationResult:
    """Drop-file glue for ``kind="project:trust"``.

    Expected payload shape::

        {
            "kind": "project:trust",
            "project_id": str,
            "trusted": bool | None,       # True / False = explicit, null = reset to inherit
            "propagation": bool | None,   # optional; defaults to "is under git" for an
                                          # explicit decision, ignored on reset
        }

    Project **trust is a human-only decision**: this path backs the
    ``twicc update-project --trust/--untrust/--reset-trust`` flags and the
    web dialog, never an agent-facing skill. Delegates to
    :func:`twicc.core.services.trust.decide_project_trust` (provider-config
    projection + DB write under lock + ``project_updated`` broadcast).
    """
    from twicc.core.services.trust import decide_project_trust

    project_id = payload.get("project_id")
    if not isinstance(project_id, str) or not project_id:
        return _invalid_payload_result("project_id", "project_id must be a non-empty string.")

    if "trusted" not in payload:
        return _invalid_payload_result("trusted", "trusted is required (true, false, or null).")
    trusted = payload.get("trusted")
    if trusted is not None and not isinstance(trusted, bool):
        return _invalid_payload_result("trusted", "trusted must be true, false, or null.")

    propagation = payload.get("propagation")
    if propagation is not None and not isinstance(propagation, bool):
        return _invalid_payload_result("propagation", "propagation must be a boolean or null.")

    project = await Project.objects.filter(id=project_id).afirst()
    if project is None:
        return ProjectMutationResult(False, project_id, None, [
            ProjectMutationError("PROJECT", "project_not_found",
                                  f"Project {project_id!r} not found."),
        ])

    result = await decide_project_trust(project_id, trusted, propagation)
    if not result.get("ok"):
        code = result.get("error", "trust_decision_failed")
        return ProjectMutationResult(False, project_id, None, [
            ProjectMutationError("trust", code, f"Trust decision failed: {code}."),
        ])

    project = await Project.objects.filter(id=project_id).afirst()
    return ProjectMutationResult(True, project_id, project, None)

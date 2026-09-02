"""CLI launch builder for hybrid sessions.

Builds the exact ``claude`` argv equivalent of what the SDK agent configures
through ``ClaudeAgentOptions`` (see ``agent.py``'s options builder — every
decision here mirrors it one-for-one), plus the hybrid-specific pieces:
the single injected ``PermissionRequest`` hook, forced file checkpointing,
the temp title and the attachments directory.

All flags verified empirically on the bundled CLI 2.1.170 (design doc §3.2).

**Fresh provider home caveat** (verified on CLI 2.1.258, 2026-09-02): in a
Claude home that has never run the CLI — a relocated ``CLAUDE_CONFIG_DIR``
(``twicc.provider_homes``), or a first install — the pane opens on the CLI's
own onboarding wizard (theme, then login method) instead of the composer, so
the first message is never submitted and no JSONL is written. That state lives
in the home's ``.claude.json`` and is the CLI's to own: TwiCC deliberately
seeds nothing (writing a partial ``.claude.json`` risks a CLI that disagrees
with its own schema). The user answers the wizard once, in the embedded
terminal. SDK sessions are unaffected — only the interactive CLI onboards.
"""

import shlex
from pathlib import Path

import orjson

from twicc.agent.plugin import get_plugin_dir
from twicc.core.enums import Provider
from twicc.paths import get_hybrid_hooks_dir, get_session_hybrid_dir
from twicc.providers.claude_code.bin import resolve_bundled_binary
from twicc.providers.helpers import AgentSettings, get_provider_helpers


# How long the CLI lets the PermissionRequest hook live — i.e. how long the
# GUI-answer channel stays open for one prompt (the hook IS the channel: it
# polls for the answer file until the CLI reaps it). Mirror of TwiCC's own
# rule that an agent is never auto-killed while a pending request is open.
# 14 days: comfortably "as long as the CLI session" in practice while staying
# under Node's setTimeout ceiling of 2^31-1 ms (~24.8 days) — values above it
# overflow. Ceiling probed empirically (2026-06-12, plan Task 2).
HYBRID_HOOK_TIMEOUT_SECONDS = 14 * 24 * 3600


def build_hooks_settings(session_id: str, fast_mode: bool) -> str:
    """Inline ``--settings`` JSON: fastMode + forced file checkpointing + the
    single ``PermissionRequest`` hook.

    Schema validated empirically on CLI 2.1.170 (2026-06-11 design probe).
    The hook is pure shell — it drops its stdin JSON into the watched
    hybrid-hooks directory (no HTTP endpoint, no token; filesystem write
    access is the authentication), then polls for a ``.status.json`` answer
    next to its drop (the drop-requests convention) and prints it on stdout —
    the CLI applies the decision and dismisses the displayed TUI dialog
    (design doc 2026-06-12 §4.2). ``$$`` + nanoseconds make the name unique;
    the ``.tmp`` → ``mv`` rename makes the drop atomic (the watcher ignores
    ``.tmp`` and ``.status.json`` files). The poll loop is unbounded: the
    CLI's own ``timeout`` is the reaper, and the agent's clearing janitor
    reaps orphans sooner via a dummy status file. On drop failure exit
    early — TwiCC will never see the request, so nobody would ever answer.
    """
    hooks_dir = shlex.quote(str(get_hybrid_hooks_dir()))
    name = f"{session_id}__PermissionRequest__$$-$(date +%s%N)"
    command = (
        f'n={hooks_dir}/{name}; cat > "$n.json.tmp" && mv "$n.json.tmp" "$n.json" || exit 0; '
        f'while :; do if [ -f "$n.status.json" ]; then cat "$n.status.json"; rm -f "$n.status.json"; exit 0; fi; '
        f'sleep 0.2; done'
    )
    settings = {
        "fastMode": fast_mode,
        # Forced ON: off by default in SDK mode and user-disablable in user
        # settings; the original-file capture (compute) depends on it. The
        # env purge already drops an inherited
        # CLAUDE_CODE_DISABLE_FILE_CHECKPOINTING.
        "fileCheckpointingEnabled": True,
        # Ghost prompt suggestions render INSIDE the composer whenever it is
        # empty, so the composer-ready detector (tmux.composer_ready) reads
        # them as a TUI draft and fail-fasts every TwiCC send until the user
        # manually dismisses them (observed on CLI 2.1.173, 2026-06-12).
        # Disable the feature for hybrid sessions.
        "promptSuggestionEnabled": False,
        "hooks": {
            "PermissionRequest": [{
                "hooks": [{
                    "type": "command",
                    "command": command,
                    "timeout": HYBRID_HOOK_TIMEOUT_SECONDS,
                }],
            }],
        },
    }
    return orjson.dumps(settings).decode()


def build_argv(
    *,
    session_id: str,
    settings: AgentSettings,
    resume: bool,
    temp_title: str,
    addendum_path: Path | None,
    add_dirs: list[str],
    untrusted: bool,
) -> list[str]:
    """Build the full claude argv for a hybrid launch.

    ``settings`` must be the RESOLVED bundle (no ``None`` placeholders left
    for fields the provider uses). ``untrusted`` is the project's effective
    trust, resolved by the caller the same way the SDK agent does
    (``project_is_untrusted``); the permission-mode security floor is
    re-applied here regardless of what the stored session value says.
    ``add_dirs`` carries the session work dirs (artifacts/scratch, mirroring
    the SDK's ``add_dirs``) plus the hybrid attachments dir.
    """
    helpers = get_provider_helpers(Provider.CLAUDE_CODE)
    argv = [str(resolve_bundled_binary())]
    if resume:
        argv += ["--resume", session_id]
    else:
        argv += ["--session-id", session_id]
    sdk_model = helpers.resolve_sdk_model(settings.selected_model, settings.context_max)
    if sdk_model:
        argv += ["--model", sdk_model]
    if settings.effort:
        argv += ["--effort", settings.effort]
    # Mirror the SDK thinking mapping: True → adaptive, False → disabled,
    # None → omit (CLI default). ``--thinking-display summarized`` is required
    # on the True branch — the SDK sets it via ``ThinkingConfigAdaptive(
    # display="summarized")`` and the CLI defaults to ``omitted``, which strips
    # the thinking summary and leaves an empty thinking block. (No display on
    # the disabled branch — the SDK's ``ThinkingConfigDisabled`` carries none.)
    if settings.thinking_enabled is True:
        argv += ["--thinking", "adaptive", "--thinking-display", "summarized"]
    elif settings.thinking_enabled is False:
        argv += ["--thinking", "disabled"]
    permission_mode = settings.permission_mode
    if untrusted:
        # Security floor (trust design §13.4): clamp + user-only settings
        # sources (project/local settings, hooks and .mcp.json are
        # repo-controlled and must not shape the session).
        from twicc.core.services.trust import clamp_permission_mode_for_untrusted

        permission_mode = clamp_permission_mode_for_untrusted(Provider.CLAUDE_CODE, permission_mode)
        argv += ["--setting-sources", "user"]
    else:
        # Opt-in for bypassPermissions, withheld in untrusted projects.
        argv += ["--allow-dangerously-skip-permissions"]
        argv += ["--setting-sources", "user,project,local"]
    if permission_mode:
        argv += ["--permission-mode", permission_mode]
    # Mirror the SDK chrome mapping (truthy → --chrome, else --no-chrome).
    argv += ["--chrome"] if settings.claude_in_chrome else ["--no-chrome"]
    if settings.question_widget is False:
        argv += ["--disallowedTools", "AskUserQuestion"]
    argv += ["--settings", build_hooks_settings(session_id, bool(settings.fast_mode))]
    argv += ["--plugin-dir", str(get_plugin_dir())]
    # TwiCC's own MCP server (/mcp): pass the per-session config as a FILE path
    # (never inline JSON — the bearer token would show in ``ps``). No
    # ``--strict-mcp-config`` so the user's own MCP servers survive.
    # ``--allowedTools mcp__twicc`` permits our tools in EVERY mode: ``dontAsk``
    # (read-only) denies a permissioned tool before the hook fires, so the
    # allow-list is required for the control plane to work in read-only.
    from twicc.mcp import mcp_enabled
    from twicc.mcp.wiring import write_claude_mcp_config

    if mcp_enabled():
        argv += ["--mcp-config", str(write_claude_mcp_config(session_id))]
        argv += ["--allowedTools", "mcp__twicc"]
    for add_dir in add_dirs:
        argv += ["--add-dir", add_dir]
    if addendum_path is not None:
        argv += ["--append-system-prompt-file", str(addendum_path)]
    if temp_title:
        argv += ["-n", temp_title[:100]]
    return argv


def write_addendum_file(session_id: str, addendum: str | None) -> Path | None:
    """Persist the frozen system-prompt addendum for ``--append-system-prompt-file``.

    A file (hidden flag, verified) rather than an inline argument: the
    addendum is multi-KB and shell-quoting it into the pane command would be
    fragile. Rewritten at every launch — content is byte-stable by design
    (see ``Session.system_prompt_addendum``).
    """
    if not addendum:
        return None
    path = get_session_hybrid_dir(session_id) / "addendum.md"
    path.write_text(addendum, encoding="utf-8")
    return path

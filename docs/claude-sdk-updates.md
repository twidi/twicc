# Claude Agent SDK updates

Every Claude Agent SDK update requires both live diagnostics below.
Run them from the checkout with the updated SDK.
Do not ship an update while either required diagnostic reports FAIL or INCONCLUSIVE.

```bash
uv run python scripts/diagnose_hermetic_llm.py --provider all --live --yes
uv run python scripts/diagnose_claude_title_sync.py --live --yes --json
```

The first command checks hermetic model calls for both providers.
The second command checks persistent Claude session titles against the actual bundled CLI.
These checks supplement each other.

## Private title protocol

`src/twicc/providers/claude_code/title_control.py` owns the accepted private SDK dependency.
It calls `client._query._send_control_request()` with:

```python
{
    "subtype": "rename_session",
    "title": title,
    "source": "host",
    "session_id": session_id,
}
```

An acknowledgement alone does not prove publication.
The diagnostic checks a new matching title record after dispatch.
It verifies all six SDK checks from section 12 of
[the title synchronization spec](plans/2026-11-01-claude-session-title-sync-spec.md):

1. The private Python control entry point returns correctly.
2. The CLI accepts the title, `source`, and target session identity.
3. Renames work while idle and during response streaming.
4. Metadata re-append and session resume preserve the title.
5. The CLI rejects the wrong session identity.
6. The CLI accepts voluntary reuse of an earlier title.

The diagnostic creates the UUID before the first prompt with `extra_args['session-id']`.
It also reproduces the unsafe active-session file append in its own disposable transcript.
The CLI must restore its in-memory title on the next metadata flush.
This deliberate diagnostic mutation is not a production publication route.

## Hybrid evidence

Hybrid mode remains disabled. The required SDK gate reports hybrid as NOT_RUN.
Before future hybrid activation, also run the explicit live gate:

```bash
uv run python scripts/diagnose_claude_title_sync.py --live --yes --hybrid --json
```

This command attempts a real isolated tmux/TUI `/rename`.
It checks command echo, command-result ancestry, and title record positions.
It passes those actual observations through the title coordinator.
A successful command outcome permits observed convergence.
It never proves that TwiCC owns the paste.
The independent delivery hazard must block successor publication until process death.

A blocked explicit TUI probe reports INCONCLUSIVE and exits with code 1.
This separate hybrid gate does not block SDK updates while hybrid remains disabled.
The binding SDK update checks come from spec section 12.
The user accepts the strict hybrid limitation and has not activated hybrid mode.
Unit fixtures do not replace live CLI evidence.
The accepted strict limitation can retain a hazard until process death.
Do not weaken attribution to make the diagnostic pass.

## Isolation and output

```bash
# Versions only. No model calls.
uv run python scripts/diagnose_claude_title_sync.py --json

# Offline contracts. No model calls.
uv run pytest tests/test_claude_title_diagnostic.py -q
```

Live mode requires `--yes` because it makes billable Haiku calls.
The report includes installed SDK version, actual bundled CLI version, check statuses, call count, and API cost.
Exit codes are 0 for PASS, 1 for FAIL or INCONCLUSIVE, and 2 for setup failure.
Reports contain no credentials or model response text.

Credentials use the existing runtime credential accessor and configured provider homes.
Each live run creates temporary working, Claude configuration, secure-storage, and user home directories.
It copies no user configuration or transcripts.
SDK probes disable tools, MCP, settings, skills, automatic memory, and project instructions.
Every child receives `provider_env_overlay()` before intentional isolated-home overrides.
Inherited Claude nesting variables are removed for the isolated SDK transport.
The run restores its environment, stops its own tmux server, and removes its temporary files.
It never stops an existing user tmux server.

Retain the JSON report with the SDK update review.
Record both versions and every unresolved check.
Do not retain generated transcripts or credentials.

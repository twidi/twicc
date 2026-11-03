"""MCP-only tool descriptions, for commands whose CLI help is too long.

Clients cap a tool description at 1024 characters, and the external surface
appends ``EXTERNAL_DESCRIPTION_SUFFIX`` (``server.py``) to every one, so a
description must stay within ``MAX_DESCRIPTION_LENGTH``. By default a tool's
description is the command's full CLI help (``tools._description_for``); the
commands below exceed the cap, so their MCP description is a short text that
names the MCP parameters (``wait_reply``, ``from_line``) instead of the CLI
flags. The details stay in the CLI help, the parameter descriptions and the
skills. Keep each text in sync with its command's CLI help.
"""

from __future__ import annotations

MAX_DESCRIPTION_LENGTH = 1024

EXTERNAL_DESCRIPTION_SUFFIX = "\nExternal MCP: use explicit IDs. No self or parent references."

MCP_DESCRIPTIONS: dict[str, str] = {
    "timeline": """Read recorded timeline events, newest first, with cursor pagination.

Scope: all projects by default, or project, workspace or session. A project includes its worktrees; \
a worktree only itself. Session filters use current metadata: annotation, filiation, provider, state, active. \
Hidden excluded by default; filiation includes them as in sessions. Use include_hidden or only_hidden. \
Archived sessions always included.

Events carry IDs; sessions and projects map cited IDs to serialized objects. Sessions use the sessions command's \
reduced format; full=true includes every session field and the full process block. Projects are fully serialized. \
Workspace scope also returns the complete workspace object. \
pagination: limit (default 20, max 100), has_more, next_cursor. Pass next_cursor as before with the same filters. \
No offset or total. Instance events appear only in baseline scopes without explicit session-selection filters.""",
    "create-session": """Create a session. PROMPT is required; other options use settings defaults.

Asynchronous: created means started, not completed. wait_reply waits for confirmed logical completion and returns a reply block. Prefer the returned wait_token with session_wait_reply for a later wait. Reuse it after an ordinary timeout for the same input. Without a token, use from_line=0 for a new session. To wait past a consumed or ignored answer, omit the old token and use its line_num as from_line.

Confirmation initially takes one second without root activity, pending delivery, or assistant holds. Return the last relevant indexed answer. A pending request takes priority over intermediate text. Background shells do not extend confirmation. wait_background ignores answers read during background work and continues into later intervals. Read reply.outcome; the exit code only reports creation. Read process.state to check whether the session still runs.""",
    "send-message": """Send to an existing session. Keeps stored agent settings; change them with update_session_settings. prompt is optional with attach.

Asynchronous: sent means accepted, not completed. wait_reply returns the last relevant indexed answer after confirmed logical completion, initially one second. A pending request takes priority over intermediate text. wait_background ignores answers read during background work and continues into later intervals.

Accepted sends return wait_token and last_line captured before provider delivery. For a later wait, prefer session_wait_reply with that wait_token. Reuse it after an ordinary timeout for the same input. Without a token, use from_line=last_line. To wait past a consumed or ignored answer, omit the old token and use its line_num as from_line; repeat wait_background if needed. Read process.state from session or sessions_get to check whether it still runs.""",
    "send-messages": """Send the same message to several sessions. Each send runs an agent and spends tokens. message is optional with attach.

Selection: session_ids UNIONED with spawned_by/descendants/siblings/annotation. Returns results by id and summary. Exit 0 for per-session failures; 6 if none sent.

sent means accepted, not completed. Each accepted send returns wait_token and last_line captured before provider delivery. For later waits, prefer session_wait_reply per returned token; reuse it after an ordinary timeout for the same input. Without a token, use each last_line as from_line. sessions_wait_reply accepts no tokens; for a batch, use since from before the send.

wait_reply returns the last relevant indexed answer after confirmed logical completion, initially one second. Pending requests take priority over intermediate text. wait_first stops at the first conclusion. wait_background ignores answers during background work and waits for later intervals.""",
    "session/wait-reply": """Wait for confirmed logical completion. Sends nothing. Prefer create/send wait_token. It selects the input and admission cursor. Reuse it after an ordinary timeout. No token: use last_line as from_line. After a consumed/ignored answer, omit the old token; use line_num as from_line and repeat wait_background if needed.

from_line/since (ISO 8601): mutually exclusive, strict bounds. No token/bounds: after last user message, or last_line with stale compute; unindexed: 0.

Confirm after one second without root activity, pending delivery or assistant holds. Return last relevant indexed answer. Pending requests take priority. Shells do not delay confirmation; wait_background ignores answers during background work and waits for later intervals.

outcome: replied, awaiting_user_input, ended, timeout, provider_error, backend_gone, wait_failed. Exit 0 answered/blocked, 5 timeout/ended/provider_error, 2 backend gone, 1 broken/refused.""",
    "sessions/wait-reply": """Wait for several sessions. Sends nothing. One wait_timeout covers all; wait_first stops at first conclusion. Same completion rules as session_wait_reply; wait_background applies per session.

Selection: listing filters UNIONED with session_ids; an id or filter is required. Hidden included, archived excluded, state=dead refused.

Prefer session_wait_reply per returned wait_token after a send. This plural tool accepts no tokens or from_line. For one cursor-based batch, use since (ISO 8601) from before the send. Otherwise start after each last user message, or current last_line if compute is stale; unindexed: 0.

Returns summary (total, replied, awaiting_user_input, concluded, all_replied) and reply per id; unknown id: unknown_session. Exit 0: inspect outcomes; 1: refusal. Retry a timed-out batch with since=batch start. To wait past an ignored background answer, use singular from_line=its line_num without the old token, with wait_background again.""",
}

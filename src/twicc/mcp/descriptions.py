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
    "send-message": """Send a message to an existing session.

prompt may be omitted when attach is given. The session keeps its stored agent settings; change them with \
update_session_settings.

Asynchronous by default: "sent" only means the agent received the message. Pass wait_reply to wait until the \
session concludes (an answer, or a pending request only a human can clear) and get the answer back in the same \
call. Accepted sends return wait_token and last_line captured before provider delivery. The wait returns the last relevant indexed answer after confirmed logical completion, initially one second. With wait_background, a final \
message read while background work runs does not count: the first one read once it has ended does. To check \
whether a session still runs, read process.state from session or sessions_get.""",
    "send-messages": """Send the same message to several sessions.

Selection: session_ids UNIONED with spawned_by/descendants/siblings/annotation. message may be omitted with attach. \
Output: results keyed by session id and a summary. Exit 0 for per-session failures; exit 6 if none was sent.

sent means accepted, not completed. Each accepted send returns wait_token and last_line captured before provider \
delivery. wait_reply returns each last relevant indexed answer after confirmed logical completion, initially one \
second. A pending request takes priority over intermediate text. wait_first stops at the first recipient to conclude. \
wait_background explicitly ignores answers read during background work and continues into later intervals.

Each send starts or resumes an agent and spends tokens.""",
    "session/wait-reply": """Wait for confirmed logical completion. Sends nothing.

Initially one second without root activity, pending delivery, or assistant holds. Returns the last relevant indexed \
answer. A pending request takes priority over intermediate text. Background shells do not extend confirmation; \
wait_background explicitly ignores answers read during background work and continues into later intervals.

wait_token selects an accepted input and defaults to its stored admission cursor. Explicit from_line or since \
(ISO 8601), mutually exclusive, stays strict. Otherwise: after the last user message; unindexed sessions: line 0.

outcome: replied, awaiting_user_input, ended, timeout, provider_error, backend_gone, wait_failed. Resume from \
line_num after replied/provider_error or an ignored background answer; otherwise since_line_num.

Exit 0 answered/blocked, 5 timeout/ended/provider_error, 2 backend gone, 1 refusal/broken wait.""",
    "sessions/wait-reply": """Block until several sessions conclude, each past its own cursor. Sends nothing. One \
wait_timeout covers the whole batch. Each ends like session_wait_reply. wait_first stops at the first one. \
wait_background applies per session.

Selection: the sessions listing filters, with session_ids UNIONED on top. At least one id or filter is required. \
Hidden always included, archived always excluded, state=dead refused.

Cursor: each session starts after its own last user message, or at since (an ISO 8601 instant). No from_line. \
After send_messages without wait_reply, pass since = an instant taken before the send.

Returns summary (total, replied, awaiting_user_input, concluded, all_replied) and one session_wait_reply block per \
id; an unknown id gives outcome unknown_session. Exit 0 whatever the outcomes: branch on the summary or each \
outcome. Exit 1 on a local refusal. To resume a timed-out batch, run it again with since = the batch start.""",
}

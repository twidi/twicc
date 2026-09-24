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
    "send-message": """Send a message to an existing session.

prompt may be omitted when attach is given. The session keeps its stored agent settings; change them with \
update_session_settings.

Asynchronous by default: "sent" only means the agent received the message. Pass wait_reply to wait until the \
session concludes (an answer, or a pending request only a human can clear) and get the answer back in the same \
call. The wait covers the turn THIS message triggers, never the previous one. To check whether a session still \
runs, read process.state from session or sessions_get.""",
    "send-messages": """Send the same message to several sessions at once.

Selection: session_ids UNIONED with spawned_by / descendants / siblings / annotation. message may be omitted when \
attach is given. Output is keyed by session id, with a summary. A per-session failure never fails the batch \
(exit 0); exit 6 if no session was sent.

Asynchronous by default: "sent" only means the agent received the message. Pass wait_reply to wait until the \
recipients conclude (an answer, or a pending request only a human can clear) and get each answer back; each \
recipient is waited from its own cursor, never its previous turn. wait_first stops at the first recipient to \
conclude.

Each send starts or resumes an agent (real work, token spend): a batch can cold-start many stopped sessions.""",
    "session/wait-reply": """Block until a session concludes, past a cursor. Sends nothing.

Use it on a session nobody just messaged, or one you messaged without wait_reply (then pass from_line = the \
last_line the send returned).

It ends on an answer (the message closing a turn) or a pending request only a human can clear; an answer wins a tie.

Cursor: from_line (a line number, strictly past it) or since (an ISO 8601 instant), mutually exclusive. Omitted: \
after the last user message. A just-spawned, not-yet-indexed session is waited from line 0.

outcome: `replied`, `awaiting_user_input`, `ended` (crash, interruption, empty answer), `timeout`, \
`provider_error`, `backend_gone`, `wait_failed`. To resume, pass as from_line: after `replied` or \
`provider_error`, its `line_num`; after any other outcome, its `since_line_num`.

Exit 0 answered or blocked, 5 timeout, 2 TwiCC stopped, 1 local refusal or broken wait.""",
    "sessions/wait-reply": """Block until several sessions conclude, each past its own cursor. Sends nothing. One \
wait_timeout covers the whole batch. Each ends like session_wait_reply. wait_first stops at the first one.

Selection: the sessions listing filters, with session_ids UNIONED on top. At least one id or filter is required. \
Hidden always included, archived always excluded, state=dead refused.

Cursor: each session starts after its own last user message, or at since (an ISO 8601 instant). No from_line. \
After send_messages without wait_reply, pass since = an instant taken before the send.

Returns summary (total, replied, awaiting_user_input, concluded, all_replied) and one session_wait_reply block per \
id; an unknown id gives outcome unknown_session. Exit 0 whatever the outcomes: branch on the summary or each \
outcome. Exit 1 on a local refusal. To resume a timed-out batch, run it again with since = the batch start.""",
}

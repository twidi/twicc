"""Frozen external MCP event contracts and deterministic identifiers.

Callback URLs enter identity exactly as validated. This module never normalizes them.
Runtime, storage and transport import these shared protocol constants.
"""

from hashlib import sha256

import orjson

EVENT_NAME = "session.concluded"
CAPABILITY_EXTENSION = "io.modelcontextprotocol/events"
INVALID_PARAMS = -32602
NOT_FOUND = -32011
RESOURCE_EXHAUSTED = -32013
UNSUPPORTED = -32014
CALLBACK_ENDPOINT_ERROR = -32015

MAX_SUBSCRIPTIONS_PER_CONNECTION = 50
MAX_SUBSCRIPTIONS = 100
MAX_CONCURRENT_VERIFICATIONS = 8
MAX_VERIFICATIONS_PER_MINUTE = 60
MAX_CALLBACK_URL_LENGTH = 2048
MAX_SINCE_LINE_NUM = 2147483647
MIN_SECRET_BYTES = 24
MAX_SECRET_BYTES = 64
MAX_BODY_BYTES = 262144
MAX_RESPONSE_BYTES = 4096
DEFAULT_TTL_MS = 86400000
MIN_TTL_MS = 3600000
MAX_TTL_MS = 604800000
VERIFICATION_CACHE_SECONDS = 86400
VERIFICATION_SLOT_WAIT_SECONDS = 5
VERIFICATION_RATE_WINDOW_SECONDS = 60
ATTEMPT_TIMEOUT_SECONDS = 10
SECRET_ROTATION_SECONDS = 300
RETRY_DELAYS_SECONDS = (0, 30, 120)
SUPERVISOR_INTERVAL_SECONDS = 5
EPOCH_BACKSTOP_SECONDS = 5
FAILURE_BACKOFF_INITIAL_SECONDS = 0.25
FAILURE_BACKOFF_MAX_SECONDS = 60
POISON_FAILURE_THRESHOLD = 3
FAILURE_LOG_INTERVAL_SECONDS = 60
WRITER_DRAIN_SECONDS = 2
EXPIRY_CLEANUP_MARGIN_SECONDS = 60

EVENT_DEFINITION = {
    "name": "session.concluded",
    "description": "A TwiCC session concluded past the cursor: it answered, it is blocked on a question or an "
    "approval, the provider refused the turn, or the turn ended without an answer. Same "
    "conclusion as the session_wait_reply tool.",
    "delivery": ["webhook"],
    "inputSchema": {
        "type": "object",
        "properties": {
            "session_id": {"type": "string", "minLength": 1, "description": "Full id of the session to watch."},
            "wait_background": {
                "type": "boolean",
                "default": False,
                "description": "A final message read while background "
                "work runs behind the agent (a subagent, "
                "background shell, Monitor, scheduled "
                "wake-up or goal) does not count; the "
                "event fires on the first final message "
                "read once that work has ended. If the "
                "agent then starts a new turn, the "
                "ignored message is skipped and the new "
                "turn's conclusion is delivered. If no "
                "new answer comes and the agent stays "
                "idle, no event fires; the ignored "
                "message stays readable with the session "
                "content tools (after a server restart it "
                "can still be delivered as replied once "
                "the background work has ended). A dead "
                "agent fires 'ended' carrying it.",
            },
            "since_line_num": {
                "type": "integer",
                "minimum": 0,
                "maximum": 2147483647,
                "description": "On a new subscription: deliver the first "
                "conclusion strictly after this transcript "
                "line, even if it already happened. Pass "
                "the last_line a send returned.",
            },
        },
        "required": ["session_id"],
        "additionalProperties": False,
    },
    "payloadSchema": {
        "type": "object",
        "properties": {
            "session_id": {"type": "string", "description": "Full id of the session."},
            "session_title": {"type": ["string", "null"], "description": "Session title, null when unknown."},
            "request_type": {
                "type": "string",
                "description": "Only for awaiting_user_input: ask_user_question, tool_approval or hybrid_terminal.",
            },
            "reply": {
                "type": "object",
                "properties": {
                    "outcome": {
                        "type": "string",
                        "enum": ["replied", "awaiting_user_input", "provider_error", "ended"],
                        "description": "Same values and meaning as the reply.outcome of session_wait_reply.",
                    },
                    "line_num": {
                        "type": ["integer", "null"],
                        "description": "Transcript line of "
                        "the message, usable "
                        "with the session "
                        "content tools. Null "
                        "when there is none.",
                    },
                    "is_final": {
                        "type": ["boolean", "null"],
                        "description": "Whether that message "
                        "closed a turn. Null "
                        "when there is no "
                        "message or when it "
                        "is unknown.",
                    },
                    "since_line_num": {
                        "type": "integer",
                        "description": "The cursor this conclusion was searched after.",
                    },
                    "text": {
                        "type": "string",
                        "description": "Text of the message or of the provider error. Absent when there is none.",
                    },
                    "text_truncated": {
                        "type": "boolean",
                        "description": "Present and "
                        "true only when "
                        "text was cut "
                        "to fit the "
                        "delivery size "
                        "limit; the "
                        "full text is "
                        "readable with "
                        "line_num.",
                    },
                },
                "required": ["outcome", "line_num", "is_final", "since_line_num"],
            },
        },
        "required": ["session_id", "session_title", "reply"],
    },
}

INPUT_SCHEMA = EVENT_DEFINITION["inputSchema"]
PAYLOAD_SCHEMA = EVENT_DEFINITION["payloadSchema"]


def canonical_identity(arguments: dict) -> bytes:
    """Serialize validated identity arguments; a starting cursor is not identity."""
    return orjson.dumps(
        {"session_id": arguments["session_id"], "wait_background": arguments.get("wait_background", False)},
        option=orjson.OPT_SORT_KEYS,
    )


def subscription_id(connection_id: str, url: str, name: str, arguments: dict) -> str:
    """Identify a subscription within its connection and exact callback URL."""
    parts = [connection_id.encode(), url.encode(), name.encode(), canonical_identity(arguments)]
    return "sub_" + sha256(b"\x00".join(parts)).hexdigest()[:32]


def event_id(subscription_id: str, outcome: str, key: str) -> str:
    """Identify one conclusion; callers supply the epoch-aware or request key."""
    return "evt_" + sha256(f"{subscription_id}\x00{outcome}\x00{key}".encode()).hexdigest()[:32]

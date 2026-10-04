"""External event methods, transactional subscriptions, and discovery capability.

Registration is explicit. Importing this module never changes either MCP server.
SubscriptionSnapshot is private backend state, including signing secrets. It must
never be included in owner API snapshots or method responses.
"""

import asyncio
import base64
import binascii
from copy import deepcopy
from datetime import datetime, timedelta
import math
from types import MappingProxyType
from typing import Any, Mapping, NamedTuple
from urllib.parse import urlsplit

from asgiref.sync import sync_to_async
from django.db import transaction
from jsonschema import Draft202012Validator
from mcp.shared.exceptions import MCPError
from mcp_types import RequestParams
from pydantic import ConfigDict

from twicc.agent import registry
from twicc.agent.states import AgentState
from twicc.cli.session import lookup_wait_session
from twicc.core.models import McpEventSubscription, McpOperation, Session
from twicc.mcp.events import SYSTEM_CLOCK
from twicc.mcp.events import catalog
from twicc.mcp.events.delivery import VerificationError
from twicc.mcp.events.prompts import first_non_command_prompt
from twicc.mcp.identity import external_caller
from twicc.mcp.oauth import storage
from twicc.paths import get_data_dir


class ListEventsParams(RequestParams):
    model_config = ConfigDict(extra="allow")


class SubscribeParams(RequestParams):
    model_config = ConfigDict(extra="allow")
    name: Any = None
    arguments: Any = None
    delivery: Any = None
    cursor: Any = None
    ttlMs: Any = None


class UnsubscribeParams(RequestParams):
    model_config = ConfigDict(extra="allow")
    name: Any = None
    arguments: Any = None
    delivery: Any = None


class SubscriptionSnapshot(NamedTuple):
    """Immutable full row passed to the runtime, never a public serializer."""

    id: str
    connection_id: str
    name: str
    arguments: Mapping[str, Any]
    session_id: str
    callback_url: str
    secret: str
    previous_secret: str
    previous_secret_until: datetime | None
    cursor_line: int
    cursor_at: float
    initial_last_line: int
    turn_open: bool
    turn_started_at: float | None
    turn_opened_by: str
    turn_start_line: int
    numbering: int | None
    data_dir: str
    refresh_before: datetime
    created_at: datetime
    updated_at: datetime

    @property
    def generation(self):
        return self.id, self.created_at

    @classmethod
    def from_row(cls, row):
        values = {name: getattr(row, name) for name in cls._fields}
        # The validated arguments contain only scalar values.
        values["arguments"] = MappingProxyType(dict(row.arguments))
        return cls(**values)


def invalid(reason):
    raise MCPError(catalog.INVALID_PARAMS, "Invalid event parameters", {"reason": reason})


def validate_url(value):
    if not isinstance(value, str) or len(value) > catalog.MAX_CALLBACK_URL_LENGTH:
        invalid("delivery.url must be an HTTPS URL of at most 2048 characters")
    try:
        parsed = urlsplit(value)
        valid = (
            parsed.scheme == "https" and parsed.hostname and parsed.hostname.isascii()
            and parsed.username is None and parsed.password is None and not parsed.fragment
            and "#" not in value
        )
        # Accessing port validates both the numeric value and its range.
        parsed.port
    except ValueError:
        invalid("delivery.url has an invalid hostname or port")
    if not valid:
        invalid("delivery.url requires HTTPS, an ASCII hostname, no credentials, and no fragment")
    return value


def validate_secret(value):
    if not isinstance(value, str) or not value.startswith("whsec_"):
        invalid("delivery.secret must be a whsec_ secret")
    encoded = value.removeprefix("whsec_")
    try:
        decoded = base64.b64decode(encoded + "=" * (-len(encoded) % 4), validate=True)
    except (ValueError, binascii.Error):
        invalid("delivery.secret must contain strict base64")
    if not catalog.MIN_SECRET_BYTES <= len(decoded) <= catalog.MAX_SECRET_BYTES:
        invalid("delivery.secret must decode to 24 through 64 bytes")
    return value


def granted_ttl(value):
    if value is None:
        return catalog.DEFAULT_TTL_MS
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        invalid("ttlMs must be a finite number or null")
    if isinstance(value, float) and not math.isfinite(value):
        invalid("ttlMs must be a finite number or null")
    return max(catalog.MIN_TTL_MS, min(catalog.MAX_TTL_MS, value))


def is_live(row, data_dir, arrival):
    return row is not None and row.data_dir == data_dir and row.refresh_before > arrival


def check_limits(connection_id, data_dir, arrival):
    rows = McpEventSubscription.objects.filter(data_dir=data_dir, refresh_before__gt=arrival)
    for count, maximum in (
        (rows.filter(connection_id=connection_id).count(), catalog.MAX_SUBSCRIPTIONS_PER_CONNECTION),
        (rows.count(), catalog.MAX_SUBSCRIPTIONS),
    ):
        if count >= maximum:
            raise MCPError(catalog.RESOURCE_EXHAUSTED, "Subscription limit reached",
                           {"limit": "subscriptions", "max": maximum})


def unknown_session():
    raise MCPError(catalog.NOT_FOUND, "Unknown session", {"reason": "unknown_session"})


class EventMethods:
    """Handlers with injected verification and synchronous runtime command sinks.

    runtime.add(snapshot) and runtime.remove(id, created_at) enqueue commands.
    Transaction callbacks post them to the event loop after commit. They survive
    request cancellation during storage.write. Task 13 owns runtime lifecycle.
    """

    def __init__(self, runtime, verification, *, clock=SYSTEM_CLOCK, data_dir=None):
        self.runtime = runtime
        self.verification = verification
        self.clock = clock
        self.data_dir = str(get_data_dir().resolve()) if data_dir is None else str(data_dir)

    async def list_events(self, ctx, params):
        return {"events": [deepcopy(catalog.EVENT_DEFINITION)]}

    async def subscribe(self, ctx, params):
        loop = asyncio.get_running_loop()
        arrival = self.clock.utcnow()
        if not isinstance(params.name, str):
            invalid("name must be a string")
        if not isinstance(params.delivery, dict) or "mode" not in params.delivery:
            invalid("delivery must be an object with a mode")
        if not isinstance(params.arguments, dict):
            invalid("arguments must be an object")
        ttl = granted_ttl(params.ttlMs)
        if params.name != catalog.EVENT_NAME:
            raise MCPError(catalog.NOT_FOUND, "Unknown event", {"kind": "event"})
        if params.delivery["mode"] != "webhook":
            raise MCPError(catalog.UNSUPPORTED, "Unsupported delivery mode",
                           {"feature": "deliveryMode", "value": params.delivery["mode"]})
        error = next(Draft202012Validator(catalog.INPUT_SCHEMA).iter_errors(params.arguments), None)
        if error is not None:
            invalid(error.message)
        arguments = dict(params.arguments)
        if "since_line_num" in arguments:
            arguments["since_line_num"] = int(arguments["since_line_num"])
        secret = validate_secret(params.delivery.get("secret"))
        url = validate_url(params.delivery.get("url"))
        caller = external_caller.get()
        lookup = await sync_to_async(lookup_wait_session)(arguments["session_id"], accept_history_epoch=True)
        session = lookup.session
        initial_last_line = session.last_line if session is not None else 0
        numbering = session.history_epoch if session is not None else 0
        if session is not None and session.last_offset == 0 and numbering > 0:
            numbering = None
        identity = catalog.subscription_id(caller.connection_id, url, params.name, arguments)

        def precheck():
            row = McpEventSubscription.objects.filter(pk=identity).first()
            if not is_live(row, self.data_dir, arrival):
                if not lookup.accepted:
                    unknown_session()
                check_limits(caller.connection_id, self.data_dir, arrival)

        await sync_to_async(precheck)()
        try:
            await self.verification.verify(caller.connection_id, url, identity, secret)
        except VerificationError as exc:
            raise MCPError(exc.code, exc.message, exc.data) from exc

        def mutate():
            with transaction.atomic():
                row = McpEventSubscription.objects.filter(pk=identity).first()
                if is_live(row, self.data_dir, arrival):
                    row.refresh_before = arrival + timedelta(milliseconds=ttl)
                    if row.secret != secret:
                        row.previous_secret = row.secret
                        row.previous_secret_until = self.clock.utcnow() + timedelta(
                            seconds=catalog.SECRET_ROTATION_SECONDS,
                        )
                        row.secret = secret
                    row.arguments = arguments
                    row.save(update_fields=["refresh_before", "secret", "previous_secret",
                                            "previous_secret_until", "arguments", "updated_at"])
                else:
                    if not lookup.accepted:
                        unknown_session()
                    check_limits(caller.connection_id, self.data_dir, arrival)
                    if row is not None:
                        row.delete()
                    cursor = arguments.get("since_line_num", initial_last_line)
                    info = registry.get_agent_manager_registry().get_agent_info(arguments["session_id"])
                    turn_open, turn_started_at, opened_by = False, None, ""
                    if info is not None and info.state in (AgentState.STARTING, AgentState.ASSISTANT_TURN):
                        turn_open, turn_started_at, opened_by = True, info.state_changed_at, "initial"
                    else:
                        provider = session.provider if session is not None else Session.objects.filter(
                            pk=arguments["session_id"],
                        ).values_list("provider", flat=True).first()
                        prompt = first_non_command_prompt(arguments["session_id"], provider, cursor) if provider else None
                        if prompt is not None:
                            turn_open, opened_by = True, "history"
                            turn_started_at = prompt.timestamp.timestamp() if prompt.timestamp else 0.0
                    row = McpEventSubscription.objects.create(
                        id=identity, connection_id=caller.connection_id, name=params.name, arguments=arguments,
                        session_id=arguments["session_id"], callback_url=url, secret=secret,
                        cursor_line=cursor, cursor_at=self.clock.epoch(), initial_last_line=initial_last_line,
                        turn_open=turn_open, turn_started_at=turn_started_at, turn_opened_by=opened_by,
                        turn_start_line=cursor, numbering=numbering, data_dir=self.data_dir,
                        refresh_before=arrival + timedelta(milliseconds=ttl),
                    )
                    self._audit(caller, "events/subscribe", row)
                snapshot = SubscriptionSnapshot.from_row(row)
                transaction.on_commit(lambda: loop.call_soon_threadsafe(self.runtime.add, snapshot))
                return snapshot

        snapshot = await storage.write(mutate)
        return {"id": snapshot.id, "refreshBefore": snapshot.refresh_before.isoformat(),
                "cursor": None, "truncated": params.cursor is not None}

    async def unsubscribe(self, ctx, params):
        loop = asyncio.get_running_loop()
        if not isinstance(params.name, str) or not params.name:
            invalid("name must be a non-empty string")
        if not isinstance(params.arguments, dict):
            invalid("arguments must be an object")
        session_id = params.arguments.get("session_id")
        if not isinstance(session_id, str) or not session_id:
            invalid("arguments.session_id must be a non-empty string")
        if not isinstance(params.arguments.get("wait_background", False), bool):
            invalid("arguments.wait_background must be a boolean")
        if not isinstance(params.delivery, dict):
            invalid("delivery must be an object")
        url = validate_url(params.delivery.get("url"))
        caller = external_caller.get()
        identity = catalog.subscription_id(caller.connection_id, url, params.name, params.arguments)

        def mutate():
            with transaction.atomic():
                row = McpEventSubscription.objects.filter(pk=identity, connection_id=caller.connection_id).first()
                if row is None:
                    return None
                generation = row.id, row.created_at
                self._audit(caller, "events/unsubscribe", row)
                row.delete()
                transaction.on_commit(lambda: loop.call_soon_threadsafe(self.runtime.remove, *generation))

        await storage.write(mutate)
        return {}

    @staticmethod
    def _audit(caller, method, row):
        McpOperation.objects.create(
            connection_id=caller.connection_id, name=caller.name, tool=method,
            targets={"session_id": row.session_id, "subscription_id": row.id},
        )


async def discovery_capability(ctx, call_next):
    result = await call_next(ctx)
    if ctx.method == "server/discover":
        capabilities = result.setdefault("capabilities", {})
        capabilities["events"] = {}
        capabilities.setdefault("extensions", {})[catalog.CAPABILITY_EXTENSION] = {}
    return result


def register_event_methods(server, methods):
    """Explicit external-server hook. The integration layer calls this once."""
    server.add_request_handler("events/list", ListEventsParams, methods.list_events)
    server.add_request_handler("events/subscribe", SubscribeParams, methods.subscribe)
    server.add_request_handler("events/unsubscribe", UnsubscribeParams, methods.unsubscribe)
    if discovery_capability not in server.middleware:
        server.middleware.append(discovery_capability)

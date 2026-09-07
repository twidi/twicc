"""Pure MCP batch validation, public schemas, and bounded result assembly."""

from copy import deepcopy
from functools import lru_cache, partial
import re
from typing import NamedTuple

from jsonschema.validators import validator_for
from mcp import types as mcp_types
import orjson

from twicc.mcp.dispatch import PreparedTool, UnknownToolError, check_caller_arguments
from twicc.rpc.generator import CommandSpec

BATCH_NAMES = frozenset({"batch", "batch_read"})
MAX_CALLS = 20
MAX_ERRORS = 100
MAX_DIAGNOSTIC_CHARS = 512
MAX_CHILD_BYTES = 384 * 1024
MAX_RESULT_BYTES = 16 * 1024 * 1024
ID_PATTERN = r"^[A-Za-z0-9_-]{1,64}$"
_ID = re.compile(ID_PATTERN)


class PreparedCall(NamedTuple):
    index: int
    id: str
    tool: PreparedTool


class PreparedBatch(NamedTuple):
    batch_id: str
    mode: str
    on_error: str
    calls: tuple[PreparedCall, ...]


class BatchValidation(NamedTuple):
    prepared: PreparedBatch | None
    rejection: dict | None


def _object(properties, **extra):
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
        **extra,
    }


_CALL_SCHEMA = _object(
    {
        "id": {"type": "string", "pattern": ID_PATTERN, "maxLength": 64},
        "name": {"type": "string"},
        "arguments": {"type": "object"},
    }
)
BATCH_INPUT_SCHEMAS = {
    name: _object(
        {
            "calls": {"type": "array", "minItems": 1, "maxItems": MAX_CALLS, "items": _CALL_SCHEMA},
            "mode": {
                "type": "string",
                "enum": ["sequential"] if name == "batch" else ["sequential", "parallel"],
                "default": "sequential" if name == "batch" else "parallel",
            },
            "on_error": {
                "type": "string",
                "enum": ["continue", "stop"],
                "default": "stop" if name == "batch" else "continue",
            },
        }
    )
    for name in BATCH_NAMES
}
for _schema in BATCH_INPUT_SCHEMAS.values():
    _schema["required"] = ["calls"]
    _schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"

_MESSAGES = {
    "invalid_batch": "Batch structure is invalid.",
    "duplicate_id": "Call ID must be unique.",
    "unknown_tool": "Tool is not available.",
    "invalid_arguments": "Arguments are invalid.",
    "tool_not_allowed": "Tool is not allowed in this batch.",
    "invalid_policy": "Batch policy is invalid.",
    "server_busy": "Batch capacity is full. Submit a new request later.",
    "execution_error": "Command execution did not return a normal response.",
    "previous_call_failed": "Command did not start because an earlier command failed.",
    "authorization_changed": "Command did not start because authorization changed.",
    "authorization_unavailable": "Command did not start because authorization could not be checked.",
    "response_too_large": "Command ran but its response is omitted because it exceeds the output limit.",
}
_VALIDATOR_MESSAGES = {
    "required": "Required argument is missing.",
    "type": "Argument has an invalid type.",
    "enum": "Argument is not an allowed value.",
    "const": "Argument is not an allowed value.",
    "additionalProperties": "Unknown arguments are not allowed.",
    **{
        key: "Argument is outside the allowed range or pattern."
        for key in (
            "minimum",
            "maximum",
            "exclusiveMinimum",
            "exclusiveMaximum",
            "minLength",
            "maxLength",
            "minItems",
            "maxItems",
            "pattern",
            "multipleOf",
        )
    },
}


@lru_cache(maxsize=512)
def _validator(name: str, schema_bytes: bytes):
    """Include schema bytes so injected registries cannot reuse stale validators."""
    schema = orjson.loads(schema_bytes)
    cls = validator_for(schema)
    cls.check_schema(schema)
    return cls(schema)


def _safe_id(value):
    return value if isinstance(value, str) and _ID.fullmatch(value) else None


def _pointer(error, schema, prefix, *, required_index=0):
    """Stop before keys supplied through pattern/additional properties or refs."""
    parts = list(prefix)
    node = schema
    for part in error.absolute_path:
        if not isinstance(node, dict):
            break
        if isinstance(part, int):
            parts.append(str(part))
            node = node.get("items", {})
        elif part in node.get("properties", {}):
            parts.append(part)
            node = node["properties"][part]
        else:
            break
    else:
        if error.validator == "required" and isinstance(error.instance, dict):
            missing = next(
                (
                    key
                    for index, key in enumerate(key for key in error.validator_value if key not in error.instance)
                    if index == required_index and key in node.get("properties", {})
                ),
                None,
            )
            if missing is not None:
                parts.append(missing)
    path = "".join("/" + str(part).replace("~", "~0").replace("/", "~1") for part in parts)
    return path if len(path) <= MAX_DIAGNOSTIC_CHARS else "".join("/" + str(part) for part in prefix)


def _diagnostic(code, *, index=None, id=None, path="", message=None):
    return {
        "index": index,
        "id": _safe_id(id),
        "code": code,
        "path": path[:MAX_DIAGNOSTIC_CHARS],
        "message": (message or _MESSAGES[code])[:MAX_DIAGNOSTIC_CHARS],
    }


def _rejection(batch_id, errors, truncated=False):
    return {
        "batch_id": batch_id,
        "status": "rejected",
        "ok": False,
        "executed": 0,
        "errors": errors,
        "errors_truncated": truncated,
    }


def rejected_batch(batch_id: str, code: str) -> dict:
    return _rejection(batch_id, [_diagnostic(code)])


def validate_batch(
    name: str,
    arguments: object,
    *,
    registry: dict[str, CommandSpec],
    read_only_paths: frozenset[str],
    external: bool,
    batch_id: str,
) -> BatchValidation:
    schema = BATCH_INPUT_SCHEMAS[name]
    # Structural failure can stop before inspecting any command arguments.
    error = next(_validator(name, orjson.dumps(schema)).iter_errors(arguments), None)
    if error is not None:
        code = "invalid_policy" if list(error.absolute_path) in (["mode"], ["on_error"]) else "invalid_batch"
        return BatchValidation(None, _rejection(batch_id, [_diagnostic(code, path=_pointer(error, schema, []))]))
    for index, entry in enumerate(arguments["calls"]):
        if _safe_id(entry["id"]) is None:
            return BatchValidation(
                None, _rejection(batch_id, [_diagnostic("invalid_batch", index=index, path=f"/calls/{index}/id")])
            )
    mode = arguments.get("mode", schema["properties"]["mode"]["default"])
    policy = arguments.get("on_error", schema["properties"]["on_error"]["default"])
    if mode == "parallel" and policy == "stop":
        return BatchValidation(None, rejected_batch(batch_id, "invalid_policy"))

    def errors_and_calls():
        seen = set()
        for index, entry in enumerate(arguments["calls"]):
            id, tool_name, args = entry["id"], entry["name"], entry["arguments"]
            base = ["calls", index, "arguments"]
            diagnostic = partial(_diagnostic, index=index, id=id, path=f"/calls/{index}/name")
            if id in seen:
                yield diagnostic("duplicate_id", path=f"/calls/{index}/id")
            seen.add(id)
            if tool_name in BATCH_NAMES:
                yield diagnostic("tool_not_allowed")
                continue
            spec = registry.get(tool_name)
            if spec is None:
                yield diagnostic("unknown_tool")
                continue
            invalid = False
            required_occurrences = {}
            for error in _validator(tool_name, orjson.dumps(spec.json_schema)).iter_errors(args):
                invalid = True
                required_index = 0
                if error.validator == "required":
                    # Validators yield one error per absent required key, in schema order.
                    # Match that order without reading the value-bearing error message.
                    location = (tuple(error.absolute_schema_path), tuple(error.absolute_path))
                    required_index = required_occurrences.get(location, 0)
                    required_occurrences[location] = required_index + 1
                yield diagnostic(
                    "invalid_arguments",
                    path=_pointer(error, spec.json_schema, base, required_index=required_index),
                    message=_VALIDATOR_MESSAGES.get(error.validator, _MESSAGES["invalid_arguments"]),
                )
            if invalid:
                continue
            try:
                check_caller_arguments(tool_name, args, external=external)
            except (UnknownToolError, ValueError):
                yield diagnostic("tool_not_allowed")
                continue
            if name == "batch_read" and spec.path not in read_only_paths:
                yield diagnostic("tool_not_allowed")
                continue
            yield PreparedCall(index, id, PreparedTool(tool_name, spec, deepcopy(args)))

    errors, calls = [], []
    for item in errors_and_calls():
        if isinstance(item, PreparedCall):
            calls.append(item)
        elif len(errors) == MAX_ERRORS:
            return BatchValidation(None, _rejection(batch_id, errors, True))
        else:
            errors.append(item)
    if errors:
        return BatchValidation(None, _rejection(batch_id, errors))
    return BatchValidation(PreparedBatch(batch_id, mode, policy, tuple(calls)), None)


_WRAPPER_CODES = [
    "execution_error",
    "previous_call_failed",
    "authorization_changed",
    "authorization_unavailable",
    "response_too_large",
]
_REJECTION_CODES = [
    "invalid_batch",
    "duplicate_id",
    "unknown_tool",
    "invalid_arguments",
    "tool_not_allowed",
    "invalid_policy",
    "server_busy",
]
_NULL = {"type": "null"}
_ID_SCHEMA = {"type": "string", "pattern": ID_PATTERN, "maxLength": 64}
_ERROR_SCHEMA = _object(
    {
        "code": {"enum": _WRAPPER_CODES},
        "message": {"type": "string", "maxLength": MAX_DIAGNOSTIC_CHARS},
        "caused_by": {"anyOf": [_ID_SCHEMA, _NULL]},
    }
)
_RESPONSE_SCHEMA = _object({"exit_code": {"type": "integer"}, "result": {}, "error": {"type": ["string", "null"]}})
_RECORD_SCHEMA = _object(
    {
        "id": _ID_SCHEMA,
        "name": {"type": "string"},
        "status": {"enum": ["success", "command_error", "tool_error", "skipped"]},
        "outcome_unknown": {"type": "boolean"},
        "response": {"anyOf": [_RESPONSE_SCHEMA, _NULL]},
        "error": {"anyOf": [_ERROR_SCHEMA, _NULL]},
        "response_omitted": {"type": "boolean"},
    }
)
BATCH_OUTPUT_SCHEMA = {
    "type": "object",
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "oneOf": [
        _object(
            {
                "batch_id": {"type": "string"},
                "status": {"const": "completed"},
                "ok": {"type": "boolean"},
                "summary": _object(
                    {
                        key: {"type": "integer", "minimum": 0, "maximum": MAX_CALLS}
                        for key in ("total", "succeeded", "failed", "skipped")
                    }
                ),
                "results": {"type": "array", "maxItems": MAX_CALLS, "items": _RECORD_SCHEMA},
            }
        ),
        _object(
            {
                "batch_id": {"type": "string"},
                "status": {"const": "rejected"},
                "ok": {"const": False},
                "executed": {"const": 0},
                "errors_truncated": {"type": "boolean"},
                "errors": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": MAX_ERRORS,
                    "items": _object(
                        {
                            "index": {"type": ["integer", "null"], "minimum": 0, "maximum": MAX_CALLS - 1},
                            "id": {"anyOf": [_ID_SCHEMA, _NULL]},
                            "code": {"enum": _REJECTION_CODES},
                            "path": {"type": "string", "maxLength": MAX_DIAGNOSTIC_CHARS},
                            "message": {"type": "string", "maxLength": MAX_DIAGNOSTIC_CHARS},
                        }
                    ),
                },
            }
        ),
    ],
}


def _wrapper_error(code, caused_by=None):
    return {"code": code, "message": _MESSAGES[code], "caused_by": caused_by}


def _record(call, status, *, unknown=False, response=None, error=None):
    return {
        "id": call.id,
        "name": call.tool.name,
        "status": status,
        "outcome_unknown": unknown,
        "response": response,
        "error": error,
        "response_omitted": False,
    }


def _omit(record):
    record.update(response=None, response_omitted=True, error=_wrapper_error("response_too_large"))


def command_record(call: PreparedCall, envelope: dict) -> dict:
    encoded = orjson.dumps(envelope)
    record = _record(
        call, "success" if envelope["exit_code"] == 0 else "command_error", unknown=envelope["exit_code"] == 5
    )
    if len(encoded) > MAX_CHILD_BYTES:
        _omit(record)
    else:
        record["response"] = orjson.loads(encoded)
    return record


def failed_record(call: PreparedCall, *, started: bool) -> dict:
    return _record(call, "tool_error", unknown=started, error=_wrapper_error("execution_error"))


def skipped_record(call: PreparedCall, *, code: str, caused_by: str | None = None) -> dict:
    return _record(call, "skipped", error=_wrapper_error(code, caused_by))


def _ok(records):
    return all(
        r["status"] == "success"
        and r["response"] is not None
        and not r["response_omitted"]
        and not r["outcome_unknown"]
        for r in records
    )


def completed_batch(batch_id: str, records: list[dict]) -> dict:
    return {
        "batch_id": batch_id,
        "status": "completed",
        "ok": _ok(records),
        "summary": {
            "total": len(records),
            "succeeded": sum(r["status"] == "success" for r in records),
            "failed": sum(r["status"] in ("command_error", "tool_error") for r in records),
            "skipped": sum(r["status"] == "skipped" for r in records),
        },
        "results": records,
    }


def fit_result(payload: dict) -> mcp_types.CallToolResult:
    # Keep caller-owned records intact while dropping entire retained envelopes.
    payload = deepcopy(payload)
    while True:
        result = mcp_types.CallToolResult(
            content=[mcp_types.TextContent(type="text", text=orjson.dumps(payload).decode())],
            structured_content=payload,
            is_error=payload["status"] == "rejected",
        )
        if len(result.model_dump_json(by_alias=True, exclude_unset=True).encode()) <= MAX_RESULT_BYTES:
            return result
        record = next((r for r in reversed(payload.get("results", [])) if r["response"] is not None), None)
        if record is None:
            raise ValueError("Batch metadata exceeds the output limit.")
        _omit(record)
        payload["ok"] = _ok(payload["results"])

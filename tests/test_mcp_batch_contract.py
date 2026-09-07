"""Batch validation and bounded public result contracts."""

from types import SimpleNamespace

import jsonschema
import orjson
import pytest

from twicc.mcp import batch_contract as contract
from twicc.mcp.tools import MCP_READ_ONLY_PATHS, tools_by_name


def validate(body, name="batch", **kwargs):
    return contract.validate_batch(
        name,
        body,
        registry=kwargs.pop("registry", tools_by_name()),
        read_only_paths=MCP_READ_ONLY_PATHS,
        external=kwargs.pop("external", False),
        batch_id="test-batch",
        **kwargs,
    )


def call(id="first", name="workspaces", arguments=None):
    return {"id": id, "name": name, "arguments": {} if arguments is None else arguments}


@pytest.mark.parametrize(
    "body",
    [
        None,
        [],
        {},
        {"calls": []},
        {"calls": [call()] * 21},
        {"calls": [call()], "extra": True},
        {"calls": [{"id": "x", "name": "workspaces"}]},
        {"calls": [call(arguments=[])]},
        {"calls": [call(id="x\n")]},
        {"calls": [dict(call(), extra=True)]},
    ],
)
def test_invalid_structure(body):
    result = validate(body)
    assert result.prepared is None
    assert result.rejection["executed"] == 0
    assert result.rejection["errors"][0]["code"] == "invalid_batch"


@pytest.mark.parametrize(
    "name, fields, mode, policy",
    [
        ("batch", {}, "sequential", "stop"),
        ("batch_read", {}, "parallel", "continue"),
        ("batch_read", {"mode": "sequential", "on_error": "stop"}, "sequential", "stop"),
    ],
)
def test_defaults(name, fields, mode, policy):
    result = validate({"calls": [call()], **fields}, name)
    assert result.rejection is None
    assert (result.prepared.mode, result.prepared.on_error) == (mode, policy)


@pytest.mark.parametrize(
    "name,fields",
    [("batch", {"mode": "parallel"}), ("batch_read", {"on_error": "stop"}), ("batch", {"on_error": "other"})],
)
def test_invalid_policy(name, fields):
    assert validate({"calls": [call()], **fields}, name).rejection["errors"][0]["code"] == "invalid_policy"


def test_late_invalid_child_and_duplicate():
    result = validate({"calls": [call(), call("first", "session")]})
    assert result.prepared is None
    assert result.rejection["executed"] == 0
    assert [e["code"] for e in result.rejection["errors"]] == ["duplicate_id", "invalid_arguments"]
    assert result.rejection["errors"][1]["path"] == "/calls/1/arguments/session_id"


@pytest.mark.parametrize(
    "name,code",
    [
        ("batch", "tool_not_allowed"),
        ("batch_read", "tool_not_allowed"),
        ("not_a_command", "unknown_tool"),
        ("settings", "unknown_tool"),
    ],
)
def test_child_resolution(name, code):
    assert validate({"calls": [call(name=name)]}).rejection["errors"][0]["code"] == code


def test_read_and_external_restrictions():
    assert (
        validate({"calls": [call(name="whoami")]}, external=True).rejection["errors"][0]["code"] == "tool_not_allowed"
    )
    mutation = call(name="update_session_title", arguments={"session_id": "abc", "new_title": "test"})
    assert validate({"calls": [mutation]}, "batch_read").rejection["errors"][0]["code"] == "tool_not_allowed"


def test_safe_diagnostics_and_cap():
    secret = "credential-" * 1000
    schema = {
        "type": "object",
        "properties": {f"field{i}": {"type": "integer"} for i in range(110)},
        "additionalProperties": {"type": "integer"},
    }
    registry = {"custom": SimpleNamespace(path="custom", json_schema=schema)}
    args = {f"field{i}": secret for i in range(110)} | {secret: secret}
    payload = validate({"calls": [call(name="custom", arguments=args)]}, registry=registry).rejection
    assert len(payload["errors"]) == 100
    assert payload["errors_truncated"] is True
    assert "credential" not in orjson.dumps(payload).decode()
    assert all(len(e["path"]) <= 512 and len(e["message"]) <= 512 for e in payload["errors"])


def test_unknown_property_path_stays_at_safe_ancestor():
    schema = {"type": "object", "properties": {"data": {"type": "object", "additionalProperties": {"type": "integer"}}}}
    registry = {"custom": SimpleNamespace(path="custom", json_schema=schema)}
    payload = validate(
        {"calls": [call(name="custom", arguments={"data": {"secret": "secret"}})]}, registry=registry
    ).rejection
    assert payload["errors"][0]["path"] == "/calls/0/arguments/data"
    assert "secret" not in orjson.dumps(payload).decode()


@pytest.fixture
def prepared_call():
    return validate({"calls": [call()]}).prepared.calls[0]


@pytest.mark.parametrize(
    "exit_code,status,unknown", [(0, "success", False), (4, "command_error", False), (5, "command_error", True)]
)
def test_command_records(prepared_call, exit_code, status, unknown):
    envelope = {"exit_code": exit_code, "result": [], "error": None}
    record = contract.command_record(prepared_call, envelope)
    assert record["status"] == status
    assert record["outcome_unknown"] is unknown
    assert record["response"] == envelope
    jsonschema.validate(contract.completed_batch("test", [record]), contract.BATCH_OUTPUT_SCHEMA)


def test_failures_and_skips(prepared_call):
    for started in (False, True):
        record = contract.failed_record(prepared_call, started=started)
        assert record["outcome_unknown"] is started
        assert record["error"]["code"] == "execution_error"
    skipped = contract.skipped_record(prepared_call, code="previous_call_failed", caused_by="other")
    assert skipped["response"] is None
    payload = contract.completed_batch("test", [record, skipped])
    assert payload["summary"] == {"total": 2, "succeeded": 0, "failed": 1, "skipped": 1}
    assert payload["ok"] is False
    jsonschema.validate(payload, contract.BATCH_OUTPUT_SCHEMA)


def test_omission_preserves_execution(prepared_call):
    record = contract.command_record(
        prepared_call, {"exit_code": 0, "result": "x" * contract.MAX_CHILD_BYTES, "error": None}
    )
    assert record["status"] == "success"
    assert record["response"] is None
    assert record["response_omitted"] is True
    payload = contract.completed_batch("test", [record])
    assert payload["summary"]["succeeded"] == 1
    assert payload["ok"] is False


@pytest.mark.parametrize("unit", ['"', "\\", "\n", "é", "x"])
def test_dual_output_bound(prepared_call, unit):
    envelope = {
        "exit_code": 0,
        "result": unit * (contract.MAX_CHILD_BYTES // len(orjson.dumps(unit)[1:-1]) - 100),
        "error": None,
    }
    records = [contract.command_record(prepared_call._replace(id="x" * 62 + str(i)), envelope) for i in range(20)]
    result = contract.fit_result(contract.completed_batch("test", records))
    assert result.is_error is False
    assert len(result.model_dump_json(by_alias=True, exclude_unset=True).encode()) <= contract.MAX_RESULT_BYTES
    assert orjson.loads(result.content[0].text) == result.structured_content
    jsonschema.validate(result.structured_content, contract.BATCH_OUTPUT_SCHEMA)
    assert result.structured_content["summary"]["succeeded"] == 20


def test_rejection_schema_and_adapter():
    payload = contract.rejected_batch("test", "server_busy")
    result = contract.fit_result(payload)
    assert result.is_error is True
    assert orjson.loads(result.content[0].text) == result.structured_content
    jsonschema.validate(payload, contract.BATCH_OUTPUT_SCHEMA)


def test_child_schema_dialect_defaults_and_copy():
    # Draft 4 permits boolean exclusiveMinimum, unlike later dialects.
    schema = {
        "$schema": "http://json-schema.org/draft-04/schema#",
        "type": "object",
        "properties": {
            "count": {"type": "number", "minimum": 0, "exclusiveMinimum": True},
            "items": {"type": "array", "items": {"type": "string"}},
            "defaulted": {"type": "string", "default": "keep absent"},
        },
        "additionalProperties": True,
    }
    registry = {"custom": SimpleNamespace(path="custom", json_schema=schema)}
    args = {"count": 1, "items": ["first"], "extra": True}
    prepared = validate({"calls": [call(name="custom", arguments=args)]}, registry=registry).prepared
    args["items"].append("second")
    assert prepared.calls[0].tool.arguments == {"count": 1, "items": ["first"], "extra": True}
    assert validate({"calls": [call(name="custom", arguments={"count": 0})]}, registry=registry).prepared is None


def test_schema_paths_escape_and_fall_back():
    long = "x" * 600
    schema = {"type": "object", "properties": {"a~/b": {"type": "integer"}, long: {"type": "integer"}}}
    registry = {"custom": SimpleNamespace(path="custom", json_schema=schema)}
    errors = validate(
        {"calls": [call(name="custom", arguments={"a~/b": "bad", long: "bad"})]}, registry=registry
    ).rejection["errors"]
    assert [e["path"] for e in errors] == ["/calls/0/arguments/a~0~1b", "/calls/0/arguments"]


def test_exact_error_limit_is_not_truncated():
    schema = {"type": "object", "properties": {f"field{i}": {"type": "integer"} for i in range(100)}}
    registry = {"custom": SimpleNamespace(path="custom", json_schema=schema)}
    payload = validate(
        {"calls": [call(name="custom", arguments={key: "bad" for key in schema["properties"]})]}, registry=registry
    ).rejection
    assert len(payload["errors"]) == 100
    assert payload["errors_truncated"] is False
    jsonschema.validate(payload, contract.BATCH_OUTPUT_SCHEMA)


def test_child_byte_boundary(prepared_call):
    envelope = {"exit_code": 0, "result": "", "error": None}
    envelope["result"] = "x" * (contract.MAX_CHILD_BYTES - len(orjson.dumps(envelope)))
    assert contract.command_record(prepared_call, envelope)["response"] == envelope
    envelope["result"] += "x"
    assert contract.command_record(prepared_call, envelope)["response_omitted"] is True


def test_aggregate_omits_from_end_without_mutating_input(prepared_call, monkeypatch):
    monkeypatch.setattr(contract, "MAX_RESULT_BYTES", 3500)
    records = [
        contract.command_record(prepared_call._replace(id=str(i)), {"exit_code": 0, "result": "x" * 700, "error": None})
        for i in range(3)
    ]
    output = contract.fit_result(contract.completed_batch("test", records)).structured_content
    assert [r["response_omitted"] for r in output["results"]] == [False, True, True]
    assert all(r["response"] is not None for r in records)
    assert output["summary"]["succeeded"] == 3
    assert output["ok"] is False


def test_huge_unsafe_outer_fields_are_not_echoed():
    secret = "private-token-" * 10000
    for body in (
        {"calls": [call(id=secret)]},
        {"calls": [dict(call(), **{secret: secret})]},
        {"calls": [call(name=secret)]},
        {secret: secret},
    ):
        result = contract.fit_result(validate(body).rejection)
        assert "private-token" not in result.content[0].text
        assert len(result.model_dump_json(by_alias=True, exclude_unset=True).encode()) < 4000


@pytest.mark.parametrize("name", ["batch", "batch_read"])
def test_sdk_tools_list_serializes_batch_output_schema(name):
    from mcp import types as mcp_types
    from mcp_types.methods import serialize_server_result

    tool = mcp_types.Tool(
        name=name, input_schema=contract.BATCH_INPUT_SCHEMAS[name], output_schema=contract.BATCH_OUTPUT_SCHEMA
    )
    advertised = mcp_types.ListToolsResult(tools=[tool]).model_dump(by_alias=True, exclude_unset=True)
    wire = serialize_server_result("tools/list", "2025-11-25", advertised)
    assert wire["tools"][0]["outputSchema"]["type"] == "object"
    assert len(wire["tools"][0]["outputSchema"]["oneOf"]) == 2


def test_missing_required_fields_have_distinct_schema_paths():
    payload = validate({"calls": [call(name="update_session_title")]}).rejection
    assert [error["path"] for error in payload["errors"]] == [
        "/calls/0/arguments/session_id",
        "/calls/0/arguments/new_title",
    ]
    assert all(error["message"] == "Required argument is missing." for error in payload["errors"])

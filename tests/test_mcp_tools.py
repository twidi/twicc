"""Tool derivation from the Click tree: selection, naming, metadata."""

from twicc.cli._output import listing_cutover_passed
from twicc.mcp.tools import build_mcp_registry, iter_mcp_tools
from twicc.rpc.generator import build_registry


def test_selection_matches_the_skill_surface():
    reg = build_mcp_registry()
    paths = set(reg)
    assert "whoami" in paths                       # re-admitted local-only
    assert not any(p.split("/")[0] == "settings" for p in paths)
    # ``share`` is agent-gated server-side (agent-sharing design): the tools
    # are ALWAYS exposed — a disabled setting rejects at call time (A4).
    assert any(p.split("/")[0] == "share" for p in paths)
    assert "share/create" not in paths  # the group is no longer callable (silent no-op fix)
    for banned in ("password", "token", "run", "claude", "codex"):
        assert not any(p.split("/")[0] == banned for p in paths)
    # Everything else from the RPC registry is present — except, past
    # 2026-10-01, the retired `process` / `processes` routes, which RPC keeps
    # (they answer with their removal error) and MCP drops. Read on the
    # effective cutover: MCP_EXCLUDED_ROOTS is evaluated at import.
    excluded = {"settings"}
    if listing_cutover_passed():
        excluded |= {"process", "processes"}
    rpc_paths = {p for p in build_registry() if p.split("/")[0] not in excluded}
    assert rpc_paths <= paths


def test_tool_names_are_mcp_safe_and_bijective():
    tools = iter_mcp_tools()
    names = [t.name for t in tools]
    assert len(names) == len(set(names))
    for n in names:
        assert n.replace("_", "").isalnum() and n == n.lower()
    assert "create_session" in names
    assert "update_session_settings" in names
    assert "session_content" in names
    assert "share_create_session" in names
    assert "share_create_artifact" in names
    assert "share_create" not in names
    for name in (
        "update_session_mute",
        "update_session_notify",
        "update_sessions_mute",
        "update_sessions_notify",
    ):
        assert name in names


def test_schemas_and_descriptions():
    by_name = {t.name: t for t in iter_mcp_tools()}
    reg = build_mcp_registry()
    assert by_name["create_session"].input_schema == reg["create-session"].json_schema
    assert by_name["create_session"].description  # full help, non-empty
    assert len(by_name["create_session"].description) > len(reg["create-session"].summary)
    create_properties = by_name["create_session"].input_schema["properties"]
    assert create_properties["mute_on_user_turn"]["type"] == "boolean"
    assert "finished-working" in create_properties["mute_on_user_turn"]["description"]


def test_annotations_and_always_load():
    by_name = {t.name: t for t in iter_mcp_tools()}
    assert by_name["sessions"].annotations.read_only_hint is True
    assert by_name["create_session"].annotations.read_only_hint is False
    assert (by_name["whoami"].meta or {}).get("anthropic/alwaysLoad") is True
    assert (by_name["update_workspace"].meta or {}).get("anthropic/alwaysLoad") is None
    assert by_name["share"].annotations.read_only_hint is True
    assert by_name["share_show"].annotations.read_only_hint is True
    for name in (
        "share_create_session",
        "share_create_artifact",
        "share_update",
        "share_revoke",
        "share_unrevoke",
        "share_delete",
        "share_propagate",
    ):
        assert by_name[name].annotations.read_only_hint is False


def test_every_tool_has_a_title_and_a_description():
    for tool in iter_mcp_tools():
        assert tool.title, tool.name
        assert tool.description, tool.name
    by_name = {t.name: t for t in iter_mcp_tools()}
    assert by_name["update_session_settings"].title == "Update session settings"
    assert by_name["batch_read"].title == "Batch read"


def test_descriptions_fit_the_client_cap_on_both_surfaces():
    # The external surface appends a suffix to every description; both
    # surfaces must stay within the 1024-character client cap.
    from twicc.mcp.descriptions import EXTERNAL_DESCRIPTION_SUFFIX, MAX_DESCRIPTION_LENGTH

    for tool in iter_mcp_tools():
        length = len(tool.description) + len(EXTERNAL_DESCRIPTION_SUFFIX)
        assert length <= MAX_DESCRIPTION_LENGTH, (tool.name, length)


def test_mcp_description_overrides_target_exposed_commands():
    from twicc.mcp.descriptions import MCP_DESCRIPTIONS

    assert set(MCP_DESCRIPTIONS) <= set(build_mcp_registry())
    by_name = {t.name: t for t in iter_mcp_tools()}
    assert by_name["session_wait_reply"].description == MCP_DESCRIPTIONS["session/wait-reply"]

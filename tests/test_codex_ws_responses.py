"""Unit tests for the Codex WS response builders.

The 7 builders on ``CodexWSHandler`` translate the frontend's
payload (``{tool_name, decision, ...}``, ``{tool_name, permissions, scope}``,
``{tool_name, action, ...}`` for elicitations, or
``{tool_name, answers}`` for requestUserInput) into the SDK-compliant dicts
that ``_async_approval_handler`` returns to Codex via the bridge.

Contract: return ``dict`` on valid input, ``None`` on invalid input.
``None`` triggers the upstream ``_safe_default_for`` fallback so the
SDK never receives a malformed wire response.
"""

from __future__ import annotations

import pytest

from twicc.providers.codex.ws import CodexWSHandler


@pytest.fixture
def handler():
    # The builders don't touch ``self.consumer`` — passing None is safe
    # for pure unit testing of the response builders.
    return CodexWSHandler(consumer=None)


class TestBuildCommandResponse:
    @pytest.mark.parametrize("decision", [
        "accept", "acceptForSession", "decline", "cancel",
    ])
    def test_simple_string_decisions_return_decision_key(self, handler, decision):
        result = handler._build_command_response(decision)
        assert result == {"decision": decision}

    def test_accept_with_execpolicy_amendment_happy(self, handler):
        amendment = [{"rule": "git status"}]
        result = handler._build_command_response({
            "acceptWithExecpolicyAmendment": {"execpolicy_amendment": amendment},
        })
        assert result == {
            "decision": {
                "acceptWithExecpolicyAmendment": {"execpolicy_amendment": amendment},
            },
        }

    def test_apply_network_policy_amendment_happy(self, handler):
        amendment = {"host": "example.com"}
        result = handler._build_command_response({
            "applyNetworkPolicyAmendment": {"network_policy_amendment": amendment},
        })
        assert result == {
            "decision": {
                "applyNetworkPolicyAmendment": {"network_policy_amendment": amendment},
            },
        }

    def test_unknown_string_returns_none(self, handler):
        assert handler._build_command_response("totally_made_up") is None

    def test_amendment_with_non_list_returns_none(self, handler):
        # Inner value is "not a list" — fails isinstance check.
        result = handler._build_command_response({
            "acceptWithExecpolicyAmendment": {"execpolicy_amendment": "not a list"},
        })
        assert result is None

    def test_amendment_with_non_dict_returns_none(self, handler):
        # Inner value is "not a dict" — fails isinstance check.
        result = handler._build_command_response({
            "applyNetworkPolicyAmendment": {"network_policy_amendment": "not a dict"},
        })
        assert result is None

    def test_inner_payload_not_a_dict_returns_none(self, handler):
        # The payload for a variant key must itself be a dict (not e.g. a list).
        result = handler._build_command_response({
            "acceptWithExecpolicyAmendment": "not a dict at all",
        })
        assert result is None

    def test_dict_with_no_recognized_key_returns_none(self, handler):
        assert handler._build_command_response({"randomKey": {}}) is None

    def test_dict_with_multiple_keys_returns_none(self, handler):
        # Spec: dict variant must have EXACTLY one key.
        assert handler._build_command_response({
            "acceptWithExecpolicyAmendment": {"execpolicy_amendment": []},
            "applyNetworkPolicyAmendment": {"network_policy_amendment": {}},
        }) is None

    def test_non_string_non_dict_returns_none(self, handler):
        assert handler._build_command_response(42) is None
        assert handler._build_command_response(None) is None


class TestBuildFileResponse:
    @pytest.mark.parametrize("decision", [
        "accept", "acceptForSession", "decline", "cancel",
    ])
    def test_simple_string_decisions(self, handler, decision):
        result = handler._build_file_response(decision)
        assert result == {"decision": decision}

    def test_dict_input_returns_none(self, handler):
        # fileChange has no amendment variants — dict input is invalid.
        assert handler._build_file_response({
            "acceptWithExecpolicyAmendment": {"execpolicy_amendment": []},
        }) is None

    def test_unknown_string_returns_none(self, handler):
        assert handler._build_file_response("nonsense") is None

    def test_non_string_returns_none(self, handler):
        assert handler._build_file_response(None) is None
        assert handler._build_file_response(42) is None


class TestBuildPermissionsResponse:
    def test_happy_turn_scope(self, handler):
        content = {"permissions": {"network": True}, "scope": "turn"}
        result = handler._build_permissions_response(content)
        assert result == {"permissions": {"network": True}, "scope": "turn"}

    def test_happy_session_scope(self, handler):
        content = {"permissions": {"fileSystem": []}, "scope": "session"}
        result = handler._build_permissions_response(content)
        assert result == {"permissions": {"fileSystem": []}, "scope": "session"}

    def test_missing_permissions_returns_none(self, handler):
        assert handler._build_permissions_response({"scope": "turn"}) is None

    def test_missing_scope_returns_none(self, handler):
        assert handler._build_permissions_response({"permissions": {}}) is None

    def test_invalid_scope_returns_none(self, handler):
        assert handler._build_permissions_response({
            "permissions": {}, "scope": "forever",
        }) is None

    def test_unhashable_scope_returns_none(self, handler):
        # Regression: a set-membership check on an unhashable value raised
        # TypeError through the WS layer instead of returning None.
        assert handler._build_permissions_response({
            "permissions": {}, "scope": ["turn"],
        }) is None

    def test_non_dict_permissions_returns_none(self, handler):
        assert handler._build_permissions_response({
            "permissions": "not a dict", "scope": "turn",
        }) is None

    def test_strict_auto_review_true_included_in_result(self, handler):
        content = {
            "permissions": {},
            "scope": "turn",
            "strictAutoReview": True,
        }
        result = handler._build_permissions_response(content)
        assert result == {"permissions": {}, "scope": "turn", "strictAutoReview": True}

    def test_strict_auto_review_false_included_in_result(self, handler):
        content = {
            "permissions": {},
            "scope": "turn",
            "strictAutoReview": False,
        }
        result = handler._build_permissions_response(content)
        assert result == {"permissions": {}, "scope": "turn", "strictAutoReview": False}

    def test_strict_auto_review_non_bool_not_included(self, handler):
        # Non-bool strictAutoReview is silently ignored (not a validation error).
        content = {
            "permissions": {},
            "scope": "turn",
            "strictAutoReview": "yes",
        }
        result = handler._build_permissions_response(content)
        assert result == {"permissions": {}, "scope": "turn"}
        assert "strictAutoReview" not in result

    def test_empty_permissions_dict_accepted(self, handler):
        result = handler._build_permissions_response({"permissions": {}, "scope": "turn"})
        assert result == {"permissions": {}, "scope": "turn"}


class TestBuildCodexResponse:
    def test_command_execution_dispatches_to_command_builder(self, handler):
        result = handler._build_codex_response("commandExecution", {"decision": "accept"})
        assert result == {"decision": "accept"}

    def test_file_change_dispatches_to_file_builder(self, handler):
        result = handler._build_codex_response("fileChange", {"decision": "decline"})
        assert result == {"decision": "decline"}

    def test_permissions_dispatches_to_permissions_builder(self, handler):
        content = {"permissions": {}, "scope": "turn"}
        result = handler._build_codex_response("permissions", content)
        assert result == {"permissions": {}, "scope": "turn"}

    def test_command_execution_invalid_decision_returns_none(self, handler):
        result = handler._build_codex_response("commandExecution", {"decision": "bad_decision"})
        assert result is None

    def test_file_change_invalid_decision_returns_none(self, handler):
        result = handler._build_codex_response("fileChange", {"decision": "bad_decision"})
        assert result is None

    def test_permissions_invalid_scope_returns_none(self, handler):
        result = handler._build_codex_response("permissions", {"permissions": {}, "scope": "bad"})
        assert result is None

    def test_unknown_tool_name_returns_none(self, handler):
        # _build_codex_response logs an error and returns None for unknown tool_names.
        # It does NOT fall through to _safe_default_for — the caller is responsible.
        result = handler._build_codex_response("totally_made_up", {})
        assert result is None

    @pytest.mark.parametrize("decision", ["accept", "decline"])
    def test_auto_review_denial_accepts_exact_decisions(self, handler, decision):
        result = handler._build_codex_response(
            "autoReviewDenial", {"decision": decision},
        )
        assert result == {"decision": decision}

    @pytest.mark.parametrize("decision", ["cancel", "acceptForSession", None, {}])
    def test_auto_review_denial_rejects_other_decisions(self, handler, decision):
        result = handler._build_codex_response(
            "autoReviewDenial", {"decision": decision},
        )
        assert result is None


class TestSafeDefaultFor:
    def test_command_execution_returns_decline(self, handler):
        result = handler._safe_default_for("commandExecution")
        assert result == {"decision": "decline"}

    def test_file_change_returns_decline(self, handler):
        result = handler._safe_default_for("fileChange")
        assert result == {"decision": "decline"}

    def test_permissions_returns_empty_grant_with_turn_scope(self, handler):
        result = handler._safe_default_for("permissions")
        assert result == {"permissions": {}, "scope": "turn"}

    def test_unknown_tool_returns_decline(self, handler):
        # The else branch returns {"decision": "decline"} for any unknown tool_name.
        result = handler._safe_default_for("nonsense")
        assert result == {"decision": "decline"}

    def test_another_unknown_tool_returns_decline(self, handler):
        result = handler._safe_default_for("totallyMadeUp")
        assert result == {"decision": "decline"}


class TestBuildElicitationResponse:
    @pytest.mark.parametrize("tool_name", ["mcpToolCall", "elicitationForm", "elicitationUrl"])
    @pytest.mark.parametrize("action", ["accept", "decline", "cancel"])
    def test_valid_plain_actions(self, handler, tool_name, action):
        result = handler._build_codex_response(tool_name, {"action": action})
        assert result == {"action": action, "content": None, "_meta": None}

    @pytest.mark.parametrize("persist", ["session", "always"])
    def test_mcp_tool_call_accept_with_persist(self, handler, persist):
        result = handler._build_codex_response(
            "mcpToolCall", {"action": "accept", "persist": persist},
        )
        assert result == {
            "action": "accept", "content": None, "_meta": {"persist": persist},
        }

    def test_form_accept_with_content(self, handler):
        content = {"name": "Alice", "count": 3, "ok": True, "tags": ["a", "b"]}
        result = handler._build_codex_response(
            "elicitationForm", {"action": "accept", "content": content},
        )
        assert result == {"action": "accept", "content": content, "_meta": None}

    def test_mcp_tool_call_accept_with_content(self, handler):
        # content is deliberately NOT gated per tool_name — an accepted
        # mcpToolCall may carry one (pins the builder's documented looseness).
        content = {"note": "why not"}
        result = handler._build_codex_response(
            "mcpToolCall", {"action": "accept", "content": content},
        )
        assert result == {"action": "accept", "content": content, "_meta": None}

    @pytest.mark.parametrize("payload", [
        {"action": "approve"},                                  # unknown action
        {"action": None}, {},                                   # missing action
        {"action": ["accept"]},                                 # unhashable action
        {"action": "accept", "persist": "forever"},             # bad persist value
        {"action": "accept", "persist": ["session"]},           # unhashable persist
        {"action": "decline", "persist": "session"},            # persist without accept
        {"action": "accept", "content": "not-a-dict"},          # non-dict content
    ])
    def test_invalid_payloads(self, handler, payload):
        assert handler._build_codex_response("mcpToolCall", payload) is None

    @pytest.mark.parametrize("tool_name", ["elicitationForm", "elicitationUrl"])
    def test_invalid_payload_routed_through_other_sub_kinds(self, handler, tool_name):
        # Routing pin: the other two sub-kinds go through the same builder
        # and reject invalid payloads the same way.
        assert handler._build_codex_response(tool_name, {"action": "approve"}) is None

    def test_persist_rejected_outside_mcp_tool_call(self, handler):
        assert handler._build_codex_response(
            "elicitationForm", {"action": "accept", "persist": "session"},
        ) is None


class TestBuildRequestUserInputResponse:
    def test_valid_answers(self, handler):
        answers = {"q1": {"answers": ["Allow"]}, "q2": {"answers": ["a", "b"]}}
        result = handler._build_codex_response(
            "toolRequestUserInput", {"answers": answers},
        )
        assert result == {"answers": answers}

    def test_empty_answers_valid(self, handler):
        assert handler._build_codex_response(
            "toolRequestUserInput", {"answers": {}},
        ) == {"answers": {}}

    def test_extra_entry_keys_stripped(self, handler):
        result = handler._build_codex_response(
            "toolRequestUserInput", {"answers": {"q1": {"answers": ["a"], "extra": 9}}},
        )
        assert result == {"answers": {"q1": {"answers": ["a"]}}}

    @pytest.mark.parametrize("answers", [
        None, "nope", ["list"],                       # not a dict
        {"q1": "Allow"},                              # entry not a dict
        {"q1": {}},                                   # entry missing "answers" key
        {"q1": {"answers": "Allow"}},                 # answers not a list
        {"q1": {"answers": [1, 2]}},                  # non-string answers
    ])
    def test_invalid_answers(self, handler, answers):
        assert handler._build_codex_response(
            "toolRequestUserInput", {"answers": answers},
        ) is None


class TestSafeDefaultsNewMethods:
    @pytest.mark.parametrize("tool_name", ["mcpToolCall", "elicitationForm", "elicitationUrl"])
    def test_elicitation_default(self, handler, tool_name):
        assert handler._safe_default_for(tool_name) == {
            "action": "cancel", "content": None, "_meta": None,
        }

    def test_request_user_input_default(self, handler):
        assert handler._safe_default_for("toolRequestUserInput") == {"answers": {}}


class TestTheToolNameGate:
    """`tool_name` must be a string, and every real one must still get through.

    The dispatch tests it against sets, so a membership test on an unhashable
    value raises out of the WebSocket consumer: the connection drops and the
    pending request stays unresolved, the agent blocked with nothing left to
    unblock it. Refusing too much is the mirror failure and just as bad — no
    approval, elicitation or question answered in the web UI would ever reach
    the agent — so both directions are pinned here.
    """

    def _dispatch(self, handler, content):
        """Run the handler with a mocked manager; return it for inspection."""
        import asyncio
        from unittest.mock import AsyncMock, MagicMock, patch

        manager = MagicMock()
        manager.resolve_pending_request = AsyncMock(return_value=True)
        registry = MagicMock()
        registry.get.return_value = manager
        with patch("twicc.providers.codex.ws.ensure_provider_running"), \
             patch("twicc.providers.codex.ws.get_agent_manager_registry",
                   return_value=registry):
            asyncio.run(handler._handle_pending_request_response(content))
        return manager

    @pytest.mark.parametrize("tool_name", [["mcpToolCall"], {"a": 1}, 42, None, ""])
    def test_a_non_string_tool_name_is_refused_rather_than_raising(self, handler, tool_name):
        manager = self._dispatch(handler, {
            "session_id": "s-1", "request_id": "r-1", "tool_name": tool_name,
        })

        manager.resolve_pending_request.assert_not_awaited()

    @pytest.mark.parametrize("tool_name,content", [
        ("commandExecution", {"decision": "accept"}),
        ("fileChange", {"decision": "decline"}),
        ("permissions", {"permissions": [], "scope": "session"}),
        ("elicitationForm", {"action": "decline"}),
        ("toolRequestUserInput", {"answers": {}}),
        ("autoReviewDenial", {"decision": "accept"}),
    ])
    def test_a_real_tool_name_still_reaches_the_agent(self, handler, tool_name, content):
        manager = self._dispatch(handler, {
            "session_id": "s-1", "request_id": "r-1", "tool_name": tool_name, **content,
        })

        manager.resolve_pending_request.assert_awaited_once()
        assert manager.resolve_pending_request.await_args.args[2] is not None

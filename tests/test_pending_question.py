"""The per-provider pending-question translation.

Two directions per provider: describing a pending request for
``session <ID> pending-request``, and turning an answer into the wire response
the agent receives. The filter that decides which pending requests are
answerable is shared, so it is tested once per provider payload shape rather
than once per provider module.

Design: ``docs/plans/2026-09-18-question-cli-design.md``.
"""

from __future__ import annotations

import time

import pytest

from claude_agent_sdk.types import PermissionResultAllow, PermissionResultDeny

from twicc.agent.states import PendingRequest
from twicc.core.enums import Provider
from twicc.providers.claude_code import pending_question as claude_pq
from twicc.providers.codex import pending_question as codex_pq
from twicc.providers.helpers import get_provider_helpers


def claude_question(questions=None, **overrides) -> PendingRequest:
    fields = {
        "request_id": "req-claude",
        "request_type": "ask_user_question",
        "tool_name": "AskUserQuestion",
        "tool_input": {"questions": questions if questions is not None else [
            {"question": "Which database?", "header": "Database",
             "options": [{"label": "PostgreSQL", "description": "Reliable"},
                         {"label": "SQLite", "description": "Lightweight"}]},
        ]},
        "created_at": time.time(),
    }
    fields.update(overrides)
    return PendingRequest(**fields)


def codex_question(questions=None, **overrides) -> PendingRequest:
    fields = {
        "request_id": "req-codex",
        "request_type": "ask_user_question",
        "tool_name": "toolRequestUserInput",
        "tool_input": {"questions": questions if questions is not None else [
            {"id": "db_choice", "header": "Database", "question": "Which database?",
             "isOther": False, "isSecret": False,
             "options": [{"label": "PostgreSQL", "description": "Reliable"},
                         {"label": "SQLite", "description": "Lightweight"}]},
        ]},
        "created_at": time.time(),
    }
    fields.update(overrides)
    return PendingRequest(**fields)


class TestTheFilter:
    def test_a_claude_question_is_answerable(self):
        assert claude_pq.normalize_pending_request(claude_question())["kind"] == "question"

    def test_a_codex_question_is_answerable(self):
        assert codex_pq.normalize_pending_request(codex_question())["kind"] == "question"

    def test_a_tool_approval_is_out_of_scope(self):
        pending = claude_question(request_type="tool_approval", tool_name="Bash",
                                  tool_input={"command": "ls"})
        entry = claude_pq.normalize_pending_request(pending)
        assert entry["kind"] == "out_of_scope"
        assert entry["reason"] == "tool_approval"

    def test_an_elicitation_is_out_of_scope_despite_its_request_type(self):
        # It carries ``ask_user_question`` too, which is why the filter cannot
        # be on ``request_type`` alone.
        pending = claude_question(tool_name="elicitationForm", tool_input={})
        entry = claude_pq.normalize_pending_request(pending)
        assert entry["kind"] == "out_of_scope"
        assert entry["reason"] == "elicitation"

    def test_a_degraded_hybrid_question_is_out_of_scope(self):
        # The GUI-expiry rewrite changes the request_type and leaves the
        # tool_name intact, which is why the filter cannot be on tool_name alone.
        pending = claude_question(request_type="hybrid_terminal")
        entry = claude_pq.normalize_pending_request(pending)
        assert entry["kind"] == "out_of_scope"
        assert entry["reason"] == "terminal_only"

    def test_a_codex_plan_prompt_is_out_of_scope(self):
        pending = codex_question(request_type="tool_approval", tool_name="planImplementation",
                                 tool_input={})
        assert codex_pq.normalize_pending_request(pending)["reason"] == "choice"


class TestTheDisguisedMcpApproval:
    def test_the_prefix_excludes_it(self):
        pending = codex_question([
            {"id": "mcp_tool_call_approval_call-1", "question": "Allow this tool?",
             "options": [{"label": "Allow"}, {"label": "Cancel"}]},
        ])
        entry = codex_pq.normalize_pending_request(pending)
        assert entry["kind"] == "out_of_scope"
        assert entry["reason"] == "mcp_tool_approval"

    @pytest.mark.parametrize("questions", [
        "not a list",
        [],
        ["not a dict"],
        [{"id": 42}],
        [{"question": "no id at all"}],
    ])
    def test_a_pathological_payload_does_not_crash(self, questions):
        # The rule's own three guards — a list with nothing in it, a first
        # entry that is not a dict, an id that is not a string — plus a
        # question with no id at all. (Its fourth guard, "is this a list",
        # now lives in the shared reader and has its own test.) None of these
        # is a disguised approval, so none is reported out of scope — which is
        # a different claim from being answerable: a question with no id is
        # cancel-only, and that rule has its own tests.
        entry = codex_pq.normalize_pending_request(codex_question(questions))
        assert entry["kind"] == "question"

    def test_a_claude_question_is_never_a_disguised_approval(self):
        # The rule is Codex-only: an AskUserQuestion has no ids at all.
        pending = claude_question([
            {"id": "mcp_tool_call_approval_x", "question": "Which database?"},
        ])
        assert claude_pq.normalize_pending_request(pending)["kind"] == "question"


class TestTheStoredQuestionsReader:
    """One reader for both providers, and it trusts nothing it is handed."""

    @pytest.mark.parametrize("tool_input", [
        "a json string", ["a", "list"], 42, None,
    ])
    def test_a_tool_input_that_is_not_a_dict(self, tool_input):
        # The hybrid hook path fills ``tool_input`` from a file it does not own,
        # so a truthy non-dict really can reach here. The widget path runs inside
        # the WebSocket consumer, where a raise drops the connection.
        pending = claude_question(tool_input=tool_input)
        assert claude_pq.normalize_pending_request(pending)["questions"] == []
        assert codex_pq.normalize_pending_request(
            codex_question(tool_input=tool_input))["questions"] == []

    # ``None`` is the fixture's "use the default", so it cannot be passed here.
    @pytest.mark.parametrize("questions", ["not a list", 42, {"a": 1}])
    def test_a_questions_field_that_is_not_a_list(self, questions):
        assert claude_pq.normalize_pending_request(
            claude_question(questions))["questions"] == []
        assert codex_pq.normalize_pending_request(
            codex_question(questions))["questions"] == []



class TestTheEntryShape:
    def test_claude_publishes_one_based_indexes(self):
        entry = claude_pq.normalize_pending_request(claude_question([
            {"question": "First?", "header": "A", "options": []},
            {"question": "Second?", "header": "B", "options": [], "multiSelect": True},
        ]))
        assert [q["id"] for q in entry["questions"]] == ["1", "2"]
        assert entry["questions"][0]["multi_select"] is False
        assert entry["questions"][1]["multi_select"] is True
        # "Other" is unconditional in the widget, and Claude has no secrets.
        assert all(q["allows_free_text"] is True for q in entry["questions"])
        assert all(q["secret"] is False for q in entry["questions"])

    def test_codex_publishes_native_ids(self):
        entry = codex_pq.normalize_pending_request(codex_question())
        question = entry["questions"][0]
        assert question["id"] == "db_choice"
        assert question["multi_select"] is False
        assert question["allows_free_text"] is False
        assert question["options"] == [
            {"label": "PostgreSQL", "description": "Reliable"},
            {"label": "SQLite", "description": "Lightweight"},
        ]

    def test_codex_free_text_follows_is_other(self):
        entry = codex_pq.normalize_pending_request(codex_question([
            {"id": "q1", "question": "Why?", "isOther": True, "options": [{"label": "A"}]},
        ]))
        assert entry["questions"][0]["allows_free_text"] is True

    def test_a_non_dict_option_is_dropped_not_raised(self):
        entry = codex_pq.normalize_pending_request(codex_question([
            {"id": "q1", "question": "Why?", "options": ["not a dict", {"label": "A"}]},
        ]))
        assert entry["questions"][0]["options"] == [{"label": "A", "description": ""}]

    def test_codex_free_text_when_a_question_has_no_options(self):
        entry = codex_pq.normalize_pending_request(codex_question([
            {"id": "q1", "question": "Why?", "options": None},
        ]))
        assert entry["questions"][0]["allows_free_text"] is True

    def test_the_created_at_becomes_iso_with_an_age(self):
        pending = claude_question(created_at=time.time() - 42)
        entry = claude_pq.normalize_pending_request(pending)
        assert entry["created_at"].startswith("20")
        assert entry["created_at"].endswith("+00:00")
        assert 41 <= entry["age_seconds"] <= 44

    def test_an_out_of_scope_entry_carries_no_age(self):
        pending = claude_question(request_type="tool_approval", tool_name="Bash",
                                  tool_input={"command": "ls"})
        entry = claude_pq.normalize_pending_request(pending)
        assert set(entry) == {"request_id", "created_at", "kind", "reason",
                              "tool_name", "actions"}
        assert entry["actions"] == []

    def test_raw_covers_a_question_entry(self):
        pending = claude_question()
        entry = claude_pq.normalize_pending_request(pending, raw=True)
        assert entry["raw"] == {"tool_input": pending.tool_input}

    def test_raw_covers_an_out_of_scope_entry_too(self):
        pending = claude_question(request_type="tool_approval", tool_name="Bash",
                                  tool_input={"command": "ls"})
        entry = claude_pq.normalize_pending_request(pending, raw=True)
        assert entry["raw"] == {"tool_input": {"command": "ls"}}


class TestTheAdvertisedActions:
    def test_an_ordinary_question_offers_both(self):
        entry = claude_pq.normalize_pending_request(claude_question())
        assert [a["action"] for a in entry["actions"]] == ["answer", "cancel"]
        assert entry["actions"][0]["accepts"] == ["--answer"]
        # ``accepts`` lists the answer-carrying flags only: ``--request-id`` and
        # ``--timeout`` are accepted by every action, so a targeted cancel stays
        # expressible when two questions are pending.
        assert "accepts" not in entry["actions"][1]

    def test_an_empty_question_list_offers_cancel_alone(self):
        entry = claude_pq.normalize_pending_request(claude_question([]))
        assert entry["kind"] == "question"
        assert [a["action"] for a in entry["actions"]] == ["cancel"]

    def test_two_questions_sharing_one_id_offer_cancel_alone(self):
        # A caller names a question by its id, so a set that cannot be named one
        # by one can never be answered in full — whatever the caller does.
        entry = codex_pq.normalize_pending_request(codex_question([
            {"id": "db", "question": "Which database?", "options": [{"label": "A"}]},
            {"id": "db", "question": "Which one really?", "options": [{"label": "B"}]},
        ]))
        assert [a["action"] for a in entry["actions"]] == ["cancel"]

    def test_a_non_string_codex_id_offers_cancel_alone(self):
        # Publishing an integer id verbatim would advertise an answer the CLI
        # could never express: `--answer 42=x` is the id "42", a different key.
        entry = codex_pq.normalize_pending_request(codex_question([
            {"id": 42, "question": "Which database?", "options": [{"label": "A"}]},
        ]))
        assert entry["questions"][0]["id"] == ""
        assert [a["action"] for a in entry["actions"]] == ["cancel"]

    def test_a_secret_question_offers_cancel_alone(self):
        entry = codex_pq.normalize_pending_request(codex_question([
            {"id": "q1", "question": "Token?", "isSecret": True, "options": [{"label": "A"}]},
        ]))
        assert [a["action"] for a in entry["actions"]] == ["cancel"]


class TestTheClaudeTranslator:
    def test_submit_allows_with_the_stored_questions(self):
        pending = claude_question()
        response = claude_pq.build_question_response(
            pending, action="submit", answers={"1": ["PostgreSQL"]})
        assert isinstance(response, PermissionResultAllow)
        assert response.updated_input == {
            "questions": pending.tool_input["questions"],
            "answers": {"Which database?": "PostgreSQL"},
        }

    def test_multiple_values_join_the_way_the_widget_joins_them(self):
        pending = claude_question([
            {"question": "Which caches?", "header": "Cache", "multiSelect": True,
             "options": [{"label": "Redis"}, {"label": "Memcached"}]},
        ])
        response = claude_pq.build_question_response(
            pending, action="submit", answers={"1": ["Redis", "Memcached"]})
        assert response.updated_input["answers"] == {"Which caches?": "Redis, Memcached"}

    def test_partial_denies_with_the_native_clarify_text(self):
        pending = claude_question([
            {"question": "Which database?", "header": "Database", "options": []},
            {"question": "Which cache?", "header": "Cache", "options": []},
        ])
        response = claude_pq.build_question_response(
            pending, action="partial", answers={"1": ["PostgreSQL"]})
        assert isinstance(response, PermissionResultDeny)
        assert response.message == (
            "The user wants to clarify these questions.\n"
            "    This means they may have additional information, context or questions for you.\n"
            "    Take their response into account and then reformulate the questions if appropriate.\n"
            "    Start by asking them what they would like to clarify.\n"
            "\n"
            "    Questions asked:\n"
            '- "Which database?"\n'
            "  Answer: PostgreSQL\n"
            '- "Which cache?"\n'
            "  (No answer provided)"
        )

    def test_cancel_denies_with_the_fixed_text(self):
        response = claude_pq.build_question_response(
            claude_question(), action="cancel", answers={})
        assert isinstance(response, PermissionResultDeny)
        assert response.message == claude_pq.QUESTION_CANCEL_MESSAGE

    def test_an_unknown_action_is_a_programming_error(self):
        with pytest.raises(ValueError):
            claude_pq.build_question_response(claude_question(), action="nope", answers={})


class TestTheWidgetEntryPoint:
    """The web UI's own payload, which must reach the agent untouched."""

    def test_the_widget_answers_are_forwarded_verbatim(self):
        # The widget pre-joins a multi-select answer, so its map already is what
        # the agent receives. Re-deriving it would only add a chance to lose it.
        pending = claude_question([{"question": "Which caches?", "multiSelect": True,
                                    "options": []}])
        ui_answers = {"Which caches?": "Redis, Memcached"}
        response = claude_pq.build_question_response_from_ui(
            pending, action="submit", ui_answers=ui_answers)
        assert response.updated_input["answers"] == ui_answers

    def test_the_stored_questions_are_forwarded_verbatim(self):
        # Including an entry no normalizer could read: the agent asked with this
        # list and must get this list back.
        pending = claude_question([{"question": "Which database?"}, "not a dict"])
        response = claude_pq.build_question_response_from_ui(
            pending, action="submit", ui_answers={"Which database?": "SQLite"})
        assert response.updated_input["questions"] == pending.tool_input["questions"]

    @pytest.mark.parametrize("malformed", [["PostgreSQL"], "PostgreSQL", 42, None])
    def test_a_malformed_answers_payload_never_raises(self, malformed):
        # This runs inside the WebSocket consumer: an exception there drops the
        # connection and leaves the request pending forever.
        response = claude_pq.build_question_response_from_ui(
            claude_question(), action="submit", ui_answers=malformed)
        assert response.updated_input["answers"] == {}


class TestPathologicalQuestions:
    def test_a_malformed_entry_keeps_its_slot(self):
        # Dropping it would renumber every question after it, so an answer to
        # question 2 would reach question 3.
        entry = claude_pq.normalize_pending_request(claude_question([
            "not a dict",
            {"question": "Which cache?", "options": [{"label": "Redis"}]},
        ]))
        assert len(entry["questions"]) == 2
        assert entry["questions"][1]["id"] == "2"
        assert entry["questions"][1]["question"] == "Which cache?"

    def test_answering_by_index_reaches_the_right_question(self):
        pending = claude_question(["not a dict", {"question": "Which cache?"}])
        response = claude_pq.build_question_response(
            pending, action="partial", answers={"2": ["Redis"]})
        assert "  Answer: Redis" in response.message

    def test_an_unreadable_question_publishes_no_id(self):
        # Nothing can target it, so the request can never be answered in full.
        entry = claude_pq.normalize_pending_request(claude_question([
            "not a dict", {"question": "Which cache?"},
        ]))
        assert [q["id"] for q in entry["questions"]] == ["", "2"]

    def test_an_unreadable_question_makes_the_request_cancel_only(self):
        # Advertising ``answer`` here would be a lie a script would act on: the
        # answer would be accepted, counted, then silently dropped.
        entry = claude_pq.normalize_pending_request(claude_question([
            "not a dict", {"question": "Which cache?"},
        ]))
        assert [a["action"] for a in entry["actions"]] == ["cancel"]

    def test_the_same_holds_on_codex(self):
        entry = codex_pq.normalize_pending_request(codex_question([
            {"id": "cache", "question": "Which cache?"}, "not a dict",
        ]))
        assert [q["id"] for q in entry["questions"]] == ["cache", ""]
        assert [a["action"] for a in entry["actions"]] == ["cancel"]

    def test_a_non_string_question_text_is_unreadable_too(self):
        # The map the agent receives is keyed by that text, so an answer to it
        # could be accepted, counted, then dropped — the request submitting as
        # complete with one answer missing.
        entry = claude_pq.normalize_pending_request(claude_question([
            {"question": ["a", "b"], "options": []},
            {"question": "Which cache?", "options": []},
        ]))
        assert [q["id"] for q in entry["questions"]] == ["", "2"]
        assert [a["action"] for a in entry["actions"]] == ["cancel"]

    def test_a_non_string_question_text_never_raises_on_the_widget_path(self):
        # The text is used as a dict key when the clarify message is built, and
        # an unhashable one raises. That runs inside the WebSocket consumer.
        pending = claude_question([{"question": ["a", "b"], "options": []}])
        response = claude_pq.build_question_response_from_ui(
            pending, action="partial", ui_answers={"anything": "X"})
        assert "(No answer provided)" in response.message

    def test_an_id_naming_an_unreadable_slot_is_dropped_not_raised(self):
        # The service refuses it upstream; this is the second guard, and it runs
        # inside the WebSocket consumer on the widget's behalf.
        pending = claude_question(["not a dict"])
        response = claude_pq.build_question_response(
            pending, action="partial", answers={"1": ["Redis"]})
        assert "  (No answer provided)" in response.message


class TestTheCodexTranslator:
    def test_submit_keys_by_the_native_id(self):
        response = codex_pq.build_question_response(
            codex_question(), action="submit", answers={"db_choice": ["PostgreSQL"]})
        assert response == {"answers": {"db_choice": {"answers": ["PostgreSQL"]}}}

    def test_cancel_is_an_empty_answer_map(self):
        assert codex_pq.build_question_response(
            codex_question(), action="cancel", answers={}) == {"answers": {}}

    def test_partial_is_a_programming_error(self):
        # A partially answered Codex request is ``missing_answers``; the caller
        # refuses it before reaching the translator.
        with pytest.raises(ValueError):
            codex_pq.build_question_response(
                codex_question(), action="partial", answers={"db_choice": ["PostgreSQL"]})


class TestTheProviderDispatch:
    """Without these overrides the base defaults raise and nothing works."""

    def test_claude_helpers_reach_their_module(self):
        helpers = get_provider_helpers(Provider.CLAUDE_CODE)
        assert helpers.normalize_pending_request(claude_question())["kind"] == "question"
        assert isinstance(
            helpers.build_question_response(claude_question(), action="cancel", answers={}),
            PermissionResultDeny,
        )

    def test_codex_helpers_reach_their_module(self):
        helpers = get_provider_helpers(Provider.CODEX)
        assert helpers.normalize_pending_request(codex_question())["kind"] == "question"
        assert helpers.build_question_response(
            codex_question(), action="cancel", answers={}) == {"answers": {}}

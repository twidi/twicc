"""Pure async-question protocol and durable source-timeline projection.

A submission boundary contains ``at``, optional canonical ``line``, immutable
``batch_ids`` and ``settled_turn_ids``, and optional ``group_ids``. Runtime
ownership overrides historical turn-end inference. Facts remain JSON-compatible
so compute processes can return them without writing session state.
"""

from __future__ import annotations

import re
from copy import deepcopy
from datetime import datetime
from functools import cmp_to_key
from typing import NamedTuple


class AsyncQuestion(NamedTuple):
    index: int
    title: str
    options: list[str]


class QuestionFact(NamedTuple):
    key: str
    kind: str
    at: str
    turn_id: str | None
    item_id: str | None
    line: int | None
    data: dict


def _identifier(value: object) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


def normalize_questions(raw: object) -> list[AsyncQuestion]:
    """Keep original question indexes and source text; discard invalid entries."""
    if not isinstance(raw, list):
        return []
    questions = []
    for index, entry in enumerate(raw):
        if not isinstance(entry, dict) or _identifier(entry.get("title")) is None:
            continue
        options = []
        raw_options = entry.get("options")
        if isinstance(raw_options, list):
            for option in raw_options:
                if _identifier(option) is not None and option not in options:
                    options.append(option)
        questions.append(AsyncQuestion(index, entry["title"], options))
    return questions


def question_fact(record: dict, *, source: str, at: str, line: int | None = None) -> QuestionFact | None:
    """Normalize completed SDK/canonical questions and basic lifecycle evidence.

    Historical provenance and continuation reconstruction belong to the caller.
    A raw SDK item is accepted when its caller already establishes completion.
    SDK notifications must explicitly be ``item/completed``.
    """
    if not isinstance(record, dict):
        return None
    if "method" in record:
        payload = record.get("params")
        if not isinstance(payload, dict):
            return None
        method = record["method"]
        if method == "turn/completed":
            turn = payload.get("turn")
            turn_id = _identifier(turn.get("id")) if isinstance(turn, dict) else None
            turn_id = turn_id or _identifier(payload.get("turnId"))
            if turn_id:
                return QuestionFact(f"end:{turn_id}", "turn_end", at, turn_id, None, line, {"source": source})
            return None
        if method != "item/completed":
            return None
        item = payload.get("item")
    elif record.get("type") == "event_msg":
        payload = record.get("payload")
        if not isinstance(payload, dict):
            return None
        if payload.get("type") in {"task_complete", "task_completed", "turn_completed"}:
            turn_id = _identifier(payload.get("turn_id"))
            if turn_id:
                return QuestionFact(f"end:{turn_id}", "turn_end", at, turn_id, None, line, {"source": source})
            return None
        if payload.get("type") != "item_completed":
            return None
        item = payload.get("item")
    else:
        item = record
        payload = record
    if not isinstance(item, dict):
        return None
    item_id = _identifier(item.get("id"))
    if item_id is None:
        return None
    turn_id = _identifier(payload.get("turn_id")) or _identifier(payload.get("turnId"))
    if item.get("type") in {"UserMessage", "userMessage"}:
        data = {"source": source, "origin": "human", "status": "accepted", "source_item_id": item_id}
        client_id = _identifier(item.get("clientId")) or _identifier(item.get("client_id"))
        text = "".join(
            entry.get("text", "")
            for entry in item.get("content", [])
            if isinstance(entry, dict) and isinstance(entry.get("text"), str)
        ).strip()
        if text.startswith(("<twicc-resume>", '<codex_internal_context source="goal">')) or text in {
            "/compact",
            "/plan",
            "/goal clear",
            "Retry the exact action I just approved.",
        }:
            data["origin"] = "internal"
        elif text.startswith("Message Type:") and "\nSender:" in text:
            data["origin"] = "agent"
        if client_id:
            data["client_message_id"] = client_id
        return QuestionFact(f"user:{item_id}", "user_submission", at, turn_id, item_id, line, data)
    if item.get("type") not in {"AgentMessage", "agentMessage"} or item.get("delivery") != "async":
        return None
    questions = normalize_questions(item.get("questions"))
    if not questions:
        return None
    return QuestionFact(
        f"question:{item_id}",
        "question",
        at,
        turn_id,
        item_id,
        line,
        {"source": source, "questions": [question._asdict() for question in questions]},
    )


def extract_async_question_facts(record: dict, *, line: int) -> list[QuestionFact]:
    """Extract canonical evidence before display rewrites; ignore legacy copies.

    ``history_context`` retains source turn starts and goal state. Its keys use
    semantic IDs and occurrence timestamps, never the mutable rollout line.
    """
    from .canonical import user_message_text
    from .compute import _goal_context_objective, _goal_snapshot_from_tool_result

    at = record.get("timestamp")
    if not isinstance(at, str):
        return []
    payload = record.get("payload") or {}
    turn_id = payload.get("turn_id") or record.get("turn_id")
    fact = question_fact(record, source="jsonl", at=at, line=line)
    facts = []
    if fact is not None:
        if fact.kind == "user_submission":
            text = (user_message_text(record) or "").strip()
            original = record.get("twiccOriginalContent")
            internal = (
                isinstance(original, dict)
                or text.startswith(("<twicc-resume>", '<codex_internal_context source="goal">'))
                or text in {"/compact", "/plan", "/goal clear", "Retry the exact action I just approved."}
            )
            agent = text.startswith("Message Type:") and "\nSender:" in text
            data = {**fact.data, "origin": "agent" if agent else "internal" if internal else "human"}
            if text.startswith("<twicc-resume>") or text == "Retry the exact action I just approved.":
                data["internal_successor"] = True
            fact = fact._replace(data=data)
        facts.append(fact)
    goal = _goal_snapshot_from_tool_result(record)
    if payload.get("type") == "thread_goal_updated":
        goal = payload.get("goal")
    goal_context = _goal_context_objective(record)
    context = None
    if isinstance(goal, dict) and isinstance(goal.get("status"), str):
        context = {"event": "goal", "status": goal["status"], "goal_id": goal.get("id")}
    elif goal_context is not None:
        context = {"event": "goal", "status": "active", "goal_id": None, "internal_successor": True}
    elif payload.get("type") == "task_started" and turn_id:
        context = {"event": "turn_start"}
    elif (user_message_text(record) or "").strip() == "/goal clear":
        context = {"event": "goal", "status": "cleared", "goal_id": None}
    if context is not None:
        identity = context.get("goal_id") or turn_id or at
        suffix = context.get("status", "")
        facts.append(
            QuestionFact(
                f"history:{context['event']}:{identity}:{suffix}:{at}",
                "history_context",
                at,
                turn_id,
                None,
                line,
                {"source": "jsonl", **context},
            )
        )
    # Abort records are source terminal evidence, including interrupted goals.
    if payload.get("type") == "turn_aborted" and turn_id:
        facts.append(
            QuestionFact(
                f"end:{turn_id}", "turn_end", at, turn_id, None, line, {"source": "jsonl", "outcome": "interrupted"}
            )
        )
    return facts


def _reconstruct_question_history(stored: dict) -> list[dict]:
    """Derive fallback links from all source evidence without rewriting facts.

    Generated goal returns never override a runtime owner. Rebuilding this view
    on every merge avoids retaining stale inferred links after line remapping.
    """
    ordered = _ordered(deepcopy(list(stored.values())))
    owned_turns = set()
    for fact in ordered:
        if fact["kind"] in {"live_owner", "settlement_decision"}:
            owned_turns.update(
                filter(
                    None, (fact.get("turn_id"), fact["data"].get("root_turn_id"), fact["data"].get("successor_turn_id"))
                )
            )
    goal_turns, goal_active, last_end = [], False, None
    goal_terminal = None
    current_turn = None
    derived = []

    def finish_goal():
        if not goal_turns or goal_terminal is None or any(turn in owned_turns for turn in goal_turns):
            return
        if any(
            fact["kind"] == "live_owner"
            and f"return:{fact['data']['group_id']}" not in stored
            and _compare(goal_terminal, fact) >= 0
            for fact in ordered
        ):
            return
        ends = {fact["turn_id"]: fact for fact in ordered if fact["kind"] == "turn_end"}
        if any(turn not in ends for turn in goal_turns):
            return
        terminal = _ordered([goal_terminal, *(ends[turn] for turn in goal_turns)])[-1]
        group = f"history-goal:{goal_turns[0]}"
        derived.append(
            {
                **terminal,
                "key": f"return:{group}",
                "kind": "control_return",
                "data": {"group_id": group, "turn_ids": list(goal_turns), "source": "history", "source_boundary": True},
            }
        )

    for fact in ordered:
        data, turn = fact["data"], fact.get("turn_id")
        if fact["kind"] == "history_context" and data["event"] == "turn_start":
            current_turn = turn
        if fact["kind"] == "history_context" and data["event"] == "goal":
            if data["status"] == "active":
                was_active = goal_active
                if not goal_active:
                    finish_goal()
                    goal_turns, goal_terminal = [], None
                goal_active = True
                if data.get("internal_successor"):
                    turn = turn or current_turn
                    preceding = last_end if not was_active else None
                    for member in (preceding, turn):
                        if member and member not in goal_turns:
                            goal_turns.append(member)
                    for previous in ordered:
                        if previous["kind"] == "turn_end" and previous["turn_id"] == preceding:
                            previous["data"]["goal_active"] = True
            else:
                goal_active = False
                goal_terminal = fact
        if turn and (goal_active or (goal_terminal is not None and turn in goal_turns)):
            if turn not in goal_turns:
                goal_turns.append(turn)
            if fact["kind"] == "turn_end":
                data["goal_active"] = True
                if data.get("outcome") == "interrupted":
                    goal_active, goal_terminal = False, fact
        if (
            fact["kind"] == "user_submission"
            and data.get("internal_successor")
            and last_end
            and turn not in owned_turns
            and last_end not in owned_turns
        ):
            data["continuation_from_turn_id"] = last_end
        if fact["kind"] == "turn_end":
            last_end = turn
    finish_goal()
    return _ordered([*ordered, *derived])


def _time(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _compare(left: dict, right: dict) -> int:
    """Canonical rollout lines win; source timestamps join facts without lines."""
    left_line, right_line = left.get("line"), right.get("line")
    left_rollout = left.get("data", {}).get("rollout_id")
    right_rollout = right.get("data", {}).get("rollout_id")
    if left_line is not None and right_line is not None and left_rollout == right_rollout:
        return (left_line > right_line) - (left_line < right_line)
    left_at, right_at = _time(left["at"]), _time(right["at"])
    return (left_at > right_at) - (left_at < right_at)


def _ordered(values: list[dict]) -> list[dict]:
    """Anchor SDK facts to ordered source lines without a cyclic comparator.

    Source timestamps can disagree with rollout lines. Pairwise sorting then
    becomes nontransitive. Keep each rollout's line sequence intact and insert
    unlined facts before the first source anchor at or after their timestamp.
    """

    def time_key(value):
        return _time(value["at"]), value.get("key", value.get("item_id", ""))

    rollouts = {}
    unlined = []
    for value in values:
        if value.get("line") is None:
            unlined.append(value)
        else:
            rollouts.setdefault(value.get("data", {}).get("rollout_id"), []).append(value)
    unlined.sort(key=time_key)
    ordered = []
    index = 0
    for rollout in sorted(rollouts.values(), key=lambda entries: min(time_key(entry) for entry in entries)):
        for anchor in sorted(rollout, key=lambda entry: (entry["line"], time_key(entry))):
            while index < len(unlined) and _time(unlined[index]["at"]) <= _time(anchor["at"]):
                ordered.append(unlined[index])
                index += 1
            ordered.append(anchor)
    return ordered + unlined[index:]


def _merge_fact(old: dict, new: dict) -> dict:
    """Enrich source metadata without replacing immutable send boundaries."""
    merged = deepcopy(old)
    old_data, new_data = old["data"], new["data"]
    live_old = old_data.get("origin_source") == "live" or old_data.get("source") not in {"jsonl", "history"}
    live_new = new_data.get("source") not in {"jsonl", "history"}
    merged["data"].update(deepcopy(new_data))
    for field in ("turn_id", "item_id"):
        if new.get(field) is not None:
            merged[field] = new[field]
    if new.get("line") is not None:
        merged["line"] = new["line"]
        merged["at"] = new["at"]
    elif old.get("line") is None and _time(new["at"]) < _time(old["at"]):
        merged["at"] = new["at"]
    if old["kind"] == "question":
        merged["data"]["questions"] = deepcopy(old_data["questions"])
        if old.get("line") is not None and new.get("line") is None:
            merged["data"]["source"] = old_data.get("source")
    if old["kind"] == "user_submission":
        # Accepted evidence is monotonic; a repeated prepared fact cannot undo it.
        rank = {"prepared": 0, "rejected": 1, "uncertain": 2, "accepted": 3}
        old_status, new_status = old_data.get("status"), new_data.get("status")
        if rank.get(old_status, -1) >= rank.get(new_status, -1) and old_status is not None:
            merged["data"]["status"] = old_status
        if live_old and not live_new:
            merged["data"]["origin_source"] = "live"
            for field in ("origin", "boundary", "request_id", "client_message_id", "target_turn_id", "delivery_route"):
                if field in old_data:
                    merged["data"][field] = deepcopy(old_data[field])
        elif "boundary" in old_data and "boundary" in new_data:
            earlier = (
                old_data["boundary"]
                if _compare(old_data["boundary"], new_data["boundary"]) <= 0
                else new_data["boundary"]
            )
            merged["data"]["boundary"] = deepcopy(earlier)
    if old["kind"] == "settlement_decision":
        rank = {"pending": 0, "continuation": 1, "return": 2}
        if rank.get(old_data.get("decision"), -1) > rank.get(new_data.get("decision"), -1):
            merged["data"]["decision"] = old_data["decision"]
        if old_data.get("successor_turn_id") and not new_data.get("successor_turn_id"):
            merged["data"]["successor_turn_id"] = old_data["successor_turn_id"]
    return merged


def _group_evidence(facts: list[dict]):
    """Build turn groups before projecting any batch; never publish half a replay."""
    parents = {}
    explicit_groups = {}

    def root(turn):
        parents.setdefault(turn, turn)
        while parents[turn] != turn:
            turn = parents[turn]
        return turn

    def join(left, right):
        if left and right:
            left_root, right_root = root(left), root(right)
            parents[right_root] = left_root

    for fact in facts:
        data, turn = fact["data"], fact.get("turn_id")
        if turn:
            root(turn)
        if fact["kind"] == "live_owner":
            owner_turn = data.get("root_turn_id") or turn
            if owner_turn:
                explicit_groups[data["group_id"]] = owner_turn
        if fact["kind"] == "settlement_decision":
            decision_turn = data.get("turn_id") or turn
            group_turn = explicit_groups.setdefault(data["group_id"], decision_turn)
            join(group_turn, decision_turn)
            join(decision_turn, data.get("successor_turn_id"))
        if fact["kind"] == "turn_end":
            join(turn, data.get("continuation_turn_id"))
        if fact["kind"] == "user_submission" and data.get("origin") == "internal":
            join(data.get("continuation_from_turn_id"), turn)
        if fact["kind"] == "control_return":
            turns = data.get("turn_ids", [])
            group_turn = explicit_groups.get(data.get("group_id")) or (turns[0] if turns else turn)
            if data.get("group_id") and group_turn:
                explicit_groups[data["group_id"]] = group_turn
            for member in turns:
                join(group_turn, member)
            join(group_turn, turn)
    # Owners can arrive after their decisions. Join their roots after every link exists.
    for fact in facts:
        if fact["kind"] in {"live_owner", "settlement_decision"}:
            data = fact["data"]
            join(explicit_groups.get(data.get("group_id")), data.get("root_turn_id") or fact.get("turn_id"))
    groups = {turn: root(turn) for turn in parents}
    names = {group: root(turn) for group, turn in explicit_groups.items() if turn}
    return groups, names


def _ready_boundaries(facts: list[dict], groups: dict, names: dict) -> dict:
    ends = {fact["turn_id"]: fact for fact in facts if fact["kind"] == "turn_end" and fact["turn_id"]}
    owned = {
        names[fact["data"]["group_id"]]
        for fact in facts
        if fact["kind"] in {"live_owner", "settlement_decision"} and fact["data"].get("group_id") in names
    }
    admissions = [
        fact
        for fact in facts
        if (
            fact["kind"] == "live_owner"
            or (
                fact["kind"] == "settlement_decision"
                and fact["data"].get("decision") == "continuation"
                and not fact["data"].get("successor_turn_id")
            )
        )
        and not any(
            other["kind"] == "control_return" and other["data"].get("group_id") == fact["data"].get("group_id")
            for other in facts
        )
    ]
    ready = {}
    for fact in facts:
        if fact["kind"] != "control_return":
            continue
        data = fact["data"]
        group = names.get(data.get("group_id")) or groups.get(fact.get("turn_id"))
        if group is None:
            continue
        linked_ends = [ended for turn, ended in ends.items() if groups.get(turn) == group]
        terminal_completion = all(turn in ends for turn, member_group in groups.items() if member_group == group)
        # Only terminal source completion can replace runtime return chronology.
        # An interrupted/failed successor can return control without completing.
        boundary = (
            _ordered(linked_ends)[-1]
            if linked_ends and terminal_completion and not data.get("source_boundary")
            else fact
        )
        if group not in ready or _compare(boundary, ready[group]) < 0:
            ready[group] = boundary
    members = {}
    for turn, group in groups.items():
        members.setdefault(group, []).append(turn)
    for group, turns in members.items():
        if group in ready or group in owned or any(turn not in ends for turn in turns):
            continue
        group_ends = [ends[turn] for turn in turns]
        if any(ended["data"].get("goal_active") for ended in group_ends):
            continue
        boundary = _ordered(group_ends)[-1]
        if any(_compare(boundary, admission) >= 0 for admission in admissions):
            continue
        ready[group] = boundary
    return ready


def _submissions(facts: dict) -> list[dict]:
    """Native client IDs prove delivery and prevent a second source boundary."""
    sends = {
        fact["data"].get("request_id", fact["key"][5:]): fact
        for fact in facts.values()
        if fact["kind"] == "user_submission" and fact["key"].startswith("send:")
    }
    linked = set()
    for fact in facts.values():
        if fact["kind"] != "user_submission" or not fact["key"].startswith("user:"):
            continue
        data = fact["data"]
        client_id = data.get("client_message_id") or data.get("request_id")
        matching = sends.get(client_id)
        if matching is None:
            matching = next(
                (
                    send
                    for send in sends.values()
                    if send["data"].get("source_item_id") == fact.get("item_id") and fact.get("item_id")
                ),
                None,
            )
        if matching is not None:
            matching["data"]["source_item_id"] = fact.get("item_id")
            matching["data"]["status"] = "accepted"
            linked.add(fact["key"])
    submissions = []
    uncertain = [send for send in sends.values() if send["data"].get("status") in {"prepared", "uncertain"}]
    for fact in facts.values():
        if fact["kind"] != "user_submission" or fact["key"] in linked:
            continue
        data = fact["data"]
        if data.get("origin") != "human" or data.get("status", "accepted") != "accepted":
            continue
        if fact["key"].startswith("user:") and any(
            (send["data"].get("target_turn_id") in {None, fact.get("turn_id")})
            and _compare(fact, send["data"].get("boundary", send)) >= 0
            for send in uncertain
        ):
            continue
        submissions.append(fact)
    return _ordered(submissions)


def _eligible(question: dict, ready: dict | None, submission: dict, group: str | None, names: dict) -> bool:
    boundary = submission["data"].get("boundary")
    if boundary is not None and question["item_id"] in boundary.get("excluded_batch_ids", []):
        return False
    if boundary is not None and question["item_id"] in boundary.get("batch_ids", []):
        return True
    if ready is None:
        return False
    if boundary is None:
        return _compare(question, submission) <= 0 and _compare(ready, submission) <= 0
    settled = question.get("turn_id") in boundary.get("settled_turn_ids", [])
    settled = settled or any(names.get(name) == group for name in boundary.get("group_ids", []))
    return settled and _compare(question, boundary) <= 0 and _compare(ready, boundary) <= 0


def build_question_boundary(
    state: dict,
    *,
    at: str,
    line: int | None = None,
    batch_ids: list[str] | None = None,
) -> dict:
    """Capture settled source groups, including groups without known questions.

    Explicit membership excludes other known ready batches. Unknown older
    questions remain eligible through the complete settled-turn boundary.
    """
    facts = _reconstruct_question_history(state.get("facts", {}))
    groups, names = _group_evidence(facts)
    readiness = _ready_boundaries(facts, groups, names)
    boundary = {"at": at, "line": line}
    settled = {group for group, ready in readiness.items() if _compare(ready, boundary) <= 0}
    ready_ids = [item_id for item_id, batch in state.get("batches", {}).items() if batch["status"] == "ready"]
    selected = ready_ids if batch_ids is None else list(batch_ids)
    return {
        **boundary,
        "batch_ids": selected,
        "excluded_batch_ids": [item_id for item_id in ready_ids if item_id not in selected],
        "settled_turn_ids": sorted(turn for turn, group in groups.items() if group in settled),
        "group_ids": sorted(name for name, group in names.items() if group in settled),
    }


def reduce_question_state(state: dict, facts: list[QuestionFact]) -> dict:
    """Merge durable facts and derive collecting/ready/sent/dismissed batches."""
    stored = deepcopy(state.get("facts", {}))
    for fact in facts:
        incoming = deepcopy(fact._asdict())
        old = stored.get(fact.key)
        stored[fact.key] = _merge_fact(old, incoming) if old is not None else incoming
    submissions = _submissions(stored)
    ordered = _reconstruct_question_history(stored)
    groups, names = _group_evidence(ordered)
    readiness = _ready_boundaries(ordered, groups, names)
    dismissals = [fact for fact in ordered if fact["kind"] == "dismiss"]
    batches = {}
    for fact in ordered:
        if fact["kind"] != "question":
            continue
        item_id = fact["item_id"]
        group = groups.get(fact.get("turn_id"))
        ready = readiness.get(group)
        status, request_id = ("ready" if ready is not None else "collecting"), None
        resolutions = []
        for submission in submissions:
            if _eligible(fact, ready, submission, group, names):
                boundary = submission["data"].get("boundary", submission)
                resolutions.append(({**submission, "at": boundary["at"], "line": boundary.get("line")}, "sent"))
        for dismissal in dismissals:
            if item_id in dismissal["data"].get("batch_ids", []):
                resolutions.append((dismissal, "dismissed"))
        if resolutions:
            resolution, status = min(resolutions, key=cmp_to_key(lambda left, right: _compare(left[0], right[0])))
            request_id = resolution["data"].get("request_id")
        batches[item_id] = {
            "item_id": item_id,
            "turn_id": fact.get("turn_id"),
            "at": fact["at"],
            "line": fact.get("line"),
            "questions": deepcopy(fact["data"]["questions"]),
            "status": status,
            "resolved_request_id": request_id,
        }
    projection = {"schema": 1, "facts": stored, "batches": batches}
    unchanged = all(state.get(key) == value for key, value in projection.items())
    return {
        "schema": 1,
        "revision": state.get("revision", 0) + (0 if unchanged else 1),
        "facts": stored,
        "batches": batches,
    }


def validate_question_answers(snapshot: dict, response: dict | None) -> list[dict]:
    """Validate immutable membership; a newer snapshot revision alone is valid."""
    if response is None:
        return []
    if not isinstance(response, dict):
        raise ValueError("async_questions_invalid")  # noqa: TRY004 - Public API requires ValueError.
    ids, answers = response.get("batch_ids"), response.get("answers")
    if not isinstance(ids, list) or not isinstance(answers, list):
        raise ValueError("async_questions_invalid")  # noqa: TRY004 - Public API requires ValueError.
    if any(_identifier(item_id) is None for item_id in ids) or len(set(ids)) != len(ids):
        raise ValueError("async_questions_invalid")
    batches = {batch["item_id"]: batch for batch in snapshot.get("batches", [])}
    for item_id in ids:
        if (
            item_id in snapshot.get("resolutions", {})
            or item_id not in batches
            or batches[item_id].get("status") != "ready"
        ):
            raise ValueError("async_questions_stale")
    valid, seen = [], set()
    for answer in answers:
        if not isinstance(answer, dict):
            raise ValueError("async_questions_invalid")  # noqa: TRY004 - Public API requires ValueError.
        item_id, index, kind, value = (answer.get(field) for field in ("item_id", "index", "kind", "value"))
        if not isinstance(item_id, str) or item_id not in ids or type(index) is not int or not isinstance(value, str):
            raise ValueError("async_questions_invalid")
        question = next((entry for entry in batches[item_id]["questions"] if entry["index"] == index), None)
        identity = (item_id, index)
        if question is None or identity in seen or kind not in ("option", "other"):
            raise ValueError("async_questions_invalid")
        seen.add(identity)
        if kind == "option" and value not in question["options"]:
            raise ValueError("async_questions_invalid")
        if value.strip():
            valid.append({"item_id": item_id, "index": index, "kind": kind, "value": value})
    return valid


def format_question_answers(batches: list[dict], answers: list[dict], text: str) -> str:
    """Build one normal message, preserving question, option, and user language."""
    selected = {
        (answer["item_id"], answer["index"]): answer["value"]
        for answer in answers
        if isinstance(answer.get("value"), str) and answer["value"].strip()
    }
    # Snapshots already have source order; explicit source metadata permits sorting.
    ordered = _ordered(batches) if all(batch.get("at") for batch in batches) else batches
    sections = []
    for batch in ordered:
        for question in sorted(batch["questions"], key=lambda entry: entry["index"]):
            value = selected.get((batch["item_id"], question["index"]))
            if value is not None:
                sections.append(f"**Question:** {question['title']}\n\n**Answer:** {value}")
    if not sections:
        return text
    body = "\n\n".join(sections)
    # Match the code-comment generator: use more colons than any body line starts with.
    # Normalize line endings only for scanning, as Markdown does before parsing.
    runs = re.findall(r"^ {0,3}(:{3,})", body.replace("\r\n", "\n").replace("\r", "\n"), re.MULTILINE)
    marker = ":" * (max((len(run) for run in runs), default=2) + 1)
    message = f"{marker} Answers to your questions\n\n{body}\n\n{marker}"
    if text.strip():
        message += f"\n\n{text}"
    return message

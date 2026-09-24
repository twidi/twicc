# `session <SESSION_ID> pending-requests` / `answer-questions` / `cancel-questions`

See what a session is waiting on, then answer or decline its question. MCP tools: `mcp__twicc__session_pending_requests`, `mcp__twicc__session_answer_questions`, `mcp__twicc__session_cancel_questions`. Read `SKILL.md` first: it resolves `$TWICC`.

## Usage

**Always call `pending-requests` first**: `answer-questions` and `cancel-questions` take the ids it returns.

### Pending-requests — what the session is waiting on

```bash
$TWICC session <SESSION_ID> pending-requests [--raw] [--timeout N]
```

Lists **every** pending request, including the ones you cannot answer: a session frozen on a tool approval is still waiting.

```json
{
  "session_id": "...",
  "agent_state": "awaiting_user_input",
  "pending_requests": [
    {
      "request_id": "...", "created_at": "2026-09-18T15:04:11.123456+00:00", "age_seconds": 42.1,
      "kind": "question", "tool_name": "AskUserQuestion",
      "questions": [
        {"id": "1", "header": "Database", "question": "Which database should we use?",
         "multi_select": false, "allows_free_text": true, "secret": false,
         "options": [{"label": "PostgreSQL", "description": "Reliable, feature-rich"},
                     {"label": "SQLite", "description": "Lightweight, file-based"}]}
      ],
      "actions": [
        {"action": "answer-questions", "label": "Answer the questions", "accepts": ["--choice"]},
        {"action": "cancel-questions", "label": "Decline to answer"}
      ]
    }
  ]
}
```

- `kind` — `question`, or `out_of_scope`: something is waiting and it is not yours. An out-of-scope entry has only `request_id`, `created_at`, `kind`, `reason` and `tool_name`, and an empty `actions`. `reason` is `tool_approval`, `mcp_tool_approval`, `elicitation`, `terminal_only` or `choice`.
- `actions[].action` — the sub-command that performs it. **Read it before answering.** A question whose only action is `cancel-questions` cannot be answered here: no questions, a secret one, one without an id, or two sharing an id. The last two earn `missing_answers` on every attempt: `--choice` names questions by id. Cancel it, or answer it in the web UI.
- `--raw` — adds each request's untouched `tool_input`, on every entry (a pending patch comes back whole). **Never needed to answer**: the plain read has the ids, options and flags. Use it only to describe an out-of-scope entry to a human.
- Nothing pending: exit 0, empty list. No agent: exit 0, `agent_state: "dead"`.

### Answer-questions — unblock a waiting session

```bash
$TWICC session <SESSION_ID> answer-questions [--request-id ID] --choice 'ID=VALUE' [--choice 'ID=VALUE' ...]
```

Answers a **question** only. A tool approval or an MCP elicitation is refused (`not_a_question`): the user clears it in the web UI.

- **The id comes from `pending-requests`**: a 1-based index on Claude Code, the native id on Codex.
- `--choice` splits on the **first** `=`, so a value may contain more. Once per question; several times on one id for a multi-select question.
- A value matching no option is free text: accepted when `allows_free_text` is true, and never mixed with options on one question.
- `--request-id` — required when several requests are pending. **Always pass it from a script**: a request cleared between your read and your answer then fails with `request_gone` instead of getting a wrong answer.
- Every question answered: it submits. Some: a partial answer, Claude Code only. None: refused (use `cancel-questions`).
- **A session cannot answer its own question.**

Exit 3 carries the reason: `no_pending_question`, `ambiguous_request`, `not_a_question`, `request_gone`, `agent_dead`, `missing_answers`, `unknown_question_id`, `free_text_not_allowed`, `free_text_exclusive`, `multi_select_unsupported`, `secret_answer_unsupported`, `option_not_accepted`, `self_answer_refused`, `provider_disabled`.

### Cancel-questions — decline a question

```bash
$TWICC session <SESSION_ID> cancel-questions [--request-id ID]
```

Declines the question without answering it: when `actions` offers nothing else, or when no answer fits. `--request-id` as for `answer-questions`.

## Examples

```bash
$TWICC session abc123 pending-requests
$TWICC session abc123 pending-requests --raw
$TWICC session abc123 answer-questions --request-id req-9 --choice '1=PostgreSQL' --choice '2=Redis'
$TWICC session abc123 cancel-questions --request-id req-9
```

## Related commands

- `$TWICC session <ID> wait-reply` — resume the wait once the question is answered. File: `wait-reply.md`.
- `$TWICC send-message <ID>` — refused while a request is pending; goes through once it is answered. Skill: `twicc-send-message`.

## How to present results

1. Say what is waiting and whether you can answer it; quote the question and its options.

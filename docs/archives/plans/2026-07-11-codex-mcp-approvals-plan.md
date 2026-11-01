# Codex MCP Approvals & Elicitations Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Handle the two Codex server requests TwiCC currently drops on the floor — `mcpServer/elicitation/request` (MCP tool-call approvals, generic MCP form elicitations, URL elicitations) and `item/tool/requestUserInput` (generic question forms) — so the user gets a real prompt instead of a silent `"user rejected MCP tool call"`.

**Architecture:** Extend the existing Codex approval bridge (sync↔async monkey-patch on the SDK's `_approval_handler` slot → `PendingRequest` → WS broadcast → frontend form → WS response → `resolve_pending_request` → SDK wire reply). No new plumbing: the two new methods join `APPROVAL_METHODS`, get params-aware `tool_name` resolution, new WS response builders, and four new self-contained frontend body components routed by the existing Codex `PendingRequestBody.vue` dispatcher.

**Tech Stack:** Python (Django backend, vendored `openai_codex` SDK), Vue 3 `<script setup>` + Web Awesome, pytest.

---

## Background — the bug

Session `019f4d27-01dd-7612-8ec4-f9659e71a7ac` (Codex, `read_only` mode, user-configured
`chrome-devtools` MCP server): every `navigate_page` call failed with
`{"Err": "user rejected MCP tool call"}` without the user ever seeing a prompt.

Root cause chain:

1. When Codex needs approval for an MCP tool call, it does **not** emit a dedicated
   approval request. With the `ToolCallMcpElicitation` feature (**enabled by default**,
   `codex-rs/features/src/lib.rs:1113-1116`) it sends the JSON-RPC server request
   `mcpServer/elicitation/request` whose `_meta` carries
   `codex_approval_kind: "mcp_tool_call"`. With the feature disabled it falls back to
   `item/tool/requestUserInput` (`codex-rs/core/src/mcp_tool_call.rs:1270-1328`).
2. TwiCC's bridge only recognises 3 methods (`APPROVAL_METHODS` in
   `src/twicc/providers/codex/agent/approvals.py:38-42`); everything else is delegated
   to the vendored SDK default handler (`src/twicc/providers/codex/agent/agent.py:1584-1595`).
3. The SDK default returns `{}` for unknown methods (`src/openai_codex/client.py:773-779`).
4. `{}` fails to deserialize into `McpServerElicitationRequestResponse`; the app-server
   falls back to **Decline** (`codex-rs/app-server/src/bespoke_event_handling.rs:1720-1757`)
   → `"user rejected MCP tool call"` (`codex-rs/core/src/mcp_tool_call.rs:238`).

Why it never bit before: TwiCC only wires its own `twicc` MCP server, with
`default_tools_approval_mode="approve"` which short-circuits the whole approval check
(`src/twicc/providers/codex/agent/manager.py:736`). User-configured servers in
`~/.codex/config.toml` default to `auto` mode → prompts fire for tools without a
`read_only_hint` annotation. The gap was known and deferred
(`docs/plans/2026-07-06-mcp-server-plan.md`, approvals design
`docs/superpowers/specs/2026-05-14-codex-approvals-design.md` §1.6, §7-Q9).

## Scope

**In scope** — the two server requests, four user-facing sub-kinds:

| wire method | sub-kind | new `tool_name` | `request_type` |
|---|---|---|---|
| `mcpServer/elicitation/request`, `mode=form`, `_meta.codex_approval_kind == "mcp_tool_call"` | MCP tool-call approval | `mcpToolCall` | `tool_approval` |
| `mcpServer/elicitation/request`, `mode=form`, no approval tag | generic MCP form elicitation | `elicitationForm` | `ask_user_question` |
| `mcpServer/elicitation/request`, `mode=url` | URL elicitation | `elicitationUrl` | `ask_user_question` |
| `item/tool/requestUserInput` | generic question form (also the MCP-approval fallback) | `toolRequestUserInput` | `ask_user_question` |

**Out of scope** — still delegated to the SDK default (unchanged behaviour):
`item/tool/call` (dynamic client tools — TwiCC registers none, cannot fire),
`account/chatgptAuthTokens/refresh` (OAuth refresh). The `twicc` MCP server keeps
`default_tools_approval_mode="approve"` (control plane stays prompt-free).

## Wire protocol reference

All shapes verified against the Codex source at `/home/twidi/dev/codex` (matches the
vendored SDK generation) — camelCase on the wire.

### `mcpServer/elicitation/request` params
(`codex-rs/app-server-protocol/src/protocol/v2/mcp.rs:286-301,624-644`)

```jsonc
// mode=form (the `request` struct is serde-flattened into the params)
{
  "threadId": "…",
  "turnId": "…" | null,
  "serverName": "chrome-devtools",
  "mode": "form",
  "_meta": { … } | null,
  "message": "Allow the chrome-devtools MCP server to run tool \"navigate_page\"?",
  "requestedSchema": { "type": "object", "properties": { … }, "required": [ … ] }
}
// mode=url
{
  "threadId": "…", "turnId": "…" | null, "serverName": "…",
  "mode": "url", "_meta": { … } | null,
  "message": "…", "url": "https://…", "elicitationId": "…"
}
```

For an **MCP tool-call approval** (built by `build_mcp_tool_approval_elicitation_request`,
`codex-rs/core/src/mcp_tool_call.rs:1594-1723`): `requestedSchema.properties` is
**empty**, and `_meta` carries (keys from `codex-rs/protocol/src/mcp_approval_meta.rs`):

- `codex_approval_kind: "mcp_tool_call"` — always (the discriminator);
- `persist`: `"session"` | `"always"` | `["session","always"]` — which "remember"
  variants the client may offer (absent → only one-shot accept);
- `tool_title`, `tool_description` — optional;
- `tool_params`: the raw arguments object — optional;
- `tool_params_display`: `[{name, value, displayName}]` — optional pre-rendered params;
- `source: "connector"` + `connector_id/name/description` — Codex-Apps-only, optional.

Note: the underlying MCP tool name is **not** in `_meta` — it is embedded in `message`.

### `mcpServer/elicitation/request` response
(`mcp.rs:675-688`; parsed by `parse_mcp_tool_approval_elicitation_response`,
`mcp_tool_call.rs:1743-1779`)

```jsonc
{ "action": "accept" | "decline" | "cancel",
  "content": { …form values… } | null,      // accept only; null otherwise
  "_meta": { "persist": "session" | "always" } | null }  // accept-with-remember only
```

- Tool-call approval: `accept` → run; `accept` + `_meta.persist="session"` →
  AcceptForSession; `+ "always"` → AcceptAndRemember; `decline` → skip with
  "user rejected MCP tool call"; `cancel` → skip with "user cancelled MCP tool call".
  Both decline and cancel keep the turn alive.
- Generic form: `accept` + `content` = the filled form (flat `{propName: value}`,
  primitives per schema); `decline` / `cancel` per MCP semantics (explicit refusal vs
  dismissal).
- Malformed/missing reply → app-server substitutes `decline`; a turn-transition error →
  `cancel` (`bespoke_event_handling.rs:1720-1757`).

### `requestedSchema` property shapes (generic form rendering)
(`mcp.rs:310-618` — MCP 2025-11-25 `ElicitRequestFormParams`)

Every property is one of (all have optional `title`, `description`, `default`):

- string: `{type:"string", minLength?, maxLength?, format?: "email"|"uri"|"date"|"date-time"}`
- number: `{type:"number"|"integer", minimum?, maximum?}`
- boolean: `{type:"boolean"}`
- enum single-select untitled: `{type:"string", enum:[…]}`
- enum single-select titled: `{type:"string", oneOf:[{const, title}, …]}`
- enum legacy titled: `{type:"string", enum:[…], enumNames?:[…]}`
- enum multi-select: `{type:"array", minItems?, maxItems?, items:{type:"string", enum:[…]}}`
  or titled `items:{anyOf:[{const,title},…]}` (`oneOf` accepted as alias of `anyOf`)

`content` values on accept: string / number / boolean / array-of-strings, keyed by
property name. Top-level `required: [propName, …]` is optional.

### `item/tool/requestUserInput` params / response
(`codex-rs/app-server-protocol/src/protocol/v2/item.rs:1405-1454`)

```jsonc
// params
{ "threadId": "…", "turnId": "…", "itemId": "…",
  "questions": [
    { "id": "…", "header": "…", "question": "…",
      "isOther": false, "isSecret": false,
      "options": [ { "label": "…", "description": "…" }, … ] | null } ] }
// response
{ "answers": { "<questionId>": { "answers": ["<label or free text>", …] }, … } }
```

- `isOther: true` → offer a free-text alternative alongside `options`;
  `options: null` → pure free-text; `isSecret` → mask the input.
- MCP-approval fallback (`build_mcp_tool_approval_question`, `mcp_tool_call.rs:1529-1573`):
  one question, id `mcp_tool_call_approval_<call_id>`, options among exactly
  `"Allow"`, `"Allow for this session"`, `"Allow and don't ask me again"`, `"Cancel"`.
  The parser matches those **label strings verbatim** (`parse_mcp_tool_approval_response`,
  `mcp_tool_call.rs:1808-1845`) — generic label passthrough is all we need. Missing/empty
  answer → Cancel.

## Design decisions

1. **Recognition lives in `approvals.py`.** The two methods join `APPROVAL_METHODS`;
   a new pure `resolve_tool_name(method, params)` refines the elicitation into its 3
   sub-kinds. `PendingRequest.request_type` becomes `ask_user_question` for the
   question-like sub-kinds so the shared form header ("Codex needs your input" vs
   "Tool approval requested") adapts for free
   (`frontend/src/components/message/PendingRequestForm.vue:152-158`).
2. **Teardown defaults**: elicitation → `{"action": "cancel"}` (turn-transition
   semantics, keeps the turn recoverable); requestUserInput → `{"answers": {}}`
   (parsed as Cancel). Same pattern as the existing `{"decision": "decline"}`.
3. **No work-dir auto-approval** for the new methods: `extract_codex_approval_paths`
   already returns `([], False)` for them and `_targets_only_work_dirs` refuses
   `fully_known=False` (`base_agent.py:769-770`). Additionally tighten
   `auto_approve_response_for`'s guard to an explicit command/file allowlist so the new
   methods can never slip through it.
4. **No `_user_terminated_tool_ids` marking** for MCP declines: the JSONL
   `mcp_tool_call_end` carries `{"Err": …}` which the Codex compute already converts
   into an errored `ToolResultLink` (`_mcp_tool_call_end_error`,
   `src/twicc/providers/codex/compute.py:1123-1160`) — the spinner stops without the
   side-table. `_record_decision_outcome` gets an explicit early-return for the two new
   methods.
5. **Frontend: four self-contained body components** under
   `frontend/src/components/session/detail/items/codex/`, each owning its action row
   (buttons differ per kind). The existing `PendingRequestBody.vue` becomes the router:
   for the new tool_names it renders the sub-component and suppresses its legacy shared
   action row / Cmd+Enter shortcut / autofocus logic. Response payloads flow through the
   unchanged `codex:pending_request_response` WS message (fields are spread verbatim,
   `frontend/src/providers/codex/ws.js:respondToPendingRequest`).
6. **UI approval semantics (mcpToolCall)**: Deny → `decline`; Approve split-button →
   `accept` (+ optional `persist`) with the persist variants shown only when `_meta.persist`
   offers them. No "Cancel turn" button: unlike command/file approvals, `cancel` here
   does NOT abort the turn (it only cancels the call, like decline but with a different
   message) — offering both would be confusing.
7. **Naming**: `mcpToolCall`, `elicitationForm`, `elicitationUrl`, `toolRequestUserInput`
   (camelCase, consistent with `commandExecution`/`fileChange`/`permissions`).

## File structure

- Modify: `src/twicc/providers/codex/agent/approvals.py` — methods map, `resolve_tool_name`,
  `make_pending_request` request_type, `default_response_for`, `auto_approve_response_for` guard.
- Modify: `src/twicc/providers/codex/agent/agent.py` — docstrings, `_record_decision_outcome`
  early-return.
- Modify: `src/twicc/providers/codex/ws.py` — response builders + safe defaults for the new
  tool_names.
- Modify: `frontend/src/components/session/detail/items/codex/PendingRequestBody.vue` — route
  new tool_names to sub-components, gate legacy action row/shortcut/focus.
- Create: `frontend/src/components/session/detail/items/codex/McpToolCallApprovalBody.vue`
- Create: `frontend/src/components/session/detail/items/codex/RequestUserInputBody.vue`
- Create: `frontend/src/components/session/detail/items/codex/ElicitationFormBody.vue`
- Create: `frontend/src/components/session/detail/items/codex/ElicitationUrlBody.vue`
- Modify: `frontend/src/providers/codex/ws.js` — doc comment of `respondToPendingRequest`.
- Test (extend): `tests/test_codex_approvals_helpers.py`, `tests/test_codex_ws_responses.py`.

No DB migration, no new WS message type, no CLI/skill change (⇒ no plugin version bump,
no SKILLS-AND-CLI.md change). All code/comments in English.

---

### Task 1: `approvals.py` — recognise the new methods

**Files:**
- Modify: `src/twicc/providers/codex/agent/approvals.py`
- Test: `tests/test_codex_approvals_helpers.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_codex_approvals_helpers.py` (adjust imports at the top of the file
to also import `resolve_tool_name`, `auto_approve_response_for`,
`ELICITATION_METHOD`, `REQUEST_USER_INPUT_METHOD`):

```python
# ---------------------------------------------------------------------------
# New methods: MCP elicitation + requestUserInput (PR: codex-mcp-approvals)
# ---------------------------------------------------------------------------

MCP_APPROVAL_PARAMS = {
    "threadId": "t1", "turnId": "u1", "serverName": "chrome-devtools",
    "mode": "form",
    "_meta": {"codex_approval_kind": "mcp_tool_call", "persist": ["session", "always"]},
    "message": 'Allow the chrome-devtools MCP server to run tool "navigate_page"?',
    "requestedSchema": {"type": "object", "properties": {}},
}
GENERIC_FORM_PARAMS = {
    "threadId": "t1", "turnId": None, "serverName": "some-server",
    "mode": "form", "_meta": None, "message": "Fill this in",
    "requestedSchema": {"type": "object", "properties": {"name": {"type": "string"}}},
}
URL_PARAMS = {
    "threadId": "t1", "turnId": None, "serverName": "some-server",
    "mode": "url", "_meta": None, "message": "Visit this",
    "url": "https://example.com/auth", "elicitationId": "e1",
}
REQUEST_USER_INPUT_PARAMS = {
    "threadId": "t1", "turnId": "u1", "itemId": "call_abc",
    "questions": [{
        "id": "mcp_tool_call_approval_call_abc", "header": "Approve app tool call?",
        "question": "Allow?", "isOther": False, "isSecret": False,
        "options": [{"label": "Allow", "description": "Run the tool and continue."}],
    }],
}


class TestNewMethodRecognition:
    @pytest.mark.parametrize("method", [ELICITATION_METHOD, REQUEST_USER_INPUT_METHOD])
    def test_is_approval_method(self, method):
        assert is_approval_method(method) is True

    @pytest.mark.parametrize("params, expected", [
        (MCP_APPROVAL_PARAMS, "mcpToolCall"),
        (GENERIC_FORM_PARAMS, "elicitationForm"),
        (URL_PARAMS, "elicitationUrl"),
        (None, "elicitationForm"),          # defensive: no params → generic form
        ({"mode": "form", "_meta": {"codex_approval_kind": "tool_suggestion"}},
         "elicitationForm"),                # other approval kinds are NOT tool approvals
    ])
    def test_resolve_tool_name_elicitation(self, params, expected):
        assert resolve_tool_name(ELICITATION_METHOD, params) == expected

    def test_resolve_tool_name_request_user_input(self):
        assert resolve_tool_name(REQUEST_USER_INPUT_METHOD, REQUEST_USER_INPUT_PARAMS) \
            == "toolRequestUserInput"

    def test_resolve_tool_name_static_methods_unchanged(self):
        assert resolve_tool_name(
            "item/commandExecution/requestApproval", {"anything": 1},
        ) == "commandExecution"


class TestNewMethodPendingRequests:
    def test_mcp_tool_call_is_tool_approval(self):
        req = make_pending_request(ELICITATION_METHOD, MCP_APPROVAL_PARAMS)
        assert req.tool_name == "mcpToolCall"
        assert req.request_type == "tool_approval"
        assert req.tool_input["serverName"] == "chrome-devtools"
        assert req.tool_input["_meta"]["persist"] == ["session", "always"]

    @pytest.mark.parametrize("params, tool_name", [
        (GENERIC_FORM_PARAMS, "elicitationForm"),
        (URL_PARAMS, "elicitationUrl"),
    ])
    def test_elicitations_are_questions(self, params, tool_name):
        req = make_pending_request(ELICITATION_METHOD, params)
        assert req.tool_name == tool_name
        assert req.request_type == "ask_user_question"

    def test_request_user_input_is_question(self):
        req = make_pending_request(REQUEST_USER_INPUT_METHOD, REQUEST_USER_INPUT_PARAMS)
        assert req.tool_name == "toolRequestUserInput"
        assert req.request_type == "ask_user_question"
        # itemId doubles as the routing key (derive_request_id picks it up).
        assert req.request_id == "call_abc"

    def test_elicitation_request_id_is_uuid(self):
        # Elicitation params carry no approvalId/itemId → uuid4 fallback.
        req = make_pending_request(ELICITATION_METHOD, MCP_APPROVAL_PARAMS)
        UUID(req.request_id)  # raises if not a valid UUID

    def test_existing_methods_stay_tool_approval(self):
        req = make_pending_request(
            "item/commandExecution/requestApproval", {"itemId": "i1"},
        )
        assert req.request_type == "tool_approval"


class TestNewMethodDefaults:
    def test_elicitation_default_is_cancel(self):
        assert default_response_for(ELICITATION_METHOD) == {"action": "cancel"}

    def test_request_user_input_default_is_empty_answers(self):
        assert default_response_for(REQUEST_USER_INPUT_METHOD) == {"answers": {}}

    def test_defaults_are_fresh_dicts(self):
        a = default_response_for(ELICITATION_METHOD)
        b = default_response_for(ELICITATION_METHOD)
        assert a is not b

    @pytest.mark.parametrize("method", [
        ELICITATION_METHOD, REQUEST_USER_INPUT_METHOD,
        "item/permissions/requestApproval",
    ])
    def test_auto_approve_rejects_non_path_methods(self, method):
        with pytest.raises(ValueError):
            auto_approve_response_for(method)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd /home/twidi/dev/twicc-poc && uv run pytest tests/test_codex_approvals_helpers.py -v -k "NewMethod"`
Expected: FAIL (ImportError on `resolve_tool_name` / `ELICITATION_METHOD`).
Note: in a worktree, use `uv run --active` (see memory `reference_worktree_pytest_active_venv`).

- [ ] **Step 3: Implement in `approvals.py`**

Update the module docstring (lines 1-19): the bridge now owns **5** methods; only
`item/tool/call` and `account/chatgptAuthTokens/refresh` remain delegated. Document the
3 elicitation sub-kinds and the fallback role of requestUserInput. Then:

```python
# Method (wire) → base tool_name. The elicitation entry is refined per-request
# by resolve_tool_name() (its sub-kind depends on the params, not the method).
APPROVAL_METHODS: dict[str, str] = {
    "item/commandExecution/requestApproval": "commandExecution",
    "item/fileChange/requestApproval":       "fileChange",
    "item/permissions/requestApproval":      "permissions",
    "mcpServer/elicitation/request":         "elicitationForm",
    "item/tool/requestUserInput":            "toolRequestUserInput",
}

ELICITATION_METHOD = "mcpServer/elicitation/request"
REQUEST_USER_INPUT_METHOD = "item/tool/requestUserInput"

# ``_meta`` discriminator marking a form elicitation as an MCP tool-call
# approval (constants from codex-rs ``protocol/src/mcp_approval_meta.rs``).
_APPROVAL_KIND_KEY = "codex_approval_kind"
_APPROVAL_KIND_MCP_TOOL_CALL = "mcp_tool_call"

# tool_names surfaced as questions rather than approvals — drives
# ``PendingRequest.request_type`` and thus the frontend form header.
_QUESTION_TOOL_NAMES = frozenset({
    "elicitationForm", "elicitationUrl", "toolRequestUserInput",
})

# The only methods whose filesystem footprint can be enumerated for the
# work-dir auto-approval. ``permissions`` is a privilege escalation with no
# path footprint; elicitations / user-input forms have none either.
_PATH_AUTO_APPROVABLE_METHODS = frozenset({
    "item/commandExecution/requestApproval",
    "item/fileChange/requestApproval",
})


def resolve_tool_name(method: str, params: dict | None) -> str:
    """Refine the wire method into the tool_name the frontend dispatches on.

    Static for every method except the MCP elicitation, whose sub-kind lives
    in the params: ``mode=url`` → ``elicitationUrl``; ``mode=form`` tagged
    ``_meta.codex_approval_kind == "mcp_tool_call"`` → ``mcpToolCall`` (Codex
    asking to approve an MCP tool call); any other form → ``elicitationForm``
    (a genuine MCP-server-initiated form). Unknown/missing params fall back to
    the generic form so a schema drift degrades to a visible (if plain)
    prompt, never a silent drop.
    """
    base = APPROVAL_METHODS[method]
    if method != ELICITATION_METHOD:
        return base
    if not isinstance(params, dict):
        return "elicitationForm"
    if params.get("mode") == "url":
        return "elicitationUrl"
    meta = params.get("_meta")
    if isinstance(meta, dict) and meta.get(_APPROVAL_KIND_KEY) == _APPROVAL_KIND_MCP_TOOL_CALL:
        return "mcpToolCall"
    return "elicitationForm"
```

In `make_pending_request`, replace the `tool_name = APPROVAL_METHODS[method]` lookup
and the hardcoded request_type (update the docstring accordingly — the "Codex never
uses ask_user_question" comment is now wrong):

```python
    tool_name = resolve_tool_name(method, params)
    request_type = (
        "ask_user_question" if tool_name in _QUESTION_TOOL_NAMES else "tool_approval"
    )
    return PendingRequest(
        request_id=derive_request_id(params),
        request_type=request_type,
        tool_name=tool_name,
        ...
```

In `default_response_for`, before the final `return {"decision": "decline"}` (and
extend its docstring):

```python
    if method == ELICITATION_METHOD:
        # ``cancel`` (not decline): a teardown is not a user decision. For an
        # MCP tool-call approval Codex skips the call with "user cancelled";
        # for a genuine elicitation ``cancel`` is the MCP "dismissed" action.
        return {"action": "cancel"}
    if method == REQUEST_USER_INPUT_METHOD:
        # An empty answers map is a valid ToolRequestUserInputResponse; the
        # MCP-approval parser maps a missing answer to Cancel.
        return {"answers": {}}
```

In `auto_approve_response_for`, replace the guard
`if method not in APPROVAL_METHODS or method == "item/permissions/requestApproval":`
with `if method not in _PATH_AUTO_APPROVABLE_METHODS:` (docstring: only command/file
are path-auto-approvable). In `extract_codex_approval_paths`'s docstring, add that the
elicitation / requestUserInput methods fall in the "anything unexpected → `([], False)`"
bucket (no code change).

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd /home/twidi/dev/twicc-poc && uv run pytest tests/test_codex_approvals_helpers.py -v`
Expected: PASS — with one known pre-existing casualty to update:
`TestApprovalMethodsConstant.test_constant_keys_match_known_methods` pins
`APPROVAL_METHODS` keys to the 3 legacy methods via the `KNOWN_METHODS` list
(`tests/test_codex_approvals_helpers.py:29-33`); extend `KNOWN_METHODS` with the two
new methods. The other legacy assertions (e.g. `request_type == "tool_approval"` for
the 3 existing methods) remain true and must stay untouched.

- [ ] **Step 5: Commit**

```bash
cd /home/twidi/dev/twicc-poc
git add src/twicc/providers/codex/agent/approvals.py tests/test_codex_approvals_helpers.py
git commit  # subject: "feat(codex): recognise MCP elicitation + requestUserInput as approval methods" + body + Co-Authored-By trailer
```

---

### Task 2: `agent.py` — bridge integration & decision-outcome no-op

**Files:**
- Modify: `src/twicc/providers/codex/agent/agent.py`
- Test: `tests/test_codex_approvals_helpers.py` (same file, agent-level section)

The recognition change in Task 1 already routes the new methods through
`_sync_approval_handler_impl` → `_async_approval_handler` (the gate is
`is_approval_method`). This task fixes the collateral: `_record_decision_outcome`
must not misread the new response shapes, and stale comments must not lie.

- [ ] **Step 1: Write the contract-locking test**

Note: this test may already pass before Step 3 (with `{"answers": {}}` the legacy
branch reads `decision=None` and happens to no-op) — it locks the contract so the
explicit early-return added in Step 3 can never regress into the legacy
decision-reading branch.

Append to `tests/test_codex_approvals_helpers.py`:

```python
from twicc.providers.codex.agent.agent import CodexAgent


class TestRecordDecisionOutcomeNewMethods:
    def _bare_agent(self) -> CodexAgent:
        # ``_record_decision_outcome`` only touches these attributes — build a
        # shell instance without running the (SDK-heavy) constructor.
        agent = CodexAgent.__new__(CodexAgent)
        agent.session_id = "s1"
        agent._items_by_id = {}
        agent._user_terminated_tool_ids = {}
        return agent

    def test_elicitation_decline_marks_nothing(self):
        agent = self._bare_agent()
        agent._record_decision_outcome(
            ELICITATION_METHOD, MCP_APPROVAL_PARAMS, {"action": "decline"},
        )
        assert agent._user_terminated_tool_ids == {}

    def test_request_user_input_marks_nothing(self):
        # params DO carry an itemId — the early-return must fire before the
        # legacy decision-reading branch misinterprets the answers shape.
        agent = self._bare_agent()
        agent._record_decision_outcome(
            REQUEST_USER_INPUT_METHOD, REQUEST_USER_INPUT_PARAMS, {"answers": {}},
        )
        assert agent._user_terminated_tool_ids == {}
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd /home/twidi/dev/twicc-poc && uv run pytest tests/test_codex_approvals_helpers.py -v -k "RecordDecisionOutcome"`
Expected: `test_request_user_input_marks_nothing` FAILS only if the legacy branch marks —
inspect: with `{"answers": {}}` the legacy code reads `decision=None` and logs a no-op, so
both may PASS already. Either way proceed to Step 3: the early-return makes the intent
explicit rather than incidental (and the test locks the contract).

- [ ] **Step 3: Implement in `agent.py`**

Import the two method constants:

```python
from .approvals import (
    ELICITATION_METHOD,
    REQUEST_USER_INPUT_METHOD,
    auto_approve_response_for,
    ...
)
```

At the top of `_record_decision_outcome` (right after the docstring — extend it too),
before the `if not params:` check:

```python
        if method in (ELICITATION_METHOD, REQUEST_USER_INPUT_METHOD):
            # No side-table marking: an MCP tool call the user declines or
            # cancels surfaces in the JSONL as a ``mcp_tool_call_end`` with a
            # ``{"Err": …}`` result, which the compute already converts into
            # an errored ToolResultLink (``_mcp_tool_call_end_error``) — the
            # spinner stops on its own. Generic elicitations / user-input
            # forms have no tool item at all.
            return
```

Update the stale prose:
- module docstring (lines 7-14): the bridge routes **five** method families;
- constructor comment (lines 272-286): the delegated list shrinks to
  `item/tool/call` + `account/chatgptAuthTokens/refresh`;
- `approvals.py` module docstring if not already done in Task 1.

- [ ] **Step 4: Run the full backend test suite**

Run: `cd /home/twidi/dev/twicc-poc && uv run pytest tests/ -x -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/twicc/providers/codex/agent/agent.py tests/test_codex_approvals_helpers.py
git commit  # subject: "feat(codex): route MCP elicitations through the approval bridge" + body + trailer
```

---

### Task 3: `ws.py` — response builders for the new tool_names

**Files:**
- Modify: `src/twicc/providers/codex/ws.py`
- Test: `tests/test_codex_ws_responses.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_codex_ws_responses.py`:

```python
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

    @pytest.mark.parametrize("payload", [
        {"action": "approve"},                                  # unknown action
        {"action": None}, {},                                   # missing action
        {"action": "accept", "persist": "forever"},             # bad persist value
        {"action": "decline", "persist": "session"},            # persist without accept
        {"action": "accept", "content": "not-a-dict"},          # non-dict content
    ])
    def test_invalid_payloads(self, handler, payload):
        assert handler._build_codex_response("mcpToolCall", payload) is None

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

    @pytest.mark.parametrize("answers", [
        None, "nope", ["list"],                       # not a dict
        {"q1": "Allow"},                              # entry not a dict
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
```

- [ ] **Step 2: Run to verify failure**

Run: `cd /home/twidi/dev/twicc-poc && uv run pytest tests/test_codex_ws_responses.py -v -k "Elicitation or RequestUserInput or NewMethods"`
Expected: FAIL (unknown tool_name → `None` everywhere; safe default returns decline).

- [ ] **Step 3: Implement in `ws.py`**

Class constants:

```python
    _ELICITATION_ACTIONS: set[str] = {"accept", "decline", "cancel"}
    _ELICITATION_PERSIST_VALUES: set[str] = {"session", "always"}
    _ELICITATION_TOOL_NAMES: set[str] = {"mcpToolCall", "elicitationForm", "elicitationUrl"}
```

Dispatch in `_build_codex_response` (before the final unknown-tool_name error; also
extend the `_handle_pending_request_response` wire-shape docstring with the new
payloads):

```python
        if tool_name in self._ELICITATION_TOOL_NAMES:
            return self._build_elicitation_response(tool_name, content)

        if tool_name == "toolRequestUserInput":
            return self._build_request_user_input_response(content)
```

Builders (mirror the logging style of the existing ones):

```python
    def _build_elicitation_response(self, tool_name: str, content: dict) -> dict | None:
        """Response for the 3 elicitation sub-kinds (wire: McpServerElicitationRequestResponse).

        ``{"action", "content", "_meta"}`` — ``content`` is the filled form on
        an accepted ``elicitationForm``; ``_meta.persist`` is the
        remember-this-choice variant on an accepted ``mcpToolCall``. Both are
        only meaningful with ``accept`` and are rejected otherwise. ``content``
        is deliberately NOT gated per tool_name (an accepted ``mcpToolCall``
        may carry one): Codex's approval parser ignores unexpected content, and
        the looseness keeps the builder simple.
        """
        action = content.get("action")
        if action not in self._ELICITATION_ACTIONS:
            logger.error("codex %s: invalid action=%r", tool_name, action)
            return None
        persist = content.get("persist")
        form_content = content.get("content")
        if action != "accept" and (persist is not None or form_content is not None):
            logger.error(
                "codex %s: persist/content only allowed with accept "
                "(action=%r persist=%r)", tool_name, action, persist,
            )
            return None
        response: dict = {"action": action, "content": None, "_meta": None}
        if form_content is not None:
            if not isinstance(form_content, dict):
                logger.error(
                    "codex %s: invalid content type=%r (expected dict)",
                    tool_name, type(form_content).__name__,
                )
                return None
            response["content"] = form_content
        if persist is not None:
            if tool_name != "mcpToolCall" or persist not in self._ELICITATION_PERSIST_VALUES:
                logger.error(
                    "codex %s: invalid persist=%r", tool_name, persist,
                )
                return None
            response["_meta"] = {"persist": persist}
        return response

    def _build_request_user_input_response(self, content: dict) -> dict | None:
        """Response for ``item/tool/requestUserInput`` (wire: ToolRequestUserInputResponse).

        ``{"answers": {question_id: {"answers": [str, …]}}}`` — an empty map is
        valid (Codex treats a missing answer as a cancel).
        """
        answers = content.get("answers")
        if not isinstance(answers, dict):
            logger.error(
                "codex toolRequestUserInput: invalid answers type=%r",
                type(answers).__name__,
            )
            return None
        normalized: dict = {}
        for question_id, entry in answers.items():
            values = entry.get("answers") if isinstance(entry, dict) else None
            if (
                not isinstance(question_id, str)
                or not isinstance(values, list)
                or not all(isinstance(v, str) for v in values)
            ):
                logger.error(
                    "codex toolRequestUserInput: invalid entry for %r: %r",
                    question_id, entry,
                )
                return None
            normalized[question_id] = {"answers": values}
        return {"answers": normalized}
```

`_safe_default_for` additions (before the final return):

```python
        if tool_name in self._ELICITATION_TOOL_NAMES:
            return {"action": "cancel", "content": None, "_meta": None}
        if tool_name == "toolRequestUserInput":
            return {"answers": {}}
```

- [ ] **Step 4: Run the tests**

Run: `cd /home/twidi/dev/twicc-poc && uv run pytest tests/test_codex_ws_responses.py -v`
Expected: PASS (new + pre-existing).

- [ ] **Step 5: Commit**

```bash
git add src/twicc/providers/codex/ws.py tests/test_codex_ws_responses.py
git commit  # subject: "feat(codex): WS response builders for MCP elicitations + requestUserInput" + body + trailer
```

---

### Task 4: frontend — route new tool_names in `PendingRequestBody.vue`

**Files:**
- Modify: `frontend/src/components/session/detail/items/codex/PendingRequestBody.vue`
- Modify: `frontend/src/providers/codex/ws.js` (doc comment only)

No frontend test infra — verification is visual (Task 9). Keep this task compiling
by creating four placeholder components in the same step batch as the routing
(Tasks 5-8 then replace their contents), or reorder: implement Tasks 5-8 first and
wire the router last. **Recommended execution order: do Task 4 last of the frontend
tasks, after 5-8.** It is specified here because the contracts (props/emits) flow
from it.

Contract for all four sub-components:

- Props: `pendingRequest` (Object, required), `isResponding` (Boolean), `sessionId` (String).
- Emit: `submit` with the payload the backend builder expects
  (`{tool_name, action, persist?, content?}` or `{tool_name: 'toolRequestUserInput', answers}`).
- Mark the primary control with class `auto-focused` (PendingRequestForm's
  `focusBodyNow` targets it, `PendingRequestForm.vue:109-127`).

- [ ] **Step 1: Add routing to `PendingRequestBody.vue`**

Script additions:

```js
import McpToolCallApprovalBody from './McpToolCallApprovalBody.vue'
import RequestUserInputBody from './RequestUserInputBody.vue'
import ElicitationFormBody from './ElicitationFormBody.vue'
import ElicitationUrlBody from './ElicitationUrlBody.vue'

// tool_names rendered by a self-contained sub-component that owns its whole
// body INCLUDING the action row (buttons differ per kind). Everything else
// keeps the legacy shared Approve/Deny/Cancel-turn row below.
const SELF_CONTAINED_BODIES = {
    mcpToolCall: McpToolCallApprovalBody,
    toolRequestUserInput: RequestUserInputBody,
    elicitationForm: ElicitationFormBody,
    elicitationUrl: ElicitationUrlBody,
}
const selfContainedBody = computed(() => SELF_CONTAINED_BODIES[toolName.value] || null)
```

Guard the legacy-only behaviours:
- in `focusApproveButton`: `if (selfContainedBody.value) return` (first line — the
  sub-components own their focus via `.auto-focused`); update the comment at lines
  28-29 ("Codex has no ask_user_question variant" is now false).
- in `onSubmitShortcut`: `if (selfContainedBody.value) return` right after the
  modifier check (the sub-components own their keyboard handling; a global
  Cmd+Enter → "approve" on a form with unfilled required fields would be wrong).

Template: wrap the whole existing content in a guard and delegate:

```html
    <div class="codex-pending-body">
        <component
            :is="selfContainedBody"
            v-if="selfContainedBody"
            :pending-request="pendingRequest"
            :is-responding="isResponding"
            :session-id="sessionId"
            @submit="emit('submit', $event)"
        />
        <template v-else>
            <!-- …existing commandExecution/fileChange/permissions/unknown templates
                 AND the shared action row, unchanged… -->
        </template>
    </div>
```

(The `unknown` fallback branch and its shared action row stay — for a truly unknown
tool_name the safe default reply remains reachable.)

- [ ] **Step 2: Update the `respondToPendingRequest` doc comment in `frontend/src/providers/codex/ws.js`**

Document the two new payload families next to the existing ones:

```js
 *   mcpToolCall / elicitationForm / elicitationUrl:
 *     { tool_name, action: 'accept' | 'decline' | 'cancel',
 *       persist?: 'session' | 'always',      // mcpToolCall accept only
 *       content?: { ... } }                  // elicitationForm accept only
 *
 *   toolRequestUserInput:
 *     { tool_name: 'toolRequestUserInput',
 *       answers: { [questionId]: { answers: [string, ...] } } }
```

- [ ] **Step 3: Verify the dev build compiles**

Run: `cd /home/twidi/dev/twicc-poc/frontend && npx vite build --logLevel error 2>&1 | tail -5`
Expected: build completes without errors. (Do NOT restart dev servers — reserved to the user.)

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/session/detail/items/codex/PendingRequestBody.vue frontend/src/providers/codex/ws.js
git commit  # subject: "feat(codex): route MCP elicitation pending requests to dedicated bodies" + body + trailer
```

---

### Task 5: `McpToolCallApprovalBody.vue`

**Files:**
- Create: `frontend/src/components/session/detail/items/codex/McpToolCallApprovalBody.vue`

- [ ] **Step 1: Create the component**

Layout mirrors the existing Codex pending bodies (`codex-pending-section` card look —
copy the section/summary/label CSS patterns from `PendingRequestBody.vue`). Content:

```vue
<script setup>
import { computed, nextTick, onMounted, ref, useId, watch } from 'vue'
import AppTooltip from '../../../../ui/AppTooltip.vue'
import { canStealFocus } from '../../../../../utils/focusGuard'

const props = defineProps({
    pendingRequest: { type: Object, required: true },
    isResponding: { type: Boolean, default: false },
    sessionId: { type: String, required: true },
})
const emit = defineEmits(['submit'])

const denyButtonId = useId()
const approveButtonId = useId()
const approveMenuId = useId()
const approveOnceId = useId()
const approveSessionId = useId()
const approveAlwaysId = useId()

// Wire params: McpServerElicitationRequestParams (mode=form, approval-tagged).
const input = computed(() => props.pendingRequest.tool_input || {})
const serverName = computed(() => input.value.serverName || 'unknown server')
const message = computed(() => input.value.message || '')
const meta = computed(() => {
    const m = input.value._meta
    return (m && typeof m === 'object') ? m : {}
})

// ``persist`` advertises which remember variants Codex will honour:
// "session" | "always" | ["session","always"] | absent.
const persistOptions = computed(() => {
    const p = meta.value.persist
    if (Array.isArray(p)) return p.filter((v) => typeof v === 'string')
    return typeof p === 'string' ? [p] : []
})
const canPersistSession = computed(() => persistOptions.value.includes('session'))
const canPersistAlways = computed(() => persistOptions.value.includes('always'))
const hasApproveMenu = computed(() => canPersistSession.value || canPersistAlways.value)

const toolTitle = computed(() => meta.value.tool_title)
const toolDescription = computed(() => meta.value.tool_description)
// Pre-rendered params: [{name, value, displayName}] — preferred display.
const paramsDisplay = computed(() =>
    Array.isArray(meta.value.tool_params_display) ? meta.value.tool_params_display : [])
// Raw arguments fallback when no rendered display is provided.
const rawParams = computed(() => {
    if (paramsDisplay.value.length) return null
    const p = meta.value.tool_params
    return (p && typeof p === 'object' && Object.keys(p).length) ? p : null
})

function displayValue(value) {
    return typeof value === 'string' ? value : JSON.stringify(value)
}

function approve(persist) {
    const payload = { tool_name: 'mcpToolCall', action: 'accept' }
    if (persist) payload.persist = persist
    emit('submit', payload)
}
function deny() {
    emit('submit', { tool_name: 'mcpToolCall', action: 'decline' })
}

// Auto-focus Approve (same gating as the legacy body).
const approveButtonRef = ref(null)
function focusApprove() {
    nextTick(() => {
        if (!canStealFocus()) return
        approveButtonRef.value?.focus()
    })
}
onMounted(focusApprove)
watch(() => props.pendingRequest?.request_id, focusApprove)
</script>

<template>
    <div class="mcp-approval-body">
        <div class="codex-pending-section">
            <div class="codex-pending-summary">
                <span class="codex-summary-label">MCP tool call</span>
                <wa-badge variant="neutral">{{ serverName }}</wa-badge>
                <span v-if="toolTitle" class="mcp-tool-title">{{ toolTitle }}</span>
            </div>
            <div class="mcp-approval-message">{{ message }}</div>
            <div v-if="toolDescription" class="mcp-tool-description">{{ toolDescription }}</div>
            <ul v-if="paramsDisplay.length" class="mcp-param-list">
                <li v-for="(param, idx) in paramsDisplay" :key="idx">
                    <span class="mcp-param-name">{{ param.displayName || param.name }}</span>
                    <code class="mcp-param-value">{{ displayValue(param.value) }}</code>
                </li>
            </ul>
            <details v-else-if="rawParams" class="mcp-raw-params">
                <summary>Arguments</summary>
                <pre>{{ JSON.stringify(rawParams, null, 2) }}</pre>
            </details>
        </div>
        <div class="codex-pending-actions">
            <wa-button
                :id="denyButtonId" variant="danger" appearance="outlined" size="small"
                :disabled="isResponding" @click="deny"
            >
                <wa-icon slot="start" name="xmark" variant="classic"></wa-icon>
                Deny
            </wa-button>
            <AppTooltip :for="denyButtonId">Refuse this tool call. Codex may try another approach.</AppTooltip>
            <wa-button-group label="Approve">
                <wa-button
                    :id="approveButtonId" ref="approveButtonRef" class="auto-focused"
                    variant="brand" size="small" :disabled="isResponding"
                    @click="approve()"
                >
                    <wa-icon slot="start" name="check" variant="classic"></wa-icon>
                    Approve
                </wa-button>
                <AppTooltip :for="approveButtonId">Approve this tool call.</AppTooltip>
                <wa-dropdown v-if="hasApproveMenu" placement="top-end">
                    <wa-button
                        :id="approveMenuId" slot="trigger" variant="brand" size="small"
                        :disabled="isResponding"
                    >
                        <wa-icon name="chevron-up" label="More approve options" variant="classic"></wa-icon>
                    </wa-button>
                    <AppTooltip :for="approveMenuId">More approve options.</AppTooltip>
                    <wa-dropdown-item :id="approveOnceId" @click="approve()">
                        <wa-icon slot="icon" name="check" variant="classic"></wa-icon>
                        Once
                    </wa-dropdown-item>
                    <AppTooltip placement="left" :for="approveOnceId">Approve only this call.</AppTooltip>
                    <wa-dropdown-item v-if="canPersistSession" :id="approveSessionId" @click="approve('session')">
                        <wa-icon slot="icon" name="rotate" variant="classic"></wa-icon>
                        For this session
                    </wa-dropdown-item>
                    <AppTooltip v-if="canPersistSession" placement="left" :for="approveSessionId">Approve and remember for the rest of the session.</AppTooltip>
                    <wa-dropdown-item v-if="canPersistAlways" :id="approveAlwaysId" @click="approve('always')">
                        <wa-icon slot="icon" name="infinity" variant="classic"></wa-icon>
                        Always
                    </wa-dropdown-item>
                    <AppTooltip v-if="canPersistAlways" placement="left" :for="approveAlwaysId">Approve and never ask again for this tool.</AppTooltip>
                </wa-dropdown>
            </wa-button-group>
        </div>
    </div>
</template>
```

Style block: reuse the visual vocabulary of `PendingRequestBody.vue` (`.codex-pending-section`,
`.codex-pending-summary`, `.codex-summary-label`, `.codex-pending-actions` are scoped to
that component — duplicate the needed rules locally, keeping names local to this file;
add `.mcp-param-list` rows styled like `.codex-permission-row`). Check that the
`infinity` icon exists in FA Free (memory `reference_fa_free_icons_cdn`: verify with a
200 check on ka-f.fontawesome.com; fall back to `repeat` if 403).

- [ ] **Step 2: Verify build**

Run: `cd /home/twidi/dev/twicc-poc/frontend && npx vite build --logLevel error 2>&1 | tail -3`
Expected: success.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/session/detail/items/codex/McpToolCallApprovalBody.vue
git commit  # subject: "feat(codex): MCP tool-call approval form" + body + trailer
```

---

### Task 6: `RequestUserInputBody.vue`

**Files:**
- Create: `frontend/src/components/session/detail/items/codex/RequestUserInputBody.vue`

- [ ] **Step 1: Create the component**

Question form: one section per entry of `tool_input.questions`. Model after the Claude
`ask_user_question` UX (`claude_code/PendingRequestBody.vue` — option cards, "Other"
input) but single-select per question (the wire `answers` array supports multiple, but
Codex's own prompts are single-choice; keep one selection + optional Other). Key points:

```js
const questions = computed(() => {
    const qs = props.pendingRequest.tool_input?.questions
    return Array.isArray(qs) ? qs : []
})
// One reactive slot per question index: {label: string|null, other: string}
const selections = ref({})     // idx -> selected option label (null if Other/none)
const otherTexts = ref({})     // idx -> free text
const otherActive = ref({})    // idx -> bool

const allAnswered = computed(() =>
    questions.value.length > 0 && questions.value.every((q, idx) => answerFor(idx) !== null))

function answerFor(idx) {
    const q = questions.value[idx]
    if (otherActive.value[idx] || !(q.options?.length)) {
        const text = (otherTexts.value[idx] || '').trim()
        return text ? text : null
    }
    return selections.value[idx] ?? null
}

function submit() {
    const answers = {}
    for (const [idx, q] of questions.value.entries()) {
        answers[q.id] = { answers: [answerFor(idx)] }
    }
    emit('submit', { tool_name: 'toolRequestUserInput', answers })
}
function dismiss() {
    // Empty answers map — Codex treats a missing answer as a cancel.
    emit('submit', { tool_name: 'toolRequestUserInput', answers: {} })
}
```

Template per question: `header` as the section label, `question` as body text, options
as clickable option-cards (label + description, selected state, first card of the first
question carries `auto-focused`), an "Other" card + `wa-input` when `isOther` (input
`type="password"` when `isSecret`), a bare `wa-input`/`wa-textarea` when `options` is
null/empty. Action row: `Dismiss` (neutral outlined, sends empty answers) + `Submit`
(brand, disabled until `allAnswered || isResponding`).

Note for the MCP-approval fallback: the options arrive with labels
"Allow" / "Allow for this session" / "Allow and don't ask me again" / "Cancel" — plain
label passthrough is the contract; no special-casing.

- [ ] **Step 2: Verify build**

Run: `cd /home/twidi/dev/twicc-poc/frontend && npx vite build --logLevel error 2>&1 | tail -3`
Expected: success.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/session/detail/items/codex/RequestUserInputBody.vue
git commit  # subject: "feat(codex): requestUserInput question form" + body + trailer
```

---

### Task 7: `ElicitationFormBody.vue`

**Files:**
- Create: `frontend/src/components/session/detail/items/codex/ElicitationFormBody.vue`

- [ ] **Step 1: Create the component**

Dynamic form driven by `tool_input.requestedSchema` (see the schema reference above).
Header: server badge (`tool_input.serverName`) + `message`. Field classification:

```js
const schema = computed(() => props.pendingRequest.tool_input?.requestedSchema || {})
const requiredSet = computed(() => new Set(schema.value.required || []))
const fields = computed(() =>
    Object.entries(schema.value.properties || {}).map(([name, spec]) => ({
        name,
        spec,
        kind: classify(spec),
        label: spec.title || name,
        required: requiredSet.value.has(name),
    })))

function classify(spec) {
    if (spec.type === 'boolean') return 'boolean'
    if (spec.type === 'number' || spec.type === 'integer') return 'number'
    if (spec.type === 'array') return 'multiselect'          // items.enum / items.anyOf|oneOf
    if (spec.type === 'string' && (spec.enum || spec.oneOf)) return 'select'
    if (spec.type === 'string') return 'text'
    return 'unsupported'                                      // render as read-only notice
}

// Normalised options for select/multiselect:
// enum:[v] (+ enumNames:[n]) → [{value: v, label: n||v}]
// oneOf/anyOf:[{const, title}] → [{value: const, label: title}]
function optionsFor(spec) {
    const source = spec.enum
        ? spec.enum.map((v, i) => ({ value: v, label: spec.enumNames?.[i] || v }))
        : (spec.oneOf || spec.items?.anyOf || spec.items?.oneOf || [])
            .map((o) => ({ value: o.const, label: o.title || o.const }))
    if (spec.items?.enum) return spec.items.enum.map((v) => ({ value: v, label: v }))
    return source
}
```

Rendering per kind: `boolean` → `wa-checkbox` (checked from `default`); `number` →
`wa-input type="number"` with `min`/`max` (`minimum`/`maximum`); `select` →
`wa-select` + `wa-option`; `multiselect` → checkbox group (respect `minItems`/`maxItems`
in the validity computed); `text` → `wa-input` with `minlength`/`maxlength` and the
`format` mapped to input types (`email` → email, `uri` → url, `date` → date,
`date-time` → datetime-local). Show `description` as help text under each field.

Value collection on submit (accept):

```js
function collectContent() {
    const content = {}
    for (const field of fields.value) {
        const raw = values.value[field.name]
        if (raw === undefined || raw === null || raw === '') continue
        if (field.kind === 'number') {
            content[field.name] = field.spec.type === 'integer'
                ? parseInt(raw, 10) : Number(raw)
        } else if (field.kind === 'boolean') {
            content[field.name] = Boolean(raw)
        } else {
            content[field.name] = raw   // string or array of strings
        }
    }
    return content
}
```

`canSubmit`: every required field has a value; number fields parse; multiselects within
minItems/maxItems. Action row: `Cancel` (neutral, `{action:'cancel'}`) · `Decline`
(danger outlined, `{action:'decline'}`) · `Submit` (brand, `.auto-focused`,
`{tool_name:'elicitationForm', action:'accept', content: collectContent()}`). An empty
`properties` object (legal) renders just the message + the three buttons — Submit then
sends `content: {}`.

- [ ] **Step 2: Verify build**

Run: `cd /home/twidi/dev/twicc-poc/frontend && npx vite build --logLevel error 2>&1 | tail -3`
Expected: success. All used WA components (`wa-input`, `wa-select`, `wa-option`,
`wa-checkbox`, `wa-switch`, `wa-textarea`) are already imported in `frontend/src/main.js`
(lines 22-41) — verify none new are introduced, else add the import there.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/session/detail/items/codex/ElicitationFormBody.vue
git commit  # subject: "feat(codex): generic MCP elicitation form" + body + trailer
```

---

### Task 8: `ElicitationUrlBody.vue`

**Files:**
- Create: `frontend/src/components/session/detail/items/codex/ElicitationUrlBody.vue`

- [ ] **Step 1: Create the component**

Simplest of the four. Reads `tool_input.serverName`, `message`, `url`. Body: server
badge + message + the URL rendered as a copyable code line AND an explicit
`<a :href="url" target="_blank" rel="noopener noreferrer">` link button ("Open link").
Action row: `Cancel` (`{tool_name:'elicitationUrl', action:'cancel'}`) · `Decline`
(`action:'decline'`) · `Done` (brand, `.auto-focused`, `action:'accept'` — the user
clicks it after completing the flow in the opened tab). Guard: if `url` is not an
`http(s)` URL, render it as text only (no clickable link).

- [ ] **Step 2: Verify build**

Run: `cd /home/twidi/dev/twicc-poc/frontend && npx vite build --logLevel error 2>&1 | tail -3`
Expected: success.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/session/detail/items/codex/ElicitationUrlBody.vue
git commit  # subject: "feat(codex): URL elicitation body" + body + trailer
```

---

### Task 9: end-to-end verification (manual, with the user)

**Files:** none (verification only). Dev-server restart is reserved to the user — ask
them to restart the backend before this task.

- [ ] **Step 1: Trigger a real MCP tool-call approval (elicitation path)**

Setup: a user-configured MCP server must be present in `~/.codex/config.toml` (the
user already has `chrome-devtools`). To force prompts even on annotation-less tools,
optionally add under that server: `default_tools_approval_mode = "prompt"`.

- Create a Codex session in `auto` (NOT `yolo`) permission mode.
- Ask it to use an MCP tool (e.g. "navigate to http://localhost:5173 with chrome-devtools").
- Expected: the "Tool approval requested" form appears with server name + message +
  Approve/Deny; **Approve** → the tool runs (`mcp_tool_call_end` with `Ok`);
  re-trigger and **Deny** → transcript shows the errored tool result
  ("user rejected MCP tool call"), spinner stops, turn continues.
- If `_meta.persist` offered variants: **For this session** → subsequent calls of the
  same tool run without a prompt.

- [ ] **Step 2: Verify the requestUserInput fallback**

In `~/.codex/config.toml` add `[features] tool_call_mcp_elicitation = false`
(verify the exact features key spelling against the installed CLI, e.g.
`codex features list` or the release notes — the Rust enum variant is
`ToolCallMcpElicitation`). Restart the session's agent, re-trigger the MCP call.
Expected: the "needs your input" form with the Allow/"Allow for this session"/Cancel
option cards; **Allow** → tool runs. Remove the feature override afterwards.

- [ ] **Step 3: Kill-path sanity**

Trigger an approval prompt, then Stop the session while the prompt is pending.
Expected: no hang — the bridge answers `{"action":"cancel"}` (elicitation) via
`default_response_for` and the agent stops cleanly (check `logs/backend.log` and, with
`TWICC_DEBUG`, the SDK log `logs/sdk/codex/<session>.jsonl` for the approval
request/response pair).

- [ ] **Step 4: Generic form/URL elicitations (best effort)**

Real MCP servers rarely elicit; if no live server is available, verify by unit-shaped
manual test: temporarily hardcode a `GENERIC_FORM_PARAMS`-shaped PendingRequest through
`make_pending_request` in a Django shell and check the WS payload renders in the UI
(or defer to the first real-world occurrence — the wire contract is covered by the
pytest suites).

- [ ] **Step 5: Report**

Summarise results to the user; remind them the backend restart applied the change to
their running instance and that the `[features]` override was removed.

---

## Risks & notes

- **`_meta` keys are snake_case** (`codex_approval_kind`, `tool_params_display`) even
  though the protocol structs are camelCase — the meta is a free-form JSON map built
  with raw string keys (`mcp_approval_meta.rs`), not a serde-renamed struct. The plan's
  detection/copy uses the snake_case keys verbatim.
- **The MCP tool name is not structured** in the elicitation `_meta` — the UI leans on
  `message` (which embeds it) plus `serverName`. Acceptable; revisit if Codex adds the
  planned item-id correlation (TODO in `mcp.rs:298-300`).
- **`derive_request_id` uses uuid4 for elicitations** (no stable wire id in Form mode).
  Routing safety is unaffected (the id round-trips through the frontend), but two
  concurrent elicitations are distinguishable only by their content — fine.
- **Guardian mode** (`approvals_reviewer = auto_review`) bypasses the client entirely
  (`routes_approval_to_guardian`) — no TwiCC involvement, nothing to do.
- **Codex upstream drift**: the elicitation/requestUserInput protocol is marked
  EXPERIMENTAL upstream. The vendored-SDK update procedure (memory
  `reference_codex_sdk_update_procedure`) should re-check these shapes on each
  re-vendoring; the pytest suites pin today's contract.

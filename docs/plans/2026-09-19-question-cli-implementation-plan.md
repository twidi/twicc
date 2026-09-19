# Pending-question CLI — Implementation Plan

**Contract:** [`2026-09-18-question-cli-design.md`](2026-09-18-question-cli-design.md).
That document is the source of truth for *what* and *why*; this one is *in what
order*. Where they disagree, the design wins and this plan is wrong.

**Status:** reviewed (PASS, round 4). Not started.

## Scope

Two commands:

```
twicc session <ID> pending-request [--raw] [--timeout N]
twicc session <ID> answer <answer|cancel> [--request-id ID] [--answer 'ID=VALUE'] [--timeout N]
```

Answerable: Claude `AskUserQuestion` and Codex `toolRequestUserInput` only, minus
the disguised MCP approval (design §1, §3). Everything else is reported
`out_of_scope` and refused.

## Why the order matters

Two constraints drive it.

**The shared status comes first** because every later task depends on it. A
half-declared `fetched` does not break other commands — only the new kind can
ever produce it (`_KIND_HANDLERS`' `success_status` slot) — but the read command
is dead until all of its declaration sites agree, and debugging that from Task 5
is a waste.

**The byte-for-byte lock comes before any extraction.** Task 3 moves the
question translation out of the two `ws.py` files, and its whole claim is "the
web UI behaves identically". That claim is only testable against a baseline
captured while the old code is still in place — hence Task 2, which writes and
runs it green *before* anything moves. Task 3 then copies, Task 4 repoints and
deletes. At no point does a commit leave `ws.py` calling a name that no longer
exists.

After that the order is bottom-up: pure functions, then the handlers, then the
services, then the CLI.

## The one open implementation choice, settled here

Design §8 leaves an alternative: *"the extracted denormalizer either accepts
both shapes, or the WS handlers translate into the normalized answer first."*
**This plan takes neither literally.** The shared translator takes an
**explicit** action:

```python
build_question_response(pending, *, action, answers) -> WireResponse
# action ∈ {"submit", "partial", "cancel"}; answers keyed as the design's §5 ids
```

Deriving submit / partial / `missing_answers` from how many questions are
answered (design §6) is a **caller** rule, not a translator rule. The CLI
service derives it and raises the rejections. The web UI already decides its own
action front-end side (`claude_code/ws.py:385-386`) and passes it straight
through — so its behaviour cannot change, which is what makes Task 3's claim
true rather than hopeful. The only WS-side work left is mapping the UI's
text-keyed answers onto the design's ids; that mapping lives in the Claude
provider module, next to the translator.

## File structure

**Task 1 — the shared status (one commit, no feature behind it yet)**
- Modify: `src/twicc/cli/_drop_request/transport.py` — `_FINAL_STATUSES` (`:38`).
- Modify: `src/twicc/cli/_drop_request/polling.py` — the duplicated tuple (`:39`).
  Hygiene: `poll_status` has no callers left, but an out-of-sync copy is a trap.
- Modify: `src/twicc/cli/_drop_request/output.py` — `build_final` (`:41-105`).
- Modify: `src/twicc/drop_requests_watcher.py` — `_STATUS_TIME_FIELDS` (`:209-217`).

**Task 2 — the baseline lock (tests only, no source change)**
- Create: `tests/test_claude_ws_question_responses.py` — the Codex side already
  has its lock and needs no edit.

**Task 3 — provider modules (copy, originals untouched)**
- Create: `src/twicc/providers/claude_code/pending_question.py`
- Create: `src/twicc/providers/codex/pending_question.py`
- Modify: `src/twicc/providers/helpers.py` — two `BaseProviderHelpers` methods
  (`:274`), defaults raising `NotImplementedError`.
- Modify: `src/twicc/providers/claude_code/helpers.py` — `ClaudeCodeHelpers`
  (`:197`) overrides both, delegating to the new module.
- Modify: `src/twicc/providers/codex/helpers.py` — `CodexHelpers` (`:134`), same.
  Without these two overrides the base defaults raise and nothing works; the
  per-task file lists here are exhaustive, not indicative.
- Create: `tests/test_pending_question.py`

**Task 4 — the WS handlers repoint, originals deleted**
- Modify: `src/twicc/providers/claude_code/ws.py` — the `ask_user_question`
  branch (`:384-420`); `_build_clarify_message` (`:167-197`) and
  `_QUESTION_CANCEL_MESSAGE` (`:161`) deleted here, now imported.
- Modify: `src/twicc/providers/codex/ws.py` —
  `_build_request_user_input_response` (`:346-373`) deleted here, now imported.
  What keeps the existing tests green is **repointing the dispatch branch**
  at `:190-191`: they call `handler._build_codex_response("toolRequestUserInput",
  …)` (`tests/test_codex_ws_responses.py:316,322,327,340`), not the builder
  directly. A same-named thin method is optional, not required.

**Task 5 — the services and the two drop kinds**
- Create: `src/twicc/core/services/pending_question.py`
- Modify: `src/twicc/drop_requests_watcher.py` — `_KIND_HANDLERS` (`:44`).

**Task 6 — the CLI surface**
- Create: `src/twicc/cli/pending_question.py`
- Modify: `src/twicc/cli/__init__.py`
- Modify: `src/twicc/rpc/permissions.py` — `COOKIE_READONLY_COMMANDS` (`:52-80`).

**Task 7 — the surrounding text**
- Modify: `src/twicc/core/services/send_message.py:151`
- Modify: `SKILLS-AND-CLI.md`, `src/twicc/agent/plugin/twicc/skills/twicc-session/SKILL.md`
- Modify: `src/twicc/agent/plugin/twicc/.claude-plugin/plugin.json` — minor bump.
- Modify: `AGENTS.md` only if `CLAUDE.md` gains anything.

**No DB migration. No new WS message type. No frontend change.**

**Every commit** carries a descriptive body, not just a subject, and the
`Co-Authored-By: Claude <the running model> <noreply@anthropic.com>` trailer
(`CLAUDE.md`, *Commit Conventions*). No CHANGELOG entry without an explicit ask.
No lint pass — `project_lint_baseline_deferred`.

---

### Task 1: the `fetched` terminal status

**Files:** the four above · Test: `tests/test_drop_request_statuses.py` (new).

- [ ] **Step 1: Write the failing test**

Pin the invariant the sites share: every member of `transport._FINAL_STATUSES`
has an entry in `drop_requests_watcher._STATUS_TIME_FIELDS`, and `fetched` is a
member.

The second assertion — that the two copies agree — has no target yet: `polling.py`
holds no constant, only an inline literal inside `poll_status` (`:39`). So Step 3
extracts it, and the direction is forced: `transport.py:32` already imports from
`polling`, so **`polling` owns the constant and `transport` imports it**, never
the reverse. Assert on the surviving public name, `polling.FINAL_STATUSES` —
not on `transport._FINAL_STATUSES`, which the clean edit deletes (it has a
single use site, `transport.py:82`) and which would otherwise have to survive as
a cosmetic alias, the kind `CLAUDE.md` *Python Patterns* discourages.

Third assertion, and the one that covers the read command's whole output
contract: `build_final` over a synthetic `fetched` outcome carrying extra keys
emits exactly `{status, request_uuid, session_id, provider, agent_state,
pending_requests}` — proving `project_id` and `fetched_at` do not leak. Without
it that projection is only ever exercised by Task 8's manual run.

- [ ] **Step 2: Run it, confirm it fails** — `uv run pytest tests/test_drop_request_statuses.py -x`

- [ ] **Step 3: Implement**

Extract the literal into `polling.FINAL_STATUSES`, import it from
`transport.py` (which already imports that module) and drop the second copy.
Then `"fetched"` into the surviving tuple, `"fetched": "fetched_at"` into
`_STATUS_TIME_FIELDS`, and a `fetched` branch in `build_final`.

That branch cannot "merge `status_extra`": `build_final` receives
`outcome.data`, which `execute_drop_payload` has already merged and stamped
(`drop_requests_watcher.py:248-257`) — id fields, `status_extra`, `fetched_at`,
all flattened together. So state the output explicitly instead: the branch emits
`{"status": "fetched", "request_uuid": …}` plus the named keys design §5
publishes — `session_id`, `provider`, `agent_state`, `pending_requests` — and
nothing else. A whitelist, so `project_id` and `fetched_at` do not silently ship.

- [ ] **Step 4: Run the full suite** — `uv run pytest`. Nothing else must move.
- [ ] **Step 5: Commit** — `feat(cli): teach the drop transport to carry a read`

---

### Task 2: lock the current wire output

**Files:** tests only. No source file changes in this task.

The point is a baseline captured while the old code is still in place. Written
after Task 1 so the suite is already green, and before any extraction.

- [ ] **Step 1: Codex — confirm the lock already exists**

`tests/test_codex_ws_responses.py:313-343` already asserts the exact dict for
`toolRequestUserInput` (through `_build_codex_response`, the dispatch Task 4
repoints), and `:352` the empty-answers default. Run them, confirm green, add
nothing.

- [ ] **Step 2: Claude — write the missing lock**

There is none: no test anywhere exercises
`ClaudeCodeWSHandler._handle_pending_request_response`. Unlike the Codex
builder it is not a pure function — it looks the pending request up through the
manager (`claude_code/ws.py:388-395`). There is no injectable manager: the
handler calls the module-level factory at call time
(`get_claude_code_agent_manager()`, `ws.py:333`). So patch that factory, have
its `get_agent_info` return an `AgentInfo` carrying a crafted `PendingRequest`,
and read the response off the mock's `resolve_pending_request` call
(`ws.py:444`). Assert it for all three actions:
`submit` (an `Allow` whose `updated_input` echoes the stored questions),
`partial` (a `Deny` carrying the native clarify text, verbatim), `cancel` (a
`Deny` carrying the fixed decline text, verbatim).

- [ ] **Step 3: Run both, confirm green** — `uv run pytest tests/test_codex_ws_responses.py tests/test_claude_ws_question_responses.py`
- [ ] **Step 4: Commit** — `test(ws): lock the question answer wire output`

---

### Task 3: provider modules

**Files:** the four in Task 3 above.

Each provider module owns two functions, reached through
`BaseProviderHelpers`:

- `normalize_pending_request(pending, *, raw=False) -> dict` — design §3 (the
  filter, the `reason` derivation) and §5 (the entry shape, `actions`, and the
  per-question fields). `raw=True` adds `"raw": {"tool_input": …}` to the entry,
  **whatever its kind** (design §5). It also converts `created_at`, an epoch
  float on `PendingRequest` (`agent/states.py:72`), into the ISO string plus the
  `age_seconds` the design publishes — computed at read time, so it belongs
  here, not in the service.
- `build_question_response(pending, *, action, answers) -> WireResponse` —
  design §6's table, with the action explicit (see *the one open implementation
  choice* above).

Claude's module also owns the UI-shape adapter, and it has **two** jobs, not
one: map the web UI's text-keyed answers onto the design's 1-based ids, and wrap
each value in a single-element list. The UI sends one **pre-joined** string per
question (`PendingRequestBody.vue:858-867`, `Array.from(selections).join(', ')`)
while the CLI sends a list per id; if the adapter forgets the wrap, the
translator re-joins an already-joined string and the widget's output changes.
The round-trip is lossless because `buildQuestionAnswers()` only ever keys on
real question texts.

Actions per provider: Claude has all three (`submit`, `partial`, `cancel`);
**Codex has no `partial`** — design §6's table gives it `missing_answers` for a
partial answer set, so its translator accepts `submit` and `cancel` only and
rejects `partial` as a programming error.

- [ ] **Step 1: Write the failing tests** (`tests/test_pending_question.py`)

Per provider: the §3 three-condition filter including the
`mcp_tool_call_approval` exclusion **with its four defensive guards** (a
non-list `questions`, an empty one, a non-dict first entry, a non-string `id`);
the five `reason` values; the entry shape with and without `raw=True`; the
`actions` list, including `cancel` alone when the request is structurally
unanswerable; and the `created_at` → ISO + `age_seconds` conversion. For the
translator: every row of §6's table, each action the provider supports (three on
Claude, two on Codex), and the id/text keying per provider.

- [ ] **Step 2: Run, confirm failure**
- [ ] **Step 3: Implement by COPYING, not moving**

`_build_clarify_message` and `_QUESTION_CANCEL_MESSAGE` are **copied** from
`claude_code/ws.py`; `_build_request_user_input_response`'s body is **copied**
from `codex/ws.py`. The originals stay where they are until Task 4. Do not
reword them: Task 2's lock asserts the exact strings.

- [ ] **Step 4: Run the FULL suite** — `uv run pytest`. Task 2's locks must
      still pass: this task changed no behaviour anywhere.
- [ ] **Step 5: Commit** — `feat(providers): add the shared question translator`

---

### Task 4: the WS handlers repoint

**Files:** the two `ws.py`.

- [ ] **Step 1: Delegate and delete**

Each handler calls the Task 3 functions and its now-duplicated helper is
removed. On Codex that means **repointing the dispatch branch** at
`ws.py:190-191` to the module function and deleting `:346-373`; no thin method
is needed, because no test calls the builder directly — they all go through
`_build_codex_response`. On Claude, the
handler passes the UI's own `action` straight through and runs the answers
through the adapter; it keeps its two approval-branch side effects (the trust
clamp, the `setMode` persist) untouched.

- [ ] **Step 2: Run Task 2's locks** — same expected output, still green. This
      is the whole point of the task.
- [ ] **Step 3: Run the full suite** — `uv run pytest`
- [ ] **Step 4: Commit** — `refactor(ws): route question answers through the shared translator`

---

### Task 5: the services

**Files:** `core/services/pending_question.py` (new), `drop_requests_watcher.py`
· Test: `tests/test_pending_question_services.py` (new).

Both services start with `_lookup_session_for_update`
(`core/services/session_update.py:89`), which is where `provider_disabled` comes
from (`:145`) — not a client-side check.

`read_pending_requests_from_payload`: shared guard → `registry.get_agent_info()`
→ `agent_state` derived from the live `AgentInfo`, **never** from a `ProcessRun`
row (design §5) → normalize every pending request, passing the payload's `raw`
flag → return through `status_extra`.

**No agent is not a rejection on this path.** `get_agent_info` returning `None`
means `agent_state: "dead"`, an empty list and **exit 0** (design §5, §7 race 2)
— the mirror image of the answer path's stage 3, which turns the same `None`
into `agent_dead`. Step 1's "one per rejection code" list does not reach this
case, since `agent_dead` is `answer`-only; test it explicitly.

That last step needs a **new result type**, and the plan must not hand-wave it.
The shared guard's error objects are `UpdateSessionResult`
(`core/services/session_update.py:80-85`), a NamedTuple with no `status_extra`
field — while `execute_drop_payload` reads the payload off
`getattr(result, "status_extra", None)` (`drop_requests_watcher.py:254`) and the
id fields off `_RESULT_ID_FIELDS` (`:193-205`). So define
`ReadPendingRequestsResult` alongside the service: `success`, `session_id`,
`provider`, `project_id`, `errors`, plus `status_extra` carrying
`{agent_state, pending_requests}`. The failure path keeps returning the guard's
`UpdateSessionResult` unchanged — both shapes satisfy the watcher's `getattr`
reads.

`answer_pending_question_from_payload`, in stages — the middle one is easy to
miss and owns six of the design's rejection codes:

1. shared guard (`provider_disabled`);
2. self-answer refusal, comparing the payload's `caller_session_id` with the
   target (Task 6 stamps it) — `self_answer_refused`;
3. `registry.get_agent_info()` — `None` is `agent_dead`;
4. resolve the target by **counting the requests that pass design §3's
   filter** — that count is the whole rule, and what is pending *besides* them
   never enters it (design §3: the filter is the only definition of
   *answerable*). Zero → `no_pending_question`, even when the session is loudly
   blocked on a tool approval: that is the ordinary case of this command, not
   an ambiguity. Exactly one → the target. Two or more → `ambiguous_request`.
   A `--request-id` bypasses the count: it names an excluded entry
   (`not_a_question`) or nothing at all (`request_gone`);
5. **normalize the target and validate the `answer` action's payload against
   that entry.** Only that action: `cancel` carries nothing to validate and
   must pass through even on a request this stage would otherwise refuse — a
   `secret` or empty-`questions` request is `actions: [cancel]` precisely so it
   can still be declined (design §5, §6). The only code that applies to
   `cancel` is `option_not_accepted`. This
   stage does not exist in an earlier draft of this plan and has no other home:
   `free_text_not_allowed`, `free_text_exclusive` and
   `multi_select_unsupported` need the per-question `allows_free_text` /
   `multi_select` metadata that only `normalize_pending_request` derives (and
   derives *per provider*); `unknown_question_id` needs its id list;
   `secret_answer_unsupported` is per request; `option_not_accepted` is the
   `actions[].accepts` rule (design §5) — e.g. `--answer` passed with `cancel`.
   So the service calls the normalizer even on the write path, and validates
   against what it returns;
6. derive the action per §6's three-state rule — `missing_answers` where it
   applies, and always on an empty `questions` list;
7. `helpers.build_question_response`;
8. `resolve_pending_request`, mapping `False` to `request_gone`. It lives on
   the **manager**, not on the registry stage 3 used
   (`agent/base_manager.py:255`), so reach it through
   `registry.find_manager_for_session(session_id)` (`agent/registry.py:87`).

- [ ] **Step 1: Write the failing tests** — one per rejection code in design §7,
      the happy path of each command, and `--raw` on a mixed list (one question
      entry, one out-of-scope entry, both carrying `raw`). The self-answer test
      passes an explicit `caller_session_id` in the payload: whether the CLI
      actually *stamps* it is a different claim, and it is Task 6's test
      (Step 4), not this one's — the CLI payload builder does not exist yet.
- [ ] **Step 2: Run, confirm failure**
- [ ] **Step 3: Implement**, and register both kinds in `_KIND_HANDLERS`:
      `session:pending_requests` → success `fetched`,
      `session:answer_pending_question` → success `updated`.
- [ ] **Step 4: Run the full suite**
- [ ] **Step 5: Commit** — `feat(core): read and answer a session's pending question`

---

### Task 6: the CLI surface

**Files:** `cli/pending_question.py` (new), `cli/__init__.py`,
`rpc/permissions.py` · Test: `tests/test_pending_question_cli.py` (new).

- [ ] **Step 1: Wire the commands**

Two different declaration shapes, and the design fixes them: the read is a
**Typer group with `invoke_without_command=True`** hanging off `session_app`
(design §5, like `session_app` itself at `cli/__init__.py:465-470`), so a second
read verb can be added later without moving the first; `answer` is a flat
command on `session_app`. `rpc/generator.py:68-70` registers such a group as its
own route either way, so the RPC/MCP names hold in both shapes — the choice is
about future room, not reachability.

Both bodies follow `cli/process_stop.py:40-70`: `ensure_server_available` →
local `lookup_session` pre-check (exit 1) → `transport.submit` →
`transport.wait` → emit. `--timeout` defaults to 30 on both. `--raw` rides the
read's payload.

**Stamp `caller_session_id`.** Nothing does it generically: the only precedent
is `cli/share_mutation.py:9-24`'s `_with_caller`, calling
`whoami.resolve_current_session()`, and it is per-command. Without the same call
here, the design's one security rule (§6, self-answer refusal) never fires on
either route — including MCP, where the value also comes from that client-side
resolution (`whoami.py:71-75` reads the `forced_session_id` contextvar).

Exit codes per design §7, and three of its four exit-1 validations live in the
command body, not in Typer: an unknown ACTION word (a positional argument, so
Typer accepts it and the body refuses it), a **malformed `--answer`** — the
format is `ID=VALUE` split on the **first** `=`, so a value may contain more and
an argument with none at all is the error — and a **non-positive `--timeout`**.
The fourth is the `lookup_session` guards above.

- [ ] **Step 2: Register the read as read-only**

`session/pending-request` into `COOKIE_READONLY_COMMANDS`. Without it the
command ships advertised as a mutation and `batch_read` refuses it.
`session/answer` stays out.

- [ ] **Step 3: Verify the generated surface**

```bash
cd /home/twidi/dev/twicc-poc
uv run twicc session --help
uv run twicc session x pending-request --help
uv run twicc session x answer --help
```

Confirm the RPC/MCP routes: `session/pending-request` →
`mcp__twicc__session_pending_request`, `session/answer` →
`mcp__twicc__session_answer`.

- [ ] **Step 4: Test that the CLI stamps the caller**

Assert that `cli/pending_question.py`'s payload builder calls
`resolve_current_session()` and puts the result under `caller_session_id`. This
is the half Task 5 could not test, and the one that decides whether the design's
security rule fires at all.

- [ ] **Step 5: Run the full suite** — including `tests/test_rpc_auth.py:99`,
      which asserts every allowlist entry is a real registry path.
- [ ] **Step 6: Commit** — `feat(cli): answer a session's pending question`

---

### Task 7: docs and the plugin bump

- [ ] **Step 1:** `SKILLS-AND-CLI.md` — both commands, their flags, their exit
      codes. A recurring blind spot; not optional.
- [ ] **Step 2:** the `twicc-session` skill — the two commands, the `actions`
      contract, and the fact that `--answer` keys by index on Claude and by id
      on Codex.
- [ ] **Step 3:** `plugin.json` — minor bump. No version number in the commit
      subject.
- [ ] **Step 4:** `send_message.py:151-154` — the `awaiting_user_input` refusal
      text names the new command. This is a user-facing runtime string, not
      documentation: commit it separately as `fix(core): point the blocked-send
      refusal at the new command`, not inside the `docs(cli)` commit below.
- [ ] **Step 5: Guard the prose, honestly scoped.**
      `tests/test_session_wait_documentation.py` exists for `session <ID> wait`
      only, written after a shipped `--wait-blocked` / `--blocked` mix-up. It is
      **not** command-parameterised: `_command()` hardcodes
      `commands["session"].commands["wait"]`, `INVOCATION` is a `wait` regex,
      `_prose_sources()` matches `"- \`wait "`, and `_refusal_flags` AST-walks
      `twicc.cli.session` from `stack = ["wait"]` while the new bodies live in
      `cli/pending_question.py`. Mirroring it whole is a parameterisation
      refactor of a 400-line file plus a per-command cross-reference table — out
      of scope here. Instead port the **one** check that matters: a copyable
      invocation names nothing but that command's real options. That needs more
      than three helpers — `_command` and `_real_options` (the command path),
      the `INVOCATION` regex, and `_copyable_spans()`, which hardcodes
      `span.startswith("wait [--")` (`:135`) and reaches through
      `_session_section_of_the_cli_doc()`. Those are the three the ported check
      actually calls (`:185-205`). Do **not** port `_prose_sources()`: it feeds
      the prose / cross-reference / refusal-clause tests, which need
      `SANCTIONED` and `CROSS_REFERENCES` — left alone here, so porting it
      yields dead code. **Assert the port selects a non-zero number of
      spans**: left half-parameterised it finds none and passes vacuously, the
      exact failure that file warns about at `:190-192`.
- [ ] **Step 6: Commit** — `docs(cli): document the pending-question commands`

---

### Task 8: end-to-end verification (manual, with the user)

The unit tests cannot reach a live agent.

- [ ] **Step 0: Ask the user to restart the backend.** Both services run inside
      it, so nothing below works against the old process. Restarting is a
      user-reserved operation (`CLAUDE.md`, *Operations Reserved to User*) —
      ask, do not run `devctl.py` on your own initiative.
- [ ] **Step 1: Claude question** — get a session to call `AskUserQuestion`,
      then `pending-request` (expect one `kind: question` entry with its
      options), then `answer answer --answer 1=…`. The widget must clear in the
      UI and the agent must resume with the answer.
- [ ] **Step 2: Partial and cancel** — with a multi-question widget, answer one
      of two and confirm the agent receives the native clarify message; then
      `answer cancel` on a fresh one.
- [ ] **Step 3: Codex question** — same with `toolRequestUserInput`, answer
      keyed by the native id.
- [ ] **Step 4: Out of scope** — block a session on a tool approval; confirm
      `pending-request` lists it `out_of_scope` with the right `reason`, and
      `answer` refuses it with `not_a_question`.
- [ ] **Step 5: `--raw`** — on both a question and an out-of-scope entry.
- [ ] **Step 6: Race** — call `pending-request`, answer the widget in the web UI,
      then `answer` with the stale `--request-id`. Expect `request_gone`, exit 3,
      not a silent success.
- [ ] **Step 7: The web UI is unchanged** — answer a question from the widget as
      before, end to end. Task 4 claims this; a human confirms it.
- [ ] **Step 8: Report** to the user.



## Risks and notes

- **The disguised-approval rule degrades toward accepting** (design §1). A test
  pins the `mcp_tool_call_approval` prefix; re-check it at every Codex
  re-vendoring, like the enum patch in the GPT-5.6 integration. That test is
  **Task 3 Step 1**, in `tests/test_pending_question.py` — not Task 2, which
  locks the WS wire output and predates the filter.
- **Hybrid sessions: exit 0 means accepted, not delivered** (design §7, race 5).
  Not a bug to fix here — the web UI has the same guarantee, through the same
  call.
- **Ephemeral sessions answer `session_not_found`**, deliberately (design §4.1).
  Do not "fix" it by skipping the lookup.
- **Do not add `updated_permissions` to a question answer**, however tempting the
  populated `permission_suggestions` field looks (design §5). That is the one
  edit that would reintroduce the security problem the narrow scope removes.
- All code, comments and docs in English.

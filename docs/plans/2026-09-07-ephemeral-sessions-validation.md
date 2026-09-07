# Ephemeral Sessions Validation

**Date:** 2026-09-07
**Design:** [Ephemeral Sessions Design](2026-09-05-ephemeral-sessions-design.md)
**Plan:** [Implementation Plan](2026-09-07-ephemeral-sessions-implementation-plan.md)
**Result:** all feature acceptance gates and independent implementation reviews pass.

## Verification results

| Check | Result |
| --- | --- |
| `uv run pytest -q` | 3,178 passed, 17 skipped, one pre-existing settings-description failure |
| `cd frontend && npm test` | 340 passed, zero skipped |
| `cd frontend && npm run build` | All five production bundles build successfully |
| Both provider integration flags, three runtime test files | 11 passed: six Codex, five Claude |
| Chromium 152, production frontend with controlled HTTP and WebSocket responses | Acceptance and control scenarios pass; no page errors |
| Ruff on all new Python modules and tests | Pass |
| `git diff --check` | Pass |

Runtime command:

```bash
TWICC_CODEX_INTEGRATION=1 TWICC_CLAUDE_INTEGRATION=1 uv run pytest \
  tests/test_ephemeral_codex_runtime.py \
  tests/test_claude_ephemeral_integration.py \
  tests/test_claude_ephemeral_scenarios.py -q --tb=short
```

The existing failure is
`tests/test_settings_cli.py::test_generic_key_descriptions_match_generic_keys`.
`mcpBaseUrl` and `externalMcpEnabled` exist in the committed settings defaults,
but their descriptions are absent from the committed CLI description map.
The same mismatch exists at the pre-implementation `HEAD` (`5254487c`).
The new `ephemeralExplainerSeen` setting has its description and frontend sync whitelist entry.
The unrelated MCP descriptions remain unchanged.

## Design acceptance matrix

| Design section | Implementation and evidence |
| --- | --- |
| 1–3: one prompt, browser-owned result, both providers | `agent/ephemeral.py`, `utils/ephemeralSessions.js`; creation, lifecycle and browser tests |
| 4.1: trusted entry point, validation, read-only identifiers | `core/services/session_creation.py`, `asgi.py`; `test_ephemeral_creation.py`, `test_ephemeral_ws.py` cover conflicts, cross-provider and mixed-mode races, existing rows, pending snapshots, and normal follow-ups |
| 4.2: no database row, result exactly once, clean terminal lifecycle | `base_manager.py`, `base_agent.py`; `test_ephemeral_lifecycle.py` covers success, failure, Stop, startup failure, shutdown, ownership and buffer cleanup |
| 4.3: Claude persistence, MCP, plugin and cron suppression | Claude agent/manager; provider tests inspect actual options and bypasses; real CLI parent/child control comparison proves no ephemeral JSONL |
| 4.4: Codex persistence, inherited configuration and final text | Codex agent/manager; provider tests cover literal MCP names in nested override tables, empty config, plugin feature flag, final-answer precedence, child/commentary exclusion, runtime child hold and factory cancellation |
| 4.5: final frame and in-memory error | Lifecycle and provider tests verify text, cost, duration, terminal status, exact-once delivery and content-safe logs |
| 4.6: ephemeral addendum | `agent/system_prompt.py`; tests verify no session work-directory or MCP contract; runtime child uses `fork_turns="none"` |
| 4.7–4.8: accepted external traces | Runtime tests inspect isolated provider homes. Agent-created task files remain allowed. Network requests still reach the model endpoint. No promise of anonymous or trace-free execution is made |
| 5.1: draft toggle and explainer | `MessageInput.vue`, settings stores and whitelist; browser toggle keeps the composer and sends the explicit ephemeral flag |
| 5.2: optimistic prompt, summaries and admission | `ephemeralSessions.js`, `draftStorage.js`, `data.js`; tests cover successful promotion, payload shaping, failed admission recovery and attachment summaries without retained bytes |
| 5.3: one chat view, working state, requests and controls | Session view/header/items list and `EphemeralActionsBar.vue`; browser verifies hidden composer/docks, no intermediate tool text, real approval form and response dispatch, Stop and Discard |
| 5.4: provider-native final rendering | `providers/ephemeralContent.js`; native envelope tests for both providers; browser verifies Markdown rendering and cost/duration state |
| 5.5: sidebar and persistent action exclusions | Session list/item/selection, `sessions.js`, `sidebarSessions.js`, notification guards; independent E/F review passes |
| 5.6: reload, binding, loss and deletion | `ephemeralStorage.js`, hydration before app mount; production action tests cover canonical rekey, first snapshots, pending controls, late errors, crash cleanup and unrelated normal failures |
| 5.7: persistent-session guard audit | Session view/header/list/selection, App shortcuts, reconciliation and notifications; independent review verifies launched-only predicates and no transcript/viewed backend calls in browser harness |
| 6–7: accepted losses and V1 limits | No continuation or persistent-session actions after launch. MCP/plugins, hybrid mode, TwiCC spawning/worktree requests and Codex goal creation remain excluded |
| 8–9: runtime risks and test plan | Eleven real-runtime tests cover parent/child transcript suppression, image input, tool approval, Stop and API error for both providers |

## Browser coverage

The temporary harness serves a bundle built from the production frontend.
It uses a fresh browser profile, Chromium 152, real Pinia actions, real IndexedDB,
real components, and controlled HTTP/WebSocket responses. It does not restart
or connect to the user's backend. External requests are intercepted.

The harness verifies:

- Toggle a draft; keep the composer; send the explicit mode without a layout.
- Replace the composer and dock panes with the ephemeral chat.
- Ignore intermediate working labels and render a tool approval form.
- Submit the approval through the normal provider response dispatcher.
- Render final Markdown and retain it after reload with an empty process snapshot.
- Discard completed content through the UI.
- Bind a Codex draft to a canonical ID and update the route.
- Reload after an absent runtime; show `lost` and retain prompt/attachment summaries.
- Keep media bytes out of the rendered and persisted prompt summary.
- Reload with a pending admission snapshot; preserve `running`.
- Click Stop before binding; send the canonical Stop after delayed binding.
- Receive `stopped`, then Discard.
- Make no backend transcript-items or viewed requests for ephemeral entries.

The harness isolates UI transport behavior. Separate real-runtime tests validate
provider behavior; it is not a live browser-to-provider end-to-end test.
Question and other existing pending forms use the unchanged shared dispatcher.
The browser exercise uses the tool-approval form.

## Independent review record

Spec and plan reviews pass before their separate commits.
Implementation ownership and review ownership differ.

- A/B backend: PASS after shared normal/ephemeral admission claims fix the mixed-mode creation race.
- C providers: PASS after cancellation closes the initialized Codex transport and ephemeral diff-capture helpers are skipped.
- D browser lifecycle: PASS after Discard removes content before awaiting Stop, hydration purges both IDs, late failures delete snapshots, and tombstone matching requires a defined alias.
- E/F rendering and actions: PASS after search/tab guards, intermediate working-label suppression, and the settings sync whitelist correction.

The final D reviewer independently runs all 15 lifecycle helper tests.
All review blockers are resolved. No provider/runtime gate is waived.
No dependencies, migrations, or user-server restarts are required for verification.

## Follow-up: inherited MCP configuration

A user report exposes a gap in the initial runtime coverage: the runtime tests
start threads without inherited MCP servers. The factory unit test incorrectly
expects TOML-quoted dotted RPC keys. Codex treats those quotes literally and
rejects the resulting transport-less server entry.

The factory now sends a nested `mcp_servers` table with literal server names.
Two regression cases call the real factory and bundled runtime with inherited
stdio servers, initially enabled or disabled, including names with dots and
quotes. Both cases fail before the fix and pass after it. No configured MCP
command runs, no ephemeral JSONL is written, and user configuration stays unchanged.

Follow-up verification: 35 tests pass across the provider, runtime and work-dir
files. The unchanged `test_runtime_ephemeral_transcript_and_final_answer[True]`
fails on subagent tracking/hold assertions in both runs. That test starts the
runtime directly and does not use the modified factory. This separate failure
remains open; the inherited-MCP regression cases both pass. Ruff and diff checks pass.

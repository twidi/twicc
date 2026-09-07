# Nested Subagents Validation

**Date:** 2026-09-07
**Status:** implementation and independent reviews complete

## Scope and gates

The feature covers Claude and Codex nested agents at arbitrary depth.
Storage parents identify the root; launch links identify the immediate launcher.
The UI uses regular agent tabs at every depth.

The design and implementation plan pass independent backend, frontend/API,
and Codex adversarial document reviews before implementation.
Document commit: `35e9b15ffa6d8961a50b1e054cfee087964aa2c8`.

MCP `process_wait` confirms session `01a07946-32bb-71d0-829c-33e70af669fc`
reaches `user_turn` at `2026-09-07T01:23:30.570572+00:00`.
Implementation begins after this gate.
Unrelated ephemeral-session work remains in its own commit, `54b294ed`.

## Requirement evidence

| Requirement | Implementation and evidence |
|---|---|
| Sidecar metadata, including depth 1 | `subagent_meta.py`; malformed/absent metadata and exact-tool race tests in `test_nested_agent_compute.py`; existing orphan-notification regressions |
| Structured result before async ack | Claude extraction and generic live/full compute; conflicting-envelope test |
| Async upgrade before stop counting | Existing-link upgrade on the same spawning tool; meta-first/ack-second live regression |
| Launcher ownership and unique prompt recovery | Shared recovery policy for live and full compute; sidecar authority, cross-tree, ambiguous prompt, and workflow tests |
| Historical queue completion | Shared notification parser, root completion backfill, separate apply channel; actual watcher queue-only stop test |
| Stable recompute and apply races | Actual compute/apply tests for both interleavings, zero churn, missing-sidecar backfill retention |
| Flat Codex roots at any depth | `subagent_roots.py`, producer and watcher; tests with 40 levels, multiple projects, queued root without DB writer, missing ancestry and cycles |
| Actual data migration | `0142_flatten_codex_subagent_parents`; historical-model callable tests verify roots, former parents, projects, null semantics and idempotence |
| Shared tree API | `build_subagents_state`; actual owner endpoints verify root/owner ids, result counts, latest child/tool completion, missing child rows and provider idle gates |
| Root lifecycle governs descendants | Store root identity and root cutoff in agent cards; real component computed tests and rendered browser checks |
| Event ordering | Shared owner/share cache handles fetch generations, per-agent revisions, pending completions and pending idle/wake evidence |
| Stop and navigation | Tool cards navigate and stop through root identity; header uses reverse link lookup; rendered navigation/reload and real SDK Stop tests |
| Comment root coordinates | Reactive provided comment context and ownership-chain root-spawn lookup; executable component test with delayed links |
| Snapshot shares | Ownership traversal rejects missing, conflicting and cyclic chains; HTTP tests cover hidden/visible subtrees, depth 40, include-off, root back edges and snapshot ceilings |
| Live shares | Owner-keyed links, persisted completion and narrow child idle/wake events; actual WebSocket tests and executable share dispatcher tests |
| No workflow or Codex pairing regression | Existing workflow and Codex suites plus workflow foreign-sidecar regression; v2 link creation unchanged |

## Historical replay

The replay copies existing Session and SessionItem data through a read-only
SQLite connection. Tests load these copies and sidecars into isolated provider
homes and a test database. They do not recompute the running instance.

- Claude `9e1cfb65-c745-43fc-bc5a-8a999804bf92`: 12 links after recompute.
  Eight root links remain. Launcher `acc4e760a52c96577` has the expected
  children `ad387380c886bae1d` and `ae1df3ede72f6b6da`, at lines 60 and 62.
  Both stop correctly. A second complete tree recompute preserves the links.
- Claude `e7932c3f-fb59-4d1b-9d45-7b64eb18639c`: 11 links after recompute.
  The three links owned by `a4a57eb737828fa4c` retain their background flags.
  A second complete tree recompute creates no duplicates.
- Codex `01a004e1-7a55-76e2-8130-f7398710088e`: the actual migration anchors
  child `01a004e1-e350-7573-897d-4dc23b121936` to the root.
  Its launch remains owned by `01a004e1-c168-7530-84e0-ce7de3cd995f`.
  Root total cost includes every item: `0.102992`.

Result: three private reference replay tests pass.
Private transcripts and runtime evidence remain outside the repository.

## Real provider runtime

```bash
TWICC_CLAUDE_INTEGRATION=1 uv run pytest tests/test_nested_subagents_runtime.py -q -s --tb=short
```

Two tests run the actual bundled Claude Code 2.1.259 through `ClaudeSDKClient`.
They use isolated homes and a controlled local model endpoint.

Both runs produce root → launcher → sync and async children.
Actual sidecars contain `spawnDepth=2` and `parentAgentId`.
The sync child and launcher finish while the async child remains active.
Natural async completion emits `completed`.
SDK `stop_task` for the nested async child emits `stopped`.
Nested Stop controls therefore remain enabled.

This verifies the real CLI and SDK behavior. Separate watcher/API tests verify
TwiCC ingestion and transport. It is not a browser-to-provider end-to-end test.

## Rendered browser acceptance

A temporary bundle imports the production app, Pinia store, router and components.
A fresh Chromium profile uses controlled HTTP/WebSocket responses.
The harness does not connect to or restart the user's backend.

The browser verifies nested View Agent and pulse, launcher idle with an active
child, root-based navigation, direct child URL reload, delayed tree fetch,
queue-only completion, root restart and stale synthetic cleanup.
The completed run reports no browser errors.

## Verification results

The final complete backend run reports 3,226 passed and 19 skipped.
One pre-existing test fails:
`tests/test_settings_cli.py::test_generic_key_descriptions_match_generic_keys`.
`externalMcpEnabled` and `mcpBaseUrl` have no generic CLI descriptions.
The test also fails alone. Its source and both settings modules match HEAD;
this feature does not modify them.

The final frontend run reports 364 tests passed.
The final Chromium run passes all eight scenarios with zero browser errors.
The production build, including the standalone share bundle, passes.
The final historical replay passes all three references after the compute corrections.

Independent implementation reviews approve backend/API/shares, Codex, compute,
and frontend state handling. Final review corrections cover:

- Child-side prompt recovery cannot choose between same-prompt siblings.
- Launcher recovery checks same-prompt tools across its entire transcript.
- Metadata remains authoritative despite prompt ambiguity.
- Idle/wake events before the first cached link survive an older REST response.
- Duplicate live links preserve REST idle and server completion state.
- Explicit null wake overrides prior idle state in main and share caches.

Independent correction checks pass 24 compute tests and 19 frontend tests.
Ruff passes on all feature Python files. After lint cleanup, 12 migration/API tests pass.

## Deployment

No dependency installation is required.
The running instance still needs a user-initiated restart through `devctl.py`.
Startup applies migration 0142 and Claude compute version 109.
No migration or restart runs against the user's instance during validation.

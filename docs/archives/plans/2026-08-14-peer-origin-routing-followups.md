# Peer Origin Routing — deferred follow-ups

**Status:** CLOSED. Every actionable item is done (see **Resolved**) and every performance item is
decided against (see **Do NOT act**). Nothing is open.

**One lesson worth keeping:** an entry here paraphrased a finding instead of quoting it, and the
paraphrase named the wrong file. The work went to the wrong place, and the real defect stayed open
under a "resolved" label. Quote a finding verbatim, or record where its text lives.
**Source:** the implementation of `docs/plans/2026-08-13-peer-origin-routing-plan.md`,
commit range `3584afc8..6a478a4c` on branch `peer-system`

Every open item below was found during implementation, triaged, and deliberately deferred. None is
load-bearing: the feature ships correctly without them. Each carries the reason it was deferred,
so a later reader can re-judge it without re-deriving the context.

Items already closed move to the **Resolved** section, which keeps what each one actually was.
Items triaged as **non-defects** are recorded at the end, so nobody re-opens them.

---

## Where the review findings live

The review workspace `.superpowers/sdd/2026-08-13-peer-origin-routing-plan/` was deleted with the
plan workspace, and the reviewer was a subagent, not a session — so it has no history of its own.
Two things survive:

- the SDD ledger, `/home/twidi/.twicc/scratch/922032b5-292f-45e8-9ac7-381e414fefd5/`
  `sdd-ledger-peer-origin-routing.md` — decisions and rulings only, no finding text;
- the reviewer's FULL report, which passed through the implementer session
  `922032b5-292f-45e8-9ac7-381e414fefd5` and is intact in its JSONL (line 323).

Anything that transited through a session is recoverable; a workspace on disk is not.

## Do NOT act on these

- **The settings write holds `_settings_lock` while the gate acquires it synchronously in the
  event loop.** `_merge_and_write` runs in a worker thread (`sync_to_async`) and holds the lock for
  the WHOLE read-modify-write, not just the file replacement; meanwhile any incoming request makes
  the event-loop thread block on the same lock inside `read_routing_settings`, so every request
  stalls, settings-related or not. **Owner decision, 2026-08-14: leave it.** The window is the time
  to validate and rewrite a small JSON file, it only opens when a human clicks Apply, and the same
  shape predates this work. The fix — publishing an immutable snapshot that readers take without
  the lock — touches every reader of `_cache` and must preserve the re-entrance the
  read-modify-write callers depend on. Same trade as the entry below: invisible gain, wide blast
  radius. Re-open only if a real latency problem is ever observed.
- **`normalize_public_origin` is called twice per changed field** in the settings write path (once
  in the `_merge_and_write` loop, once inside `validate_origin_settings`). Prescribed by the plan.
  **Owner decision, 2026-08-14: leave it.** The path runs only when a human clicks Apply on an
  address, a handful of times in an instance's life, and the function is pure and cheap — the
  performance gain is nil. The only benefit would be clarity, and the refactor (having
  `validate_origin_settings` return its `results` dict, then feeding `corrections` /
  `normalized_patch` from it) has to preserve two easily-lost guarantees: the `null` rejection must
  stay BEFORE normalization, and a type-rejected field must stay out of `changed_fields` or the
  same error is reported twice. Higher risk of breaking something than of leaving it.
- **The two lint findings** — `tests/test_origin_policy.py`'s unused plan-mandated `# noqa: E402`
  (RUF100, only under expanded rule sets: the project config does not enable E402) and three
  unused `full` unpacks in `tests/test_share_host_gate.py` (RUF059, matching a pre-existing
  pattern). They wait for the project-wide lint pass, deferred by an earlier decision. Fixing them
  piecemeal fights that decision.
- **`_policy_cache` is a single-slot memo updated without a lock.** Leave it. The tuple assignment
  is atomic, the rebuild is pure, so the worst case is a redundant rebuild, never a wrong policy.
  A snapshot-publishing fix for the lock entry above would absorb it anyway.
- **Do not back-propagate the `twicc:synced-settings-result` rename** into
  `2026-08-13-peer-origin-routing-plan.md`. That plan is a historical record and keeps the name it
  prescribed.

---

## Resolved

Closed on 2026-08-14, after a review of the whole list. Each was contained, fully covered by an
existing or added test, and carried no behavioural risk.

- **The one-way property — the JavaScript subset never rejects what Python accepts — was checked
  once, by hand.** It is now enforced on every run, WITHOUT a generator or a cross-language runner:
  the shared fixture carries `one_way_accepted_inputs`, a frozen list of adversarial inputs the
  backend accepts, and each side reads it with its own parser. `tests/test_public_origin.py` owns
  the list — it re-derives it from the same dimensions and fails if it drifts, so a Python contract
  change is caught there first and the fixture is regenerated from the builder's output.
  `frontend/src/utils/publicOrigin.test.js` asserts the property itself.
  The full product is ~300k inputs / ~28k accepted, too many to freeze; three bounded coverage sets
  replace it (every accepted host×port pair, every PAIR of decoration values on two structurally
  different hosts, one decoration at a time on each representative host) for 757 entries. The
  property was verified against the FULL 28k set once while building this, with zero violations.
  Both halves were mutation-tested: rejecting uppercase hosts in the JS subset fails the frontend
  test, dropping one fixture entry fails the Python one.
- **The popover Apply/result wiring was covered by source regexes, not by executed code.** The
  wiring now lives in `frontend/src/composables/useOriginSettingsForm.js`, driven by 16 executed
  tests. No new dependency: Vue's reactivity (`ref` / `computed` / `watch` / `nextTick`) runs as-is
  under the bare `node --test`, and the composable takes its store, hostname and event target by
  injection. It exposes `start()` / `stop()` instead of registering lifecycle hooks itself, so a
  test drives the same entry points the component does. The template is untouched: the composable
  returns the same flat names it used to declare. What stays in
  `stores/publicOriginSettings.test.js` is only what no test can execute — the Pinia store
  definition, the ASGI consumer, and the event-name link between `useWebSocket` and the form
  (that one now compares against the exported constant instead of a literal). **Do not re-add
  source regexes for the form's own behaviour.**

- **`synced_settings.read_routing_settings` double-acquired the `RLock`.** Both public readers now
  go through `_read_synced_settings_locked()`, which the caller enters holding the lock.
- **`public_origin.py` — redundant `isascii()`** removed: the `0x21..0x7e` range check already
  implies ASCII. The neighbouring `"%"` check does not, and stays.
- **`test_general_and_routing_first_reads_share_one_source_observation`** now waits 50 ms instead
  of 250 ms. The window only bounds regression-detection power; the passing path always consumes
  it whole.
- **The event name `twicc:origin-settings-result`** became `twicc:synced-settings-result`: the
  frame carries the result of ANY correlated synced-settings write, not only an origin. The
  implementation plan keeps the old name — it is a historical record.
- **IPv6 bracketing broke the Share-vs-current-host rule** (final-review Minor 8, the entry this
  list first recorded as "IPv6 hostname-hint bracketing in a Settings hint" — a lossy paraphrase
  that sent the first attempt at the wrong file; see the correction below).
  `window.location.hostname` brackets an IPv6 literal (`[::1]`), the parsed hint carries the bare
  hostname (`::1`, the shape Python's `PublicOriginResult.hostname` also uses). Compared as-is in
  `validateOriginSetting`, the rule could NEVER match on an IPv6 host, so a Share host equal to the
  current one slipped past the client check. Fixed by stripping the brackets on the browser value
  only — the bare form stays the contract on both sides. The backend Share/External conflict
  already caught the case whenever External was set, which is why it was invisible.
- **`own_display_name()` returned an unbracketed IPv6 hostname.** A separate, genuine defect found
  while acting on the paraphrase above: an IPv6 peer address advertised itself as `::1` instead of
  `[::1]`, and the value goes out in every handshake. Unrelated to Minor 8 — recorded on its own so
  the two are not confused again.
- **The "Your address" hint never mentioned routing** (final-review Minor 4). It covered HTTPS and
  machine-to-machine reachability, but not what the value DOES: a different address from External
  serves peer traffic only, the same address keeps the whole app reachable there. Nothing else
  taught that consequence once `peers.md` was the only other surface. One sentence added, as the
  reviewer asked.
- **An exact no-op Apply cleared the plain-HTTP Peer warning with no other feedback.** The warning
  now precedes the empty-patch early return, so it describes the value being applied.
- **A dropped connection discarded an in-flight Apply in silence.** The `wsConnected` watcher now
  reports it per field before clearing, under the same retyped-field guard used everywhere else.
  The message is deliberately NOT the existing "Not connected to the server — try again.": a
  refused send never left the browser, while a drop may follow a write the server already applied.
  Consequence accepted: a stale callout survives on a write that did succeed. It clears on the next
  keystroke or Apply, like every other error in the component. Tracking the fields to clear on
  reconnect was judged not worth the extra state — a stale callout dissipates, silence never does.

---

## Triaged as NON-defects — do not re-open

- **The `public_origin.py` empty-hostname branch IS reachable**, through the input `[]`. It was
  first reported as dead code; it is not.
- **The `origin_gate.py` docstring line naming `ShareHostGate`** is deliberate historical prose:
  it records what the gate replaced.
- **The "Not connected to the server" branch is NOT under-covered.** It was reported as
  near-unreachable, so the disconnected case looked unhandled. Both halves are in fact handled,
  each with its own message: with the backend down at page load `wsSendFn` is `null`, so Apply
  returns false and the branch fires; a drop mid-flight is caught by the `wsConnected` watcher (see
  **Resolved**). Closed 2026-08-14. Do NOT "fix" it by threading VueUse's `send()` return value out
  of `sendWsMessage` — that touches every caller in the app for no gain.

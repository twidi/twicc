# Daily Feature Telemetry Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans for inline implementation, then superpowers:requesting-code-review for independent review.

**Goal:** Measure the approved feature use and client profiles without new tables.

**Architecture:** Daily aggregates live in telemetry.json. Backend hooks and
authenticated frontend reports update them. Existing daily delivery serializes
buckets and anonymous client descriptions; configuration is read at send time.

**Tech Stack:** Python/Django, orjson, Vue/Pinia, existing Android bridge.

**Spec:** [Design](2026-11-11-telemetry-usage-design.md).

## Global Constraints

- Work only in feature/telemetry-usage; preserve the main checkout.
- No dependency additions, migrations, production deployment, or changelog.
- Four buckets: 0, 1, 2-5, 6-20, 21-50, 51-100, 101+.
- No collection while disabled or before notice acknowledgement.
- No device keys, content, or raw User-Agent in the outgoing payload.
- OpenRouter model is sent verbatim, as explicitly requested.

## Review Focus

- A manual consumer joining an automatic generation does not create a manual result.
- Repeated native started/resume commands count one playback.
- Disabled/old browser reports and pre-introduction days cannot backfill activity.
- Multiple tabs share a client identity; equal descriptions can represent several clients.
- Future OpenRouter model identifiers survive without an allowlist.

## Tasks

- [x] Add failing tests for counters, disabled collection, client deduplication,
  payload privacy, configuration snapshots, and introduction date.
- [x] Implement telemetry activity helpers and snapshot additions. Extend
  state/task initialization without changing send cadence.
- [x] Add browser-family/profile reporting and gated WebSocket transport.
  Test UA reduction, profile reuse, Android bridge data, and disabled sending.
- [x] Instrument generation origin/success, Web/native playback, and timeline
  activation. Test successful manual generation, automatic exclusion, cached
  reads, empty/failed audio, tests, notices, and reconnect deduplication.
- [x] Update collector transparency and design documentation. Verify focused
  backend/frontend suites and production build.
- [x] Dispatch independent reviewer; correct important findings and recheck.

## Execution ledger

- Worktree created from 62015100b84a3fa8336fd7f72fa0f5062881c330.
- Ruling: manual selections count as manual use; no extra source dimension.
- Initial delivery requires no commit or server start. After both review passes,
  the user requests a single commit containing the implementation.


## Validation and independent review

- Focused backend suites: 273 passed (telemetry activity/state/snapshot/task,
  settings mutations, native playback/timeline/protocol).
- WebSocket transport, sync integration, and origin suites: 83 passed.
- Summary service/views: 93 passed, 4 failed. All four failures are existing
  Claude timeline fixture failures at `events()[0]` (empty event list). The
  same four failures reproduce using archived sources and tests from the base
  commit, with 10 other selected baseline cases passing.
- Focused frontend suites: 241 passed (telemetry client, TTS, timeline,
  summaries, native integration/client, Android bridge and Gecko transport).
- Android JVM policy: 94 passed. Gecko app unit tests: 105 passed.
  Used the existing local JDK/SDK/Gradle cache offline. Regenerated Android
  i18n source fingerprints and embedded test bundles after bridge changes.
- `npm --prefix frontend run build`: passed; existing Vite import warnings.
- `git diff --check`: passed.
- Independent review found one Important issue: native `listen` also represents
  recovery and notification Resume. Fixed using an optional explicit collection
  epoch passed only by user Listen commands. Regression tests cover recovery,
  Resume, duplicate command replay, and stale epochs. Final independent recheck
  reports no remaining Critical or Important findings.
- No DB schema change, collector migration, new dependency, send cadence change,
  server start/restart, deployment, or merge. Delivery is on
  `feature/telemetry-usage` for user integration, with a commit requested after review.

## Integration notes

- The collector accepts additive fields under schema 1 without a migration.
  Deploy its updated transparency page from `telemetry-collector/` with
  `npm run deploy` when integrating; this execution does not deploy it.
- Restart the backend after integration to load the new instrumentation.
- Update Android to collect explicit native activations. Existing app versions
  still provide app metadata and native playback feedback, but omit activation
  attribution. Android version is bumped to 0.5.29 (versionCode 40), as requested.
  No APK publication is part of this task.
- Device counts describe client installations/browser profiles, not physical
  hardware. Separate browsers and cleared storage count as separate profiles.


## Second independent review

The user requests another full review after the Android version bump. The fresh
review identifies three Important edge cases, then independently rechecks fixes:

1. A collection check before the file lock permits a write after concurrent
   opt-out. `_collection_txn` now rechecks observed state, live settings, and
   operation epoch under the telemetry lock. It holds the settings lock through
   the write. Lock order is state then settings, matching bootstrap.
2. Retention limits entry count instead of dates. Sparse client digests can
   survive beyond 30 days. Today's partial entry can also evict the oldest
   complete day that the sender still emits, creating false zeros. Cleanup now
   enforces calendar expiry and keeps 30 complete UTC days plus today. Disabled
   periodic ticks also remove expired entries.
3. Simultaneous first tabs can create different localStorage keys. Client
   reporting now serializes creation with an origin-wide Web Lock where the
   browser provides it, then checks the epoch again before sending. Without
   Web Locks, including HTTP LAN origins, first-use creation remains best-effort.
   This limitation and unavailable-storage behavior are explicit in the design
   and transparency page; no unconditional exact-profile claim remains.

Regression tests fail before these corrections and pass after them. Independent
recheck passes 99 backend cases and 5 frontend cases. The reviewer finds no
remaining Critical or Important issue on the HTTPS/localhost Web Locks path,
subject to the documented fallback limitation.

Final verification for this pass:

- Telemetry, settings, native TTS, and WebSocket suites: 360 passed.
- Focused frontend suites: 243 passed.
- Production frontend build: passed, with existing Vite import warnings.
- Android policy and Gecko app unit suites: 199 passed; Gradle build successful.
- Android generated bundles/fingerprints refreshed. Version remains 0.5.28,
  versionCode 39; README paths match.
- The four baseline summary fixture failures remain documented above. This
  pass does not rerun that unchanged suite.

## Rebase onto local main

Rebase target: `c5b4c1968` (`main`). Check functional interactions as well as
textual conflicts:

- Main already uses Android 0.5.28/code 39. Advance this feature to
  0.5.29/code 40, including the README installation commands.
- Regenerate Android fixtures and fingerprints with main's native-message
  selection. Preserve its scoped translation hashes and smaller adapters.
  Include the new telemetry client and its crypto dependency in source hashes.
- Preserve main's Android configuration retention and terminal clipboard
  diagnostics. App metadata still uses `version` and `engine`. Native Listen
  attribution remains distinct from recovery and notification Resume.
- Preserve main's HTTP download cleanup middleware alongside the WebSocket
  telemetry hooks. No change to the telemetry send cadence or storage model.
- Recheck timeline admission and summary/native TTS integration against main's
  semantic decision changes. Eight summary fixture failures reproduce on an
  isolated archive of main itself; they are not introduced by the rebase.

Rebase validation:

- Backend feature/summary suites: 417 passed, 1 skipped. Eight existing summary
  failures reproduce on main. Four summary route tests cannot import main's
  new download module because this worktree lacks `aiofiles`.
- Frontend feature suites: 243 passed. Android generation/fingerprint suites:
  18 passed. Generation check and dependency-free source checker pass.
- Timeline decision, message association, and recovery suites: 242 passed,
  36 skipped. Both new telemetry adapter source mutations fail fingerprint
  verification as expected, without modifying source files.
- Production frontend build passes with existing Vite import warnings.
- Android policy, Gecko app, and WebView app unit suites: 312 passed offline.
  Gradle verifies the final regenerated fingerprints without task exclusions.
- Use the existing independent dependency installations with `uv run --no-sync`.
  No package installation, server restart, migration, or APK publication.
  WebSocket runtime tests still require main's newly added `aiofiles` dependency.

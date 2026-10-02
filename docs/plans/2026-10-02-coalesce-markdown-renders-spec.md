# Coalesce Markdown Renders Specification

## Status and scope

Priority 5 of `2026-10-02-streaming-performance-analysis.md`.
This document defines scheduling and obsolete-work control for `MarkdownContent.vue`.
The user authorizes this specification, independent adversarial review, commits, and a detailed implementation plan.
Implementation requires a later go.
Work stays in the existing `bugfix/stable-streaming-rows` worktree. Main stays unchanged.

## Problem and source evidence

`frontend/src/components/ui/MarkdownContent.vue::render` starts a new async render on each source or theme change.
Its block loop is sequential within one invocation. Separate invocations overlap at awaits.
`renderSeq` rejects an obsolete result only after the complete block loop.
`renderOneBlock` still parses, highlights, runs detached-node post-processing, and writes cache entries for obsolete invocations.
`postProcessIn` can render multiple Mermaid diagrams before control returns to the token check.
There is no unmount invalidation or KeepAlive suspension guard in this component.
The existing block cache stores completed HTML, not Promises.
Its key includes block source, Mermaid theme, and slash-command mode, but excludes document reference definitions.
`splitMarkdownBlocks` explicitly requires its shared `env.references` for cross-block reference links.
A code-tools action also uses `renderNestedMarkdown`; that user action is separate from source-driven rendering.

Priority 4 limits regular source publications to 30 Hz per streaming block.
An expensive render can still exceed that interval. Publication throttling does not prevent overlapping async work.

## Intended behavior

One mounted Markdown component owns at most one active source-driven document render.
It owns at most one pending request. Each newer request replaces the pending request.
A request snapshots source, effective Mermaid theme, and `tagSlashCommand`.
Only a current, eligible request can replace rendered blocks, evict cache entries, restore tools, or emit `rendered`.
The last complete DOM remains visible while newer content renders.
When input stops changing and work can complete, the latest eligible request eventually renders in full.
Intermediate obsolete sources have no obligation to appear.
A continuous stream can postpone commits until a request survives its async work.
No maximum render latency or frame rate is promised here.

## Request ownership and scheduling

A component-local coordinator owns a monotonic revision, one active operation, and one pending snapshot.
A changed input synchronously invalidates active commit ownership, before any promise continuation can commit old input.
Use a synchronous watcher for source, effective theme, and slash-command mode.
Watchers submit work; they do not start overlapping render invocations.
Actual draining starts through one queued microtask, not recursively inside the watcher.
All synchronous input changes before that microtask collapse to the last snapshot.
Deduplicate against active work only when it still owns the current eligible revision.
Deduplicate against the latest pending snapshot or an eligible successfully committed snapshot.
For A → B → A during an await, retain a fresh pending A after B invalidates the original A.
Never revive an invalidated revision. The old A settles without committing; then fresh A starts once.

The operation receives an `isCurrent()` predicate.
It checks ownership before parsing, before each block, after each await, and before commit.
At completion, the coordinator releases the active operation even after rejection or obsolescence.
It then starts only the latest pending eligible request.
There are no timers, RAF handles, cross-component queues, or global concurrency limits.

Failures release the coordinator. They must not create an unhandled promise rejection or a retry loop.
Keep the previous complete DOM. Consume every rejection.
Report once only if the failed request still owns the eligible current revision.
Use the configured Vue application error handler when present; otherwise use `console.error`.
Consume failures from obsolete, hidden, or disposed operations silently, then release the slot.
The error sink must not break slot release even if that sink itself throws.
A failed latest request ends `rendering` without emitting a successful `rendered` event.
A later changed input or an eligibility false-to-true transition can retry.
An identical failing input does not automatically retry while eligibility stays true.

## Obsolete-work checkpoints

Pass the ownership predicate through source-driven `renderOneBlock`, `postProcessIn`, and `renderMermaidIn`.
Check it after Markdown/Shiki completion and before allocating or processing its detached HTML root.
Check it before Mermaid acquisition, after acquisition, before each Mermaid render, and after each Mermaid await.
Check it before the remaining synchronous post-processing and before completed cache insertion.
An obsolete operation returns an explicit cancelled outcome, not successful empty HTML.
Do not cache cancelled or failed Mermaid output.

A synchronous parse, highlight, sanitizer, or DOM pass already executing cannot be interrupted.
An already-started third-party Promise can finish. Keep the active slot occupied until that work settles.
Do not release that slot early and start another document render beside it.
Checkpoints prevent the next expensive phase; they do not cancel library internals.

## Eligibility and lifecycle

Add `MARKDOWN_RENDER_VIEW_CONTEXT` to dependency-free `composables/streamPublicationKeys.js`.
`SessionItemsList` provides active view/session readiness: `props.viewActive && sessionActive && !isLoading && showVirtualScroller`.
This gate deliberately excludes `reveal.hidden`. Active initial positioning needs rendered canonical content for height measurement.
Do not reuse `STREAMING_VIEW_CONTEXT`: it includes the reveal gate. Keep that streaming contract unchanged.
Reuse optional `STREAMING_ROW_CONTEXT` for row intersection and scroller activity.
Do not acquire a streaming publication registry token from MarkdownContent.
Do not add IntersectionObservers or scroller geometry reads.
A component is eligible when mounted, attached to its active KeepAlive tree, and the document is visible.
An injected view must be active. An injected row must have an active scroller and intersection other than `outside`.
`unknown` intersection permits initial rendering. This avoids a circular dependency between rendered height and intersection discovery.
Absent contexts impose no restriction. Ordinary previews, tips, and share renderers still render.
Closed thinking bodies already unmount MarkdownContent; retain that behavior.

On eligibility loss, invalidate active ownership immediately and retain only the latest input for eventual resume.
Do not start parsing, commit, change cache, restore tools, or emit while ineligible.
An unfinished non-cancellable await can settle while hidden; then release the active slot without launching hidden work.
On eligibility gain, submit the latest input once. Do not replay hidden intermediate sources.
Restore an already committed identical input without extra work when no render was invalidated.
If unfinished work was invalidated, the latest input must render again, even when its text is unchanged.

On unmount, mark the coordinator disposed, invalidate ownership, discard pending input, and remove visibility listeners.
No later continuation can commit, emit, enqueue work, or change component caches after disposal.
Use KeepAlive hooks for attachment. Handle initial mount without duplicate scheduling.
Scope-disposal guards must also cover delayed `nextTick` tool-state restoration.
Check revision and eligibility after nested tool awaits before changing DOM or `codeToolsState`.

## Cache correctness and bounded retention

Preserve content-addressed completed-block reuse and occurrence-based Vue keys.
Include the document reference environment in completed cache identity.
Build an exact deterministic reference-context string from sorted `env.references` entries and their complete values.
Do not rely on a short hash as the only equality check.
Different definitions for the same reference must not share HTML, even when the block source is identical.
Theme affects Mermaid entries; slash-command mode affects the first block.
The injected file-link/media context retains its existing component-local lifetime contract.

Keep newly completed entries in a per-operation staging map until the full current operation succeeds.
Existing committed cache hits remain usable. Repeated identical blocks reuse entries from the staging map.
Discard staging on cancellation or failure. This prevents abandoned renders accumulating persistent cache entries.
On successful commit, retain only entries used by that request, then replace blocks and schedule guarded tool restoration.
The reference-context key can conservatively invalidate all blocks when definitions change.
No cross-component cache or shared in-flight Promise registry is introduced.

Single active document rendering removes duplicate in-flight document work within this component.
A snapshot identical to current active work creates no pending duplicate.
An invalidated active operation cannot satisfy that request.
Generic Promise deduplication across components or user-triggered nested renders is deferred.
It requires separate ownership, context, and lifetime rules and is not necessary for this scheduling change.

## Rendering compatibility

Preserve full-source parsing, sanitization, block splitting, Mermaid fallback, code tools, quote tools, and file/media links.
Preserve list, table, colon-container, comment, reference-link, and slash-command output.
Watch `tagSlashCommand` changes as well as source and theme.
TOC extraction remains unchanged. This priority does not reduce its synchronous parse cost.
Toolbar visibility and TOC visibility do not enter document render identity.
Do not change streaming buffering, parsed-content access, virtual scrolling, backend events, or animation behavior.
Do not defer unfinished fences or diagrams. Those belong to priority 6.

User-triggered nested Markdown rendering does not occupy the source-driven document coordinator.
It retains current tool behavior and must check component disposal and wrapper ownership after awaits before DOM mutation.
Tool restoration launched after commit must also verify its current document revision after awaits.
No obsolete tool operation can attach rendered content to a replacement or detached wrapper.

## Acceptance criteria

1. Hold render A with a deferred Promise. Submit B, C, and D. Active concurrency stays one; B and C never parse.
2. Resolve A. A cannot commit, emit, evict, or cache. D starts once and renders completely.
3. Submit identical current-active, pending, and committed snapshots. No duplicate work starts.
   Exercise A → B → A: invalidated A never commits; fresh A starts once after it settles.
4. Change source, theme, or slash mode during an await. Only the latest complete tuple commits.
5. Invalidate between blocks and between Mermaid diagrams. No subsequent expensive operation starts.
6. Hide/deactivate before await completion. No hidden commit or new parse occurs. Resume renders only the latest source.
7. Unmount before completion. No continuation mutates state, cache, DOM, or emits.
8. Reject current work. Old DOM remains; error reports once; coordinator accepts a later request without overlap.
   Reject obsolete, hidden, and disposed work: no report occurs. A throwing error sink cannot block release.
9. Change reference definitions with an unchanged link block. Its output uses the latest target.
10. Reuse successful unchanged blocks and repeated identical blocks. Cancelled staging never enters persistent cache.
11. Empty final source commits an empty block list and emits once. Final nonempty source renders complete canonical content.
12. Preserve nested code tools and links; stale tool continuations cannot mutate detached or superseded wrappers.
13. Render populated active conversation content during initial reveal positioning. Inactive views still start no work.

Use deferred Promises and counters, not arbitrary time delays, for concurrency and checkpoint tests.
Pure coordinator tests do not establish Vue lifecycle wiring or output compatibility.
Component-level tests or a source-extracted component harness must exercise actual integration functions and hooks.
A browser fixture must mount the real MarkdownContent component and the production conversation path.
Compare baseline and implementation with the same source feed and actual execution counts.
Check composer typing, theme changes, session switching, open thinking, full final output, and code/Mermaid tools.
Report throttled or blocked frame opportunities as inconclusive performance evidence.
Do not repeat the prior reading-above probe's fixed-wait assumption; observe successful navigation and component presence separately.

## Risks and limits

A long synchronous operation can still block the UI.
Different components can render concurrently. One stuck library Promise stalls only its own document coordinator.
Latest-only commits can appear less granular when async work takes longer than publication intervals.
Reference-aware keys can reduce cache hit rate. Correct links take precedence over stale cached HTML.
Visibility lifecycle wiring must preserve initial rendering and resume for all consumers, including standalone shares.
No guarantee covers the global application freeze without real workload validation.

## Review record

Round 1 finds three issues: initial reveal suppression, A → B → A invalidation, and obsolete-error reporting.
Corrections add a dedicated Markdown view gate, revision-aware deduplication, and an explicit guarded error sink.
Tool restoration also checks ownership after awaits.
Scoped re-review approves all substantive corrections.
A final editorial correction makes the cache section use the same revision-aware deduplication rule.

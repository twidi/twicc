# Chat Render Stability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking. Implementation requires a separate user instruction.

**Goal:** Remove measured unnecessary updates, then make snippet group closure follow meaningful changes.

**Architecture:** Correct reference stability at the source before changing popover behavior. Keep the current popover watches during subjects 1–3. Measure actual Vue `updated` hooks before and after each correction.

**Tech Stack:** Vue 3, Pinia, custom virtual scroller, Web Awesome, node:test, browser fixtures.

**Spec:** `/home/twidi/.twicc/scratch/01a114a6-2c4b-7922-93ae-0826105af435/render-diagnosis/report.md`, plus the user's request to retain current popover behavior during upstream corrections.

## Global Constraints

- Stay on the current branch. Preserve all existing changes.
- Do not modify popover closure behavior before subjects 1–3 pass their checks.
- Preserve genuine project, session, task, activity, snippet, and streaming updates.
- Do not treat `renderTriggered` as proof of an executed render.
- Do not install packages, restart servers, or modify backend broadcasts for this work.
- Use English for code, comments, tests, and documentation.
- Do not update the changelog or create commits without a separate user request.

## Review Focus

- Real project renames and directory changes must refresh snippet placeholders.
- Streaming can change parsed content while preserving the visual item reference.
- Virtual rows must preserve measurements, scroll position, and entrance/exit animations.
- Equivalent snapshots must stay stable; changed task or activity values must remain visible.
- Popovers must close for genuine context changes and item selection on desktop and mobile.

## File Responsibilities

- `frontend/src/stores/data.js`: project-name cache, visual-item stabilization, activity getters, session snapshots.
- `frontend/src/utils/orchestrationActivity.js`: descendant process activity summaries.
- `frontend/src/components/virtual-scroller/VirtualScroller.vue`: row rendering and scoped slots.
- `frontend/src/components/virtual-scroller/VirtualScrollerItem.vue`: row measurements and rendering boundary.
- `frontend/src/components/session/detail/SessionItemsList.vue`: chat-specific row slot and scroller integration.
- `frontend/src/components/message/MessageInput.vue`: composer context and resolved snippets.
- `frontend/src/components/message/MessageSnippetsBar.vue`: composer snippet group integration.
- `frontend/src/components/ui/GroupedSnippetEntries.vue`: shared group popover state and closure rules.
- Existing focused tests and the saved render-diagnosis fixture provide regression checks.

## Subject 1: Project-name Cache

**Interface:** Preserve `updateProject(project)` and `getProjectDisplayName(projectId)`.

- [x] Capture existing `name` and `directory` before `updateProject` mutates the project.
- [x] Add regression checks for unchanged metadata, real rename, changed directory, and a newly inserted project.
- [x] Invalidate `projectDisplayNames[project.id]` only when its display-name inputs change.
- [x] Preserve updates to all other project fields and replacement of nested settings.
- [x] Run Claude and Codex project-update scenarios with the original popover watches.

**Acceptance:** Unrelated project metadata produces zero composer updates. Snippet and context references remain stable. The group stays open. A real rename refreshes the display name and snippet placeholders.

## Subject 2: Chat Visual Items and Row Wrappers

**Interfaces:** Preserve `recomputeVisualItems(sessionId)`, parsed-content accessors, and the scroller slot contract.

### 2.1 Stable Visual-item Arrays

- [x] Add a regression check for a same-content visual recompute.
- [x] Reuse the previous array when membership, order, and item references remain identical.
- [x] Verify insertion, deletion, display-mode changes, and group expansion still update the list.

**Acceptance:** A same-content recompute does not update the chat list. Structural changes remain visible.

### 2.2 Stable Working-row Content

- [x] Identify every visible field of the synthetic working row.
- [x] Add checks for identical status and a genuinely changed working label.
- [x] Preserve parsed-content identity when those visible fields remain unchanged.
- [x] Verify label, tool, and waiting-status changes still update the working row.

**Acceptance:** An unchanged working row does not update. A changed working label remains visible.

### 2.3 Stable Row Rendering Boundary

- [x] Reproduce the 101-wrapper baseline and record unchanged wrapper props.
- [x] Trace scoped-slot dependencies before selecting a stable row boundary.
- [x] Remove forced updates of unchanged historical wrappers during working-label changes.
- [x] Verify streaming on the same item reference, index changes, and animation state changes.
- [x] Run scroll, loading, resume, and row-measurement regression checks.

**Acceptance:** Working-row changes do not update unchanged historical wrappers. Streaming, measurements, animations, and scroll behavior remain correct. Do not memoize only the item reference.

## Subject 3: Orchestration and Tasks Snapshots

### 3.1 Orchestration Activity

**Interfaces:** Preserve `getOrchestrationActivity()` results and descendant-activity semantics.

- [x] Add checks for equivalent process snapshots and genuine descendant state transitions.
- [x] Preserve summary identity when its activity values remain equivalent.
- [x] Reduce dependencies on unrelated process-map replacements where possible.
- [x] Measure orchestration indicators and their slot hosts after equivalent snapshots.

**Acceptance:** Equivalent snapshots produce no indicator updates. Descendant start, stop, removal, and parent changes still update the indicators.

### 3.2 Tasks Snapshots

**Interface:** Preserve the existing session `tasks` payload and Tasks getter contract.

- [x] Trace full-session snapshot merging before changing reference handling.
- [x] Add checks for equivalent task payloads, changed content/status/order, and task removal.
- [x] Preserve task object and item-array references when the complete payload remains equivalent.
- [x] Verify the Tasks pane and progress indicators update for genuine changes.

**Acceptance:** Equivalent task snapshots produce no Tasks updates. Changed tasks, explanation, metadata, and removal remain observable.

## Subject 4: Popover Context and Closure

**Dependency:** Complete and measure subjects 1–3 first.

**Interfaces:** Preserve shared group selection, item application, dismissal, and composer long-press behavior.

- [x] Record legitimate composer updates that still close an otherwise unchanged group.
- [x] Replace the inline composer context array with a stable computed context.
- [x] Define closure rules for actual session, project, provider, workspace, and collapsed-state changes.
- [x] Define closure rules for genuine entry changes and disappearance of the open group.
- [x] Prevent closure when equivalent context or entries receive new references.
- [x] Verify group toggle, group switching, outside click, and item selection on desktop and mobile.
- [x] Verify composer long press inside groups and normal terminal snippet/combo application.

**Acceptance:** Unrelated updates preserve the open group. Meaningful context changes and existing dismissal actions still close it. Terminal snippets and combos retain normal interaction without long press.

## Execution Order and Final Validation

- [x] Execute subjects 1, 2, 3, then 4. Orchestration and Tasks are independently reviewable sub-tasks.
- [x] Keep before/after counts for each measured scenario.
- [x] Run focused tests after each correction. Run `cd frontend && npm test` after integration.
- [x] Repeat the settled browser scenarios for Claude and Codex without diagnostic overrides.
- [x] Confirm no browser errors and no unexpected API writes.
- [x] Inspect the final diff and run `git diff --check`.

This document records task decomposition. It does not authorize implementation or commits.

# Unified Tooltip Component (`AppTooltip`)

**Date:** 2026-02-22
**Branch:** `feature/unified-tooltip-component`

## Problem

The codebase has inconsistent tooltip usage:
1. **`wa-tooltip` with boilerplate** — 61 instances across 11 files, each requiring:
   - `import { useSettingsStore }` + `const settingsStore = useSettingsStore()`
   - `const tooltipsEnabled = computed(() => settingsStore.areTooltipsEnabled)`
   - `v-if="tooltipsEnabled"` on every `<wa-tooltip>`
   - An `id` on the target element and a matching `:for` on the tooltip
2. **Native `title=` attributes** — 17 instances using browser-native tooltips instead of the system
3. **Missing tooltips** — Several interactive icon-only elements with no tooltip at all

## Solution

Create an `AppTooltip.vue` wrapper component that:
- Internally handles the `tooltipsEnabled` check (no `v-if` needed at call sites)
- Accepts a `for` prop (same as `wa-tooltip`) for ID-based targeting
- Passes through all other props/attrs to the underlying `wa-tooltip`
- Keeps the slot for tooltip content

### Component API

```vue
<AppTooltip :for="elementId">Tooltip text</AppTooltip>
```

That's it. No store import, no computed, no v-if needed in the parent.

## Files Changed

### New file
- `frontend/src/components/AppTooltip.vue`

### Migrated from `wa-tooltip` → `AppTooltip` (remove boilerplate)
- `SessionHeader.vue` (22 tooltips)
- `SessionList.vue` (10 tooltips)
- `ProjectList.vue` (4 tooltips)
- `ProjectDetailPanel.vue` (4 tooltips)
- `SessionItem.vue` (4 tooltips)
- `SettingsPopover.vue` (1 tooltip)
- `ConnectionIndicator.vue` (1 tooltip)
- `JsonViewer.vue` (1 tooltip)
- `ProjectProcessIndicator.vue` (1 tooltip)

### Migrated from `title=` → `AppTooltip`
- `FilePane.vue` — "Previous change", "Next change", "Toggle markdown preview"
- `MediaPreviewDialog.vue` — "Previous", "Next"
- `PendingRequestForm.vue` — "Collapse"/"Expand" toggle (×2)
- `MediaThumbnailGroup.vue` — item name, "Remove"
- `MessageInput.vue` — "Attach files", attachment count
- `GitPanelHeader.vue` — commit label
- `FileTreePanel.vue` — selected file path
- `CommitMessageData.vue` — commit message (truncated text)
- `AuthorData.vue` — author info (truncated text)
- `CommitNode.vue` — "View Commit" (×2)

### Added missing tooltips
- `SessionToastContent.vue` — "Go to session" button
- `SessionList.vue` — menu trigger (ellipsis icon)

### Cleanup
- Remove `tooltipsEnabled` computed + `useSettingsStore` import from components that only used them for tooltips
- Keep `useSettingsStore` imports where components use other settings (e.g., `showCosts`, `displayMode`)

## Notes
- `ContentList.vue` `:title="entry.item.title"` is a **component prop** (to `DocumentContent`), not an HTML tooltip — no change needed
- `CustomNotification.vue` `:data-notivue-has-title` is a data attribute, not a tooltip — no change needed
- `CommitNode.vue` has its own custom tooltip system (`CommitNodeTooltip.vue`) for hover — the `title` there is a simple fallback, we replace it with our system
- GitLog TypeScript components need `useId()` for generating tooltip target IDs

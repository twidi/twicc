# Search Frontend — Design Spec

## Goal

Add a full-text search UI to TwiCC that lets users search across all session messages (user + assistant) and navigate to matching sessions. The backend API (`GET /api/search/`) is already implemented using Tantivy.

## Decisions

- **Dedicated overlay** — not integrated into the sidebar session list (that may come in V2).
- **Trigger**: "+" button next to the existing filter input in the sidebar + `Ctrl+Shift+F` keyboard shortcut + Command Palette command.
- **State preservation** — the overlay remembers query, results, filters, and visited results when closed and reopened.
- **Click behavior V1** — navigating to the session only. Scrolling to the specific matched message is V2.
- **No new backend changes** — the API already returns everything needed.

## Component: `SearchOverlay.vue`

New SFC mounted in `App.vue` alongside `CommandPalette`. Uses a `wa-dialog` in modal mode.

### Opening

Three entry points, all dispatching the same `CustomEvent('twicc:open-search')`:

1. **Keyboard shortcut** `Ctrl+Shift+F` — global listener in `App.vue` (same pattern as `Ctrl+K` for Command Palette).
2. **"+" button** in the sidebar header row of `ProjectView.vue`, next to the existing "Filter sessions..." `wa-input`. The "+" icon communicates "more search options" rather than looking like a submit button.
3. **Command Palette** entry `nav.search` ("Search sessions...") in `staticCommands.js`.

`SearchOverlay.vue` listens for `twicc:open-search` and opens its dialog.

### Closing

- `Esc` or backdrop click — closes the dialog but **preserves state** (query, results, filters, visited set).
- Clicking a result — closes the dialog and navigates to the session via router.
- State resets only when the user clears the search input.

### Layout

**Desktop:** `--width: min(720px, calc(100vw - 2rem))`, positioned near top with `margin-top: 10vh`. Uses `without-header` (custom header managed inside the component).

**Mobile (< 640px):** Full-screen overlay.

### Internal structure

```
┌─────────────────────────────────────────────┐
│  🔍 [search input ................] [Esc]   │
│  [Project ▾] [Author ▾] [Date ▾] [☐ Arch]  │
├─────────────────────────────────────────────┤
│  ┌─ Session result card ──────────────────┐ │
│  │ Title                  ProjectBadge 2j │ │
│  │ [user] ...snippet with <b>match</b>... │ │
│  │ [asst] ...another <b>match</b>...      │ │
│  └────────────────────────────────────────┘ │
│  ┌─ Session result card ──────────────────┐ │
│  │ Title                  ProjectBadge 1w │ │
│  │ [user] ...snippet with <b>match</b>... │ │
│  └────────────────────────────────────────┘ │
│  ...                                        │
│  [Load more]                                │
├─────────────────────────────────────────────┤
│  ↑↓ navigate · Enter open · Esc close  1-20│
└─────────────────────────────────────────────┘
```

### Search input

- `wa-input` with magnifying-glass icon (`slot="start"`), `with-clear` for reset.
- Autofocus on dialog open (`@wa-after-show`).
- Debounced at 300ms using the existing `utils/debounce.js` utility.
- Minimum query length: 2 characters before triggering API call.

### Filter bar

Dropdowns below the search input:

| Filter | Control | API param | Values |
|--------|---------|-----------|--------|
| Project | `wa-select` | `project_id` | Populated from `store.getProjects` |
| Author | `wa-select` | `from` | "Any", "User", "Assistant" |
| Date | `wa-select` | `after`/`before` | Presets: "Any", "Last 7 days", "Last 30 days", "Last 3 months" |
| Archived | Checkbox | `include_archived` | Default: unchecked |

Changing any filter re-triggers the search (debounced).

### Result cards

Each card represents one session group from the API response:

- **Session title** — bold, clickable.
- **Project badge** — project name with project color from store (`store.projects[result.project_id]?.color`). Similar style to badges used elsewhere in the app.
- **Relative date** — `wa-relative-time` based on the most recent match timestamp.
- **Snippets** — each match rendered as:
  - A small role label ("user" / "assistant") in a muted badge
  - The snippet HTML rendered via `v-html` (Tantivy wraps matches in `<b>` tags)
  - Max 3 snippets shown per session; if more, show "+N more matches"
- **Visited indicator** — results already clicked in this search session get reduced opacity and a "✓" indicator. Tracked in a local `Set<session_id>`.
- **Left border accent** — colored line on the left of each card (brand color).

### Keyboard navigation

Same pattern as `CommandPalette.vue`:

| Key | Action |
|-----|--------|
| `↑` / `↓` | Move selection highlight |
| `Enter` | Open selected result |
| `Esc` | Close overlay |
| `Home` / `End` | Jump to first/last |
| `PageUp` / `PageDown` | Jump ~5 results |

Focus stays in the input. Selection is visual only (outline on the active card). The active card scrolls into view automatically.

### State management

All state lives in the component instance (reactive refs), not in a Pinia store or URL:

| State | Type | Preserved on close? |
|-------|------|---------------------|
| `query` | `ref('')` | Yes |
| `results` | `ref([])` | Yes |
| `totalSessions` | `ref(0)` | Yes |
| `offset` | `ref(0)` | Yes |
| `filters` | `reactive({})` | Yes |
| `selectedIndex` | `ref(0)` | Yes |
| `visitedSessionIds` | `reactive(new Set())` | Yes |
| `isLoading` | `ref(false)` | No (reset on open) |
| `error` | `ref(null)` | No |

State resets when `query` becomes empty (watcher).

### API integration

```javascript
const res = await apiFetch(`/api/search/?${params}`)
```

Uses `apiFetch` from `utils/api.js`. Query params built from `query` + active filters. Debounced at 300ms via `utils/debounce.js`.

**Error handling:**
- HTTP 503 → "Search index is still being built" (show progress from store)
- HTTP 400 → "Invalid search query" (show error message)
- HTTP 500 → "Search error, please try again"

### "Index not ready" state

When `store.searchIndexProgress?.completed !== true`:

- The "+" button in the sidebar is still visible but tapping it opens the overlay with a disabled input and a message: "Search index is being built..." with a progress bar sourced from `store.searchIndexProgress`.
- The search is non-functional until the index is ready. No API calls are made.

### Navigation on result click

1. Close the dialog (preserve state).
2. Add `session_id` to `visitedSessionIds`.
3. Navigate via router: determine the correct route based on the result's `project_id` and `session_id`. Use `router.push()` (lazy import to avoid circular imports per CLAUDE.md rules).

## Integration points

### `App.vue`

- Import and mount `<SearchOverlay ref="searchOverlayRef" />`
- Add `Ctrl+Shift+F` listener in the global keydown handler (alongside existing `Ctrl+K`).
- Forward to `searchOverlayRef.value?.open()`.

### `ProjectView.vue`

- Add a `wa-button` with `circle` variant and `plus` icon next to the existing `wa-input` filter in `.sidebar-header-row`.
- On click: `window.dispatchEvent(new CustomEvent('twicc:open-search'))`.

### `staticCommands.js`

Add a new command:

```javascript
{
    id: 'nav.search',
    label: 'Search sessions...',
    icon: 'magnifying-glass',
    category: 'navigation',
    action: () => window.dispatchEvent(new CustomEvent('twicc:open-search')),
}
```

### `main.js`

No new Web Awesome component imports needed — `wa-dialog`, `wa-input`, `wa-icon`, `wa-button`, `wa-select`, `wa-option` are all already imported.

## Out of scope (V2)

- Scroll to specific matched message within a session (using `line_num` from API).
- Highlight the matched message with a flash animation.
- Search results integrated into the sidebar session list.
- Infinite scroll pagination (V1 uses "Load more" button).
- Search query syntax help / autocomplete.

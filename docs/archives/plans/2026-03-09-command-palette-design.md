# Command Palette (Ctrl+K) — Design

## Overview

Add a command palette accessible via `Ctrl+K` / `Cmd+K`, providing quick access to existing UI actions through a searchable, keyboard-navigable overlay. Standard behavior matching what power users expect from tools like VS Code, JetBrains, Raycast, etc.

**Scope:** Intermediate — only actions that already exist in the UI. No new functionality, ~33 commands.

## Architecture

```
App.vue
└── CommandPalette.vue (custom dialog, teleported)
    ├── Search input (native styled)
    ├── Breadcrumb (nested mode)
    └── Command list (scrollable, keyboard-navigated)

composables/
└── useCommandRegistry.js
    ├── registerCommand({ id, label, icon, category, action, when?, items? })
    ├── unregisterCommand(id)
    ├── commands (computed: filtered by `when` context)
    └── openPalette() / closePalette() / isOpen

utils/
└── fuzzyMatch.js
    └── fuzzyMatch(query, text) → { match, score, ranges }
```

### Principles

- **`useCommandRegistry`** is a singleton composable (module-level shared state with `ref`/`computed`, not a Pinia store).
- **Decentralized registration:** each component/store registers its own commands (via `onMounted`/`onUnmounted` or directly in stores). No monolithic command file.
- **`CommandPalette.vue`** is instantiated once in `App.vue`.
- Global `Ctrl+K` / `Cmd+K` listener in the registry composable or `App.vue`.

### Command Schema

```js
{
  id: 'session.archive',         // unique identifier
  label: 'Archive Session',      // display label
  icon: 'archive',               // Web Awesome icon name
  category: 'session',           // grouping category
  action: () => { ... },         // executed on selection
  when: () => boolean,           // optional, controls visibility
  items: () => [...],            // optional, for nested sub-selection
}
```

Commands with `items` open a nested sub-selection instead of executing directly. Each item has `{ id, label, action, active?: boolean }` where `active` marks the currently selected option.

## Commands Inventory

### Navigation
| Command | Sub-selection | Condition |
|---|---|---|
| Go to Home | — | always |
| Go to Project… | project list | always |
| Go to Session… | session search | always |
| Go to All Projects | — | always |
| Switch to Chat tab | — | session open |
| Switch to Files tab | — | session open |
| Switch to Git tab | — | session open + has git |
| Switch to Terminal tab | — | session open |

### Session Actions
| Command | Sub-selection | Condition |
|---|---|---|
| Rename Session | — | session open, not draft |
| Archive Session | — | session open, not draft, not archived |
| Unarchive Session | — | session open, archived |
| Pin Session | — | session open, not pinned |
| Unpin Session | — | session open, pinned |
| Stop Process | — | session open, process running |
| Delete Draft | — | session open, is draft |
| Focus Message Input | — | session open |

### Creation
| Command | Sub-selection | Condition |
|---|---|---|
| New Session | — | in a project |
| New Session in… | project list | always |
| New Project | — | always |

### Display
| Command | Sub-selection | Condition |
|---|---|---|
| Change Theme… | System / Light / Dark | always |
| Change Display Mode… | Conversation / Simplified / Detailed / Debug | always |
| Toggle Show Costs | — (toggle) | always |
| Toggle Compact Session List | — (toggle) | always |
| Increase Font Size | — | always |
| Decrease Font Size | — | always |
| Toggle Editor Word Wrap | — (toggle) | always |
| Toggle Side-by-Side Diff | — (toggle) | always |

### Claude Defaults
| Command | Sub-selection | Condition |
|---|---|---|
| Change Default Model… | model list | always |
| Change Default Effort… | Low / Medium / High | always |
| Change Default Permission Mode… | mode list | always |
| Change Default Thinking… | Adaptive / Disabled | always |

### UI
| Command | Sub-selection | Condition |
|---|---|---|
| Toggle Sidebar | — | in ProjectView |
| Focus Session Search | — | in ProjectView |
| Open Settings | — | always |

## UX Behavior

### Opening / Closing
- `Ctrl+K` (or `Cmd+K` on Mac) opens the palette.
- `Escape` closes the palette (or returns to root level if nested).
- Clicking outside closes the palette.
- Palette closes automatically after executing a command.

### Root Mode (no search text)
- Commands grouped by **category** with visual separators (small grey category labels).
- Category order: Navigation → Session Actions → Creation → Display → Claude Defaults → UI.
- Commands whose `when` returns `false` are **hidden** (not greyed out).

### Search Mode (text typed)
- Categories disappear.
- Results sorted by **fuzzy match score**.
- Matched characters are **highlighted** in each label.
- First result is **pre-selected** automatically.

### Nested Mode (sub-selection)
- A **breadcrumb** appears left of the input (e.g., `Theme ›`).
- Search field clears and filters sub-options.
- Currently active option marked with a **check** (✓).
- `Escape` returns to root level (without closing).
- Selecting an option executes the action and closes the palette.

### Keyboard Navigation
| Key | Action |
|---|---|
| `↑` / `↓` | Move selection |
| `Enter` | Execute command or enter sub-level |
| `Escape` | Go up one level or close |
| `Home` / `End` | First / last item |
| `Page Up` / `Page Down` | Jump several items |

### Visual Layout
Each command row shows:
- **Icon** on the left (Web Awesome icons)
- **Label** text
- **State indicator** on the right for toggles (on/off badge or check)
- **Chevron** `›` on the right for commands with sub-selection

### Positioning
- Horizontally centered, positioned in the **upper third** of the screen.
- Width: `min(560px, calc(100vw - 2rem))`.
- Max list height: ~400px with scroll.

## Integration Points

### Where Commands Are Registered

| Commands | Registered in | Mechanism |
|---|---|---|
| Navigation (Home, projects, sessions, tabs) | `useCommandRegistry.js` (static) + `data.js` store for dynamic lists | `items: ()` reads store |
| Session actions (archive, pin, rename…) | `SessionView.vue` or `SessionHeader.vue` via `onMounted`/`onUnmounted` | `when: ()` reads current session state |
| Creation (new session, new project) | `useCommandRegistry.js` (static) | `action` calls store methods |
| Display (theme, display mode, toggles…) | `useCommandRegistry.js` (static) | `action` modifies `settingsStore` |
| Claude defaults | `useCommandRegistry.js` (static) | same |
| UI (sidebar, search, settings) | `ProjectView.vue` via `onMounted`/`onUnmounted` | contextual `when` |

### Interactions with Existing Components
- **Rename:** calls the same logic as the rename button (opens `SessionRenameDialog`).
- **New Project:** opens `ProjectEditDialog`.
- **Open Settings:** programmatic trigger of `SettingsPopover`.
- **Focus Session Search:** focuses the sidebar search input.
- **Toggle Sidebar:** manipulates the same localStorage state as the split-panel drag.

### What Doesn't Change
- No existing UI action is removed — the palette is a **complementary access**, not a replacement.
- Existing components are not structurally modified, only `registerCommand` / `unregisterCommand` calls are added.

# Dev Tools Panel (Skeleton)

**Date:** 2026-02-05
**Status:** DRAFT

## Overview

Add a collapsible panel at the bottom of the session view for future developer tools. This panel is **session-scoped** (not tied to a specific subagent tab) and persists across tab switches within a session.

This spec covers **only the panel skeleton** (container with resize, toggle, and mobile overlay). The panel content will be defined in separate specs. For now, the panel will be empty with a simple placeholder.

## Design Principles

**This panel must work EXACTLY like the existing sidebar in `ProjectView.vue`**, but oriented horizontally (bottom) instead of vertically (left):

- Same localStorage pattern (direct, not via settings store)
- Same CSS-only toggle mechanism using hidden checkbox
- Same mobile overlay behavior with backdrop
- Same resize logic with collapse threshold

## Visual Layout

### Desktop

```
┌──────────┬───────────────────────────────────────────────┐
│ Sidebar  │  SessionHeader                                │
│          ├───────────────────────────────────────────────┤
│          │  [Session] [Agent "xxx"]     <- tab-group     │
│          ├───────────────────────────────────────────────┤
│          │                                               │
│          │  Session/Subagent content (messages)          │
│          │  (includes MessageInput at bottom)            │
│          │                                               │
│          ╞═══════════════════════════════════════════════╡ <- Resize handle (drag to resize)
│          │                                               │
│          │  DevToolsPanel content (empty/placeholder)    │
│          │                                [Toggle ▼]     │
│          │                                               │
└──────────┴───────────────────────────────────────────────┘
```

### Mobile (Overlay at 90% height)

```
┌─────────────────────────────────────────────────┐
│  ~10% visible - backdrop (click to close)       │
├─────────────────────────────────────────────────┤
│                                  [Toggle ▼]     │
│                                                 │
│  DevToolsPanel content (empty/placeholder)      │
│  Takes ~90% of viewport height                  │
│                                                 │
└─────────────────────────────────────────────────┘
```

## File Structure

```
frontend/src/
├── components/
│   └── DevToolsPanel.vue    # New panel component
└── views/
    └── SessionView.vue      # Add DevToolsPanel after wa-tab-group
```

## State Management

### LocalStorage (following sidebar pattern from ProjectView.vue)

**Key:** `twicc-devtools-panel-state`

**Structure:**
```javascript
{
    open: boolean,   // Panel expanded or collapsed (default: false)
    height: number   // Height in pixels (default: 250)
}
```

### Functions (mirror sidebar pattern)

```javascript
// Constants
const DEVTOOLS_PANEL_STORAGE_KEY = 'twicc-devtools-panel-state'
const DEFAULT_DEVTOOLS_PANEL_HEIGHT = 250
const DEVTOOLS_PANEL_COLLAPSE_THRESHOLD = 50
const MOBILE_BREAKPOINT = 640

// Load panel state from localStorage
function loadDevToolsPanelState() {
    try {
        const stored = localStorage.getItem(DEVTOOLS_PANEL_STORAGE_KEY)
        if (stored) {
            const parsed = JSON.parse(stored)
            return {
                open: typeof parsed.open === 'boolean' ? parsed.open : false,
                height: typeof parsed.height === 'number' && parsed.height > 0 ? parsed.height : DEFAULT_DEVTOOLS_PANEL_HEIGHT,
            }
        }
    } catch (e) {
        console.warn('Failed to load devtools panel state from localStorage:', e)
    }
    return { open: false, height: DEFAULT_DEVTOOLS_PANEL_HEIGHT }
}

// Save panel state to localStorage
function saveDevToolsPanelState(state) {
    try {
        localStorage.setItem(DEVTOOLS_PANEL_STORAGE_KEY, JSON.stringify(state))
    } catch (e) {
        console.warn('Failed to save devtools panel state to localStorage:', e)
    }
}
```

## DevToolsPanel.vue Implementation

### Toggle Mechanism (CSS-only, like sidebar)

Use a hidden checkbox outside the panel for CSS-only toggle, following the same pattern as the sidebar:

```html
<!-- In SessionView.vue, before the panel -->
<input type="checkbox" id="devtools-panel-toggle-state" class="devtools-panel-toggle-checkbox" :checked="initialDevToolsPanelChecked" @change="handleDevToolsPanelToggle"/>

<!-- DevToolsPanel -->
<DevToolsPanel />
```

The checkbox state controls the panel visibility via CSS:
- **Desktop:** unchecked = collapsed, checked = expanded (inverted from sidebar logic since we want it closed by default)
- **Mobile:** checked = open (overlay visible)

### Template Structure

```vue
<template>
    <div class="devtools-panel">
        <!-- Resize handle (top edge, desktop only) - part of wa-split-panel -->

        <!-- Panel content area -->
        <div class="devtools-panel-content">
            <!-- Placeholder content for now -->
            <div class="devtools-panel-placeholder">
                <span>Dev Tools Panel</span>
            </div>
        </div>

        <!-- Footer with toggle button -->
        <div class="devtools-panel-footer">
            <label for="devtools-panel-toggle-state" class="devtools-panel-toggle" id="devtools-panel-toggle-label">
                <span class="devtools-panel-backdrop"></span>
                <wa-button id="devtools-panel-toggle-button" variant="neutral" appearance="filled-outlined" size="small">
                    <wa-icon class="icon-collapse" name="angles-down"></wa-icon>
                    <wa-icon class="icon-expand" name="angles-up"></wa-icon>
                </wa-button>
            </label>
        </div>
    </div>
</template>
```

### Resize Handling (using wa-split-panel like sidebar)

The panel should be integrated with a `wa-split-panel` in SessionView.vue, similar to how the sidebar uses it. The split panel provides:
- Resize by dragging the divider
- Snap points
- Touch-friendly handle

```vue
<!-- In SessionView.vue -->
<wa-split-panel
    class="session-content-split"
    :position-in-pixels="devToolsPanelState.height"
    primary="end"
    vertical
    snap="50px 150px 250px 400px"
    @wa-reposition="handleDevToolsPanelReposition"
>
    <wa-icon slot="divider" name="grip-lines" class="divider-handle"></wa-icon>

    <!-- Main content (messages, tabs) -->
    <div slot="start" class="session-main-content">
        <wa-tab-group ...>
            <!-- existing tabs -->
        </wa-tab-group>
    </div>

    <!-- DevTools Panel -->
    <aside slot="end" class="devtools-panel">
        <DevToolsPanel />
    </aside>
</wa-split-panel>
```

### Resize Handler (like sidebar)

```javascript
// Guard flag to ignore reposition events triggered by height restore after auto-collapse.
// When we auto-collapse and restore the stored height, wa-split-panel fires a new
// wa-reposition event. Without this guard, we'd process that event as a normal resize.
let ignoringReposition = false

function handleDevToolsPanelReposition(event) {
    if (ignoringReposition) return

    const checkbox = document.getElementById('devtools-panel-toggle-state')
    if (!checkbox) return

    const newHeight = event.target.positionInPixels

    if (newHeight <= DEVTOOLS_PANEL_COLLAPSE_THRESHOLD) {
        // Auto-collapse: mark as closed, reset height to stored value
        checkbox.checked = false
        saveDevToolsPanelState({ open: false, height: devToolsPanelState.height })
        // Restore height, ignoring the resulting reposition event
        ignoringReposition = true
        requestAnimationFrame(() => {
            event.target.positionInPixels = devToolsPanelState.height
            requestAnimationFrame(() => {
                ignoringReposition = false
            })
        })
    } else {
        // Normal resize: update stored height
        devToolsPanelState.height = newHeight
        saveDevToolsPanelState({ open: true, height: newHeight })
    }
}
```

### Toggle Handler

```javascript
function handleDevToolsPanelToggle(event) {
    const isOpen = event.target.checked
    saveDevToolsPanelState({ open: isOpen, height: devToolsPanelState.height })
}
```

### Styles (following sidebar patterns)

```css
/* Hidden checkbox for CSS-only toggle */
.devtools-panel-toggle-checkbox {
    position: absolute;
    opacity: 0;
    pointer-events: none;
}

/* Panel container */
.devtools-panel {
    --transition-duration: .3s;
    background: var(--wa-color-surface-default);
    display: flex;
    flex-direction: column;
    position: relative;
    container-type: block-size;
    container-name: devtools-panel;
}

/* Panel content */
.devtools-panel-content {
    flex: 1;
    min-height: 0;
    overflow: hidden;
    padding: 0;
    display: flex;
    flex-direction: column;
}

/* Placeholder content */
.devtools-panel-placeholder {
    display: flex;
    align-items: center;
    justify-content: center;
    height: 100%;
    color: var(--wa-color-text-quiet);
    font-size: var(--wa-font-size-s);
}

/* Footer */
.devtools-panel-footer {
    flex-shrink: 0;
    display: flex;
    align-items: center;
    justify-content: flex-end;
    padding: var(--wa-space-s);
    position: relative;
    background: var(--main-header-footer-bg-color);
}

/* Toggle label (like sidebar-toggle) */
.devtools-panel-toggle {
    cursor: pointer;

    wa-button {
        pointer-events: none;
    }

    /* Default state: panel is collapsed, show expand icon */
    .icon-collapse {
        display: none;
    }
    .icon-expand {
        display: inline;
    }
}

/* Container query: when panel is expanded (> 50px), show collapse icon */
@container devtools-panel (height > 50px) {
    .devtools-panel-toggle .icon-collapse {
        display: inline;
    }
    .devtools-panel-toggle .icon-expand {
        display: none;
    }
}

/* Desktop: checkbox checked = panel expanded */
.session-view:has(.devtools-panel-toggle-checkbox:checked) .session-content-split {
    /* Show the panel with stored height */
}

.session-view:has(.devtools-panel-toggle-checkbox:not(:checked)) .session-content-split {
    /* Collapse the panel - hide end slot */
    grid-template-rows: auto 0 !important;
    &::part(divider) {
        opacity: 0;
        pointer-events: none;
    }
}

/* Mobile behavior - panel as overlay (like sidebar) */
@media (width < 640px) {
    /* Panel becomes a fixed drawer from the bottom */
    .devtools-panel {
        --panel-height: min(90vh, 90dvh);
        position: fixed;
        left: 0;
        bottom: 0;
        right: 0;
        height: var(--panel-height);
        z-index: 100;
        transform: translateY(100%);
        transition: transform var(--transition-duration) ease;
        box-shadow: var(--wa-shadow-xl);
        border-top: solid var(--wa-color-neutral-border-normal) 0.25rem;
    }

    /* Toggle button sticks out from the panel when closed */
    .devtools-panel-toggle {
        position: fixed;
        bottom: var(--wa-space-s);
        right: var(--wa-space-s);
        z-index: 10;
        transform: translateY(0);
        transition: transform var(--transition-duration) ease;
    }

    /* Backdrop for closing */
    .devtools-panel-backdrop {
        display: block;
        position: fixed;
        inset: 0;
        pointer-events: none;
        background: transparent;
        transition: background var(--transition-duration) ease;
    }

    /* When panel is open */
    .session-view:has(.devtools-panel-toggle-checkbox:checked) {
        .devtools-panel {
            transform: translateY(0);
        }

        .devtools-panel-toggle {
            position: absolute;
            bottom: var(--wa-space-s);
            right: var(--wa-space-s);
        }

        .devtools-panel-backdrop {
            pointer-events: all;
            background: rgba(0, 0, 0, 0.5);
        }
    }
}
```

## Integration in SessionView.vue

### Changes Required

1. Add hidden checkbox for toggle state
2. Wrap content with wa-split-panel (vertical)
3. Add DevToolsPanel component
4. Add localStorage functions and handlers

### Modified Structure

```vue
<template>
    <div class="session-view">
        <!-- Hidden checkbox for pure CSS panel toggle -->
        <input type="checkbox" id="devtools-panel-toggle-state" class="devtools-panel-toggle-checkbox" :checked="initialDevToolsPanelChecked" @change="handleDevToolsPanelToggle"/>

        <!-- Main session header (always visible, above tabs) -->
        <SessionHeader v-if="session" ... />

        <!-- Split panel: main content + devtools -->
        <wa-split-panel
            v-if="session"
            class="session-content-split"
            :position-in-pixels="DEFAULT_DEVTOOLS_PANEL_HEIGHT"
            primary="end"
            vertical
            snap="50px 150px 250px 400px"
            @wa-reposition="handleDevToolsPanelReposition"
        >
            <wa-icon slot="divider" name="grip-lines" class="divider-handle"></wa-icon>

            <!-- Main content slot -->
            <div slot="start" class="session-main-content">
                <wa-tab-group ... >
                    <!-- existing Session/Subagent tabs -->
                </wa-tab-group>
            </div>

            <!-- DevTools Panel slot -->
            <aside slot="end" class="devtools-panel">
                <div class="devtools-panel-content">
                    <!-- Placeholder for now -->
                    <div class="devtools-panel-placeholder">
                        Dev Tools Panel (coming soon)
                    </div>
                </div>

                <div class="devtools-panel-footer">
                    <label for="devtools-panel-toggle-state" class="devtools-panel-toggle">
                        <span class="devtools-panel-backdrop"></span>
                        <wa-button variant="neutral" appearance="filled-outlined" size="small">
                            <wa-icon class="icon-collapse" name="angles-down"></wa-icon>
                            <wa-icon class="icon-expand" name="angles-up"></wa-icon>
                        </wa-button>
                    </label>
                </div>
            </aside>
        </wa-split-panel>

        <!-- No session state -->
        <div v-else class="empty-state">...</div>
    </div>
</template>
```

## Behavior Details

### Desktop

1. **Collapsed state**: Panel is completely hidden (height 0), only toggle button visible in corner
2. **Expanded state**: Panel visible with stored height, resize handle at top edge
3. **Resize**: Drag the top edge to resize (using wa-split-panel snap points: 50px, 150px, 250px, 400px)
4. **Auto-collapse**: Dragging height below 50px triggers collapse (like sidebar)
5. **Toggle button**: Click to expand/collapse
6. **Icon switch**: Uses container query to show appropriate icon (like sidebar uses `@container sidebar (width <= 50px)`)

### Mobile

1. **Closed state**: Toggle button visible in bottom-right corner
2. **Open state**: Overlay covering 90% of viewport height from bottom
3. **Backdrop**: Top 10% shows through with semi-transparent backdrop, clicking it closes the panel
4. **Animation**: Panel slides up from bottom with 0.3s ease transition
5. **No resize**: Fixed height at 90vh/90dvh

### Persistence

State saved to localStorage under key `twicc-devtools-panel-state`:
- `open`: Whether panel is expanded
- `height`: Panel height in pixels (desktop)

State persists across page reloads. Default state is closed with 250px height.

## Implementation Checklist

- [ ] Add localStorage functions in SessionView.vue (loadDevToolsPanelState, saveDevToolsPanelState)
- [ ] Add constants (DEVTOOLS_PANEL_STORAGE_KEY, DEFAULT_DEVTOOLS_PANEL_HEIGHT, etc.)
- [ ] Add hidden checkbox for toggle state
- [ ] Add initialDevToolsPanelChecked computed property
- [ ] Wrap tab-group with wa-split-panel (vertical, primary="end")
- [ ] Add devtools-panel aside with placeholder content
- [ ] Add toggle button (label + wa-button + icons)
- [ ] Add handleDevToolsPanelReposition function
- [ ] Add handleDevToolsPanelToggle function
- [ ] Add all CSS (panel styles, toggle styles, mobile styles)
- [ ] Test desktop: expand/collapse via button
- [ ] Test desktop: resize via drag
- [ ] Test desktop: auto-collapse when dragging below threshold
- [ ] Test mobile: overlay open/close
- [ ] Test mobile: backdrop click to close
- [ ] Test persistence: refresh page, state should restore

## Future Work (Separate Specs)

The panel content will be defined in separate specs.

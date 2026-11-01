# Terminal Mobile Touch Support

## Problem

xterm.js does not natively support touch-based scrolling or text selection on mobile devices ([xtermjs/xterm.js#5377](https://github.com/xtermjs/xterm.js/issues/5377)). Touch events are consumed but produce no useful behavior.

## Design

Two touch modes managed in `useTerminal.js`, with a header toolbar in `TerminalPanel.vue` visible only on touch devices.

### Touch modes (useTerminal.js)

**Scroll mode (default):** Touch drag scrolls the terminal buffer via `terminal.scrollLines()`. Delta Y is computed from `touchstart`/`touchmove` positions, divided by ~20px per line.

**Select mode:** Touch drag selects text. Screen coordinates are converted to terminal cell (col, row) using `.xterm-screen` bounding rect divided by `terminal.cols`/`terminal.rows`. Selection is applied via `terminal.select(col, row, length)`, supporting both forward and backward selection.

**Event listeners:**
- `touchstart`/`touchend`: `{ passive: true }`
- `touchmove`: `{ passive: false }` to allow `preventDefault()` blocking native browser scroll

**Clipboard:** `execCommand('copy')` via hidden textarea as primary method (more reliable on mobile), `navigator.clipboard.writeText()` as fallback.

**New exports from useTerminal:** `touchMode` (ref), `selectionLength` (ref), `copySelection()`, `clearSelection()`.

### Header toolbar (TerminalPanel.vue)

Visible only when `settingsStore.isTouchDevice` is true. Contains:
- Toggle buttons for scroll/select mode
- Character count + Copy button when text is selected in select mode

### Files modified

- `frontend/src/composables/useTerminal.js` — touch logic + new exports
- `frontend/src/components/TerminalPanel.vue` — header toolbar with toggle + copy

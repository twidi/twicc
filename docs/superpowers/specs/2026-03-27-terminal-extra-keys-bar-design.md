# Terminal Extra Keys Bar

**Date:** 2026-03-27
**Status:** Approved

## Summary

Add an "extra keys bar" below the terminal emulator, inspired by mobile terminal apps (Termux, Blink Shell, Termius). The bar provides quick access to keys that are difficult or impossible to type on mobile virtual keyboards: modifiers (Ctrl, Alt, Shift), escape, tab, arrow keys, navigation keys, special characters, and function keys.

## Motivation

On mobile devices, virtual keyboards lack essential terminal keys (Escape, Ctrl, Alt, arrow keys, function keys, pipe, tilde, etc.). This makes the terminal effectively unusable for anything beyond simple commands. Every major mobile terminal app solves this with an "extra keys bar" — there is no existing JavaScript library for this, so we build a custom component.

## Design Decisions

### Layout: Tabbed hybrid

A single row of keys organized into 3 tabs, balancing compactness (minimal vertical space) with completeness (all keys accessible).

**Tabs and their contents:**

| Tab | Keys | Purpose |
|-----|------|---------|
| **Essentials** (default) | ESC, TAB, CTRL, ALT, ←, ↑, ↓, →, -, /, \|, ~ | Daily use: modifiers, arrows, common shell characters |
| **More** | SHIFT, HOME, END, PGUP, PGDN, DEL, INS, \\, _, *, &, ., PASTE | Advanced navigation, less frequent characters, clipboard |
| **F-keys** | F1–F12 | TUI apps (htop, mc, vim, tig…) |

### Modifier behavior: One-shot + Lock

- **Single tap** → one-shot: modifier activates for the next keypress only, then auto-deactivates
- **Double-tap** → lock: modifier stays active until tapped again. Double-tap is detected when two consecutive `pointerdown` events on the same modifier occur within 350ms.
- Applies to CTRL, ALT, and SHIFT
- Visual feedback: one-shot shows accent color + glow; locked shows stronger accent + inset border

### No Web Awesome components

The entire bar (buttons, tabs, everything) is pure custom HTML/CSS. No `wa-button`, no `wa-tab-group`. This gives full control over compact sizing and terminal-matching aesthetics.

### Visibility

Always visible for now (no toggle). Future enhancement: conditionally show only on mobile using the existing `settingsStore.isTouchDevice`.

## Architecture

### Component: `ExtraKeysBar.vue`

A single SFC component using Composition API. Placed in `TerminalPanel.vue` below the `.terminal-container`.

**Props:**
- `activeModifiers` — reactive object `{ ctrl: boolean, alt: boolean, shift: boolean }` for visual rendering
- `lockedModifiers` — reactive object `{ ctrl: boolean, alt: boolean, shift: boolean }` for locked state visual
- `theme` — `'dark'` or `'light'` for matching the terminal theme

**Emits:**
- `key-input(key: string)` — emitted for regular key presses (key identifier like `'Escape'`, `'Tab'`, `'ArrowUp'`, `'Home'`, `'F5'`, `'/'`, etc.)
- `modifier-toggle(modifier: string, locked: boolean)` — emitted for modifier state changes
- `paste` — emitted for the paste action

**Internal state:**
- `activeTab` — which tab is currently shown (`'essentials'` | `'more'` | `'fkeys'`)

**Responsibilities:**
- Render tabs and key grid
- Track active tab
- Detect single-tap vs double-tap on modifiers (< 350ms between two `pointerdown`)
- Emit appropriate events
- No ANSI sequence knowledge — that stays in `useTerminal`

### Focus management (critical)

Clicking buttons in the extra keys bar must **not steal focus from the terminal**. If focus moves to a bar button, the virtual keyboard may dismiss on mobile and physical keyboard input will stop reaching xterm.js.

All interactive elements in the bar must use `@pointerdown.prevent` to prevent the default focus behavior. This keeps focus on the xterm.js terminal at all times. The PASTE button is an exception — see the Clipboard section below.

### Changes to `useTerminal.js`

**New state:**
```javascript
const activeModifiers = reactive({ ctrl: false, alt: false, shift: false })
const lockedModifiers = reactive({ ctrl: false, alt: false, shift: false })
```

**New function: `handleExtraKeyInput(key)`**
- Constructs a synthetic key descriptor `{ key, ctrlKey, altKey, shiftKey }` from the key identifier + current modifier state
- Converts to ANSI sequence using the existing `imeKeyToAnsiSequence` logic (duck-typed call — the function accesses `.key`, `.ctrlKey`, `.altKey`, `.shiftKey` which are present on the synthetic object). Pass `{ ignoreShift: false }` since the bar's shift state is intentional (unlike Android soft keyboard false positives).
- Sends the ANSI sequence to the PTY backend via `wsSend({ type: 'input', data: sequence })` — not `terminal.write()` which would only render locally
- For simple characters (`/`, `-`, `|`, `~`, `\`, `_`, `*`, `&`, `.`) without active modifiers: sends the character directly via `wsSend`
- For simple characters with active modifiers: Ctrl+letter is handled by `imeKeyToAnsiSequence`. For modifier+punctuation that `imeKeyToAnsiSequence` does not handle, Alt+char sends `\x1b` + char (standard Alt behavior), and Ctrl+punctuation that has no standard mapping sends the character as-is (modifier is consumed but has no effect).
- After sending, resets one-shot modifiers (`activeModifiers[mod] = false` for any modifier where `lockedModifiers[mod]` is false)
- Refocuses the terminal after input: `terminal.focus()`

**New function: `handleExtraKeyModifierToggle(modifier, locked)`**
- Updates `activeModifiers[modifier]` and `lockedModifiers[modifier]` state
- If `locked` is true: sets both `activeModifiers[modifier] = true` and `lockedModifiers[modifier] = true`
- If toggling off (modifier was active/locked): sets both to `false`
- If one-shot activation: sets `activeModifiers[modifier] = true`, `lockedModifiers[modifier] = false`

**New function: `handleExtraKeyPaste()`**
- Reads clipboard via `navigator.clipboard.readText()` — this must run inside the `click` event handler (not `pointerdown`) to preserve the user gesture chain required by the Clipboard API
- Sends the clipboard text to the PTY via `wsSend({ type: 'input', data: text })`
- On clipboard read failure (permission denied, unavailable): shows a toast error via the existing `toast` utility
- Refocuses the terminal after paste: `terminal.focus()`

**Exposed from composable:**
- `activeModifiers`, `lockedModifiers` (for ExtraKeysBar props)
- `handleExtraKeyInput`, `handleExtraKeyModifierToggle`, `handleExtraKeyPaste` (for ExtraKeysBar event handlers)
- `getEffectiveTheme` is already available from settings store

### Integration in `TerminalPanel.vue`

```vue
<template>
  <div class="terminal-panel">
    <div ref="containerRef" class="terminal-container"></div>
    <ExtraKeysBar
      :active-modifiers="activeModifiers"
      :locked-modifiers="lockedModifiers"
      :theme="settingsStore.getEffectiveTheme"
      @key-input="handleExtraKeyInput"
      @modifier-toggle="handleExtraKeyModifierToggle"
      @paste="handleExtraKeyPaste"
    />
    <!-- disconnect overlay unchanged -->
  </div>
</template>
```

The `.terminal-container` keeps `flex: 1; min-height: 0` — it shrinks to accommodate the bar. The bar has intrinsic height (no flex-grow). The existing `ResizeObserver` on `containerRef` will detect the container size change and call `fitAddon.fit()` automatically, so the terminal reflows its columns/rows.

### Modifier integration with existing IME handler

The existing `imeKeyToAnsiSequence` function in `useTerminal.js` handles mobile keyboard input. When a modifier is active (from the extra keys bar), the IME handler should consult `activeModifiers` to compose the correct ANSI sequence. After processing a key with a one-shot modifier, it resets `activeModifiers[mod] = false`.

This means modifiers from the extra keys bar work with both:
- Keys pressed in the extra keys bar itself
- Keys typed on the virtual/physical keyboard

### Clipboard handling for PASTE

The PASTE button requires special handling because `navigator.clipboard.readText()` needs a user gesture:
- Unlike other keys, the PASTE button uses `@click` (not `@pointerdown.prevent`) so the gesture chain is preserved for the Clipboard API
- After the paste operation completes, `terminal.focus()` is called to restore focus
- On failure (permission denied, insecure context), a toast error is shown: "Clipboard access denied"

## Visual Design

### Colors (dark theme)
- Bar background: `#141e28` (slightly darker than terminal `#1b2733`)
- Bar top border: `1px solid #2a3a4a`
- Key background: `#1e2d3d`, border `#2a3a4a`, text `#c0d0e0`
- Key hover: `#263a4d`, border `#3a4a5a`
- Key active (pressed): `#2a4050`, `scale(0.95)`
- Modifier accent color: `#c084fc` (purple), border `#3a2d5a`
- Modifier hover: `#2a2545`
- Modifier one-shot: background `#3a2560`, border `#7c5cbf`, text `#d8b4fe`, glow `box-shadow: 0 0 6px rgba(192, 132, 252, 0.3)`
- Modifier locked: background `#3a2560`, border `#9b7ad8`, text `#e0c4ff`, glow + inset `box-shadow: 0 0 6px rgba(192, 132, 252, 0.3), inset 0 0 0 1px rgba(192, 132, 252, 0.3)`
- Tab text: `#5a6a7a`, active tab text: `#c0d0e0`, active tab background: `rgba(255, 255, 255, 0.08)`

### Colors (light theme)
- Bar background: `#f0f2f4`
- Bar top border: `1px solid #d0d7de`
- Key background: `#ffffff`, border `#d0d7de`, text `#24292e`
- Key hover: `#f6f8fa`, border `#b0b8c0`
- Key active (pressed): `#eaeef2`, `scale(0.95)`
- Modifier accent color: `#8250df`, border `#c8b8e8`
- Modifier hover: `#f5f0ff`
- Modifier one-shot: background `#ede4ff`, border `#8250df`, text `#6e3dc7`, glow `box-shadow: 0 0 4px rgba(130, 80, 223, 0.2)`
- Modifier locked: background `#ede4ff`, border `#6e3dc7`, text `#5b21b6`, glow + inset `box-shadow: 0 0 4px rgba(130, 80, 223, 0.2), inset 0 0 0 1px rgba(130, 80, 223, 0.2)`
- Tab text: `#8b949e`, active tab text: `#24292e`, active tab background: `rgba(0, 0, 0, 0.06)`

### Key sizing
- `min-width: 34px`, `padding: 7px 0`
- Wider keys (HOME, END, PGUP, PGDN, PASTE): `min-width: 42px`
- Arrow keys: `min-width: 30px`, slightly larger font
- `gap: 4px`, `flex-wrap: wrap`
- `touch-action: manipulation` (prevent zoom on double-tap)
- `user-select: none`

### Tabs
- Font: 10px uppercase, `letter-spacing: 0.5px`
- Active: text brightens + subtle background

## Files to create/modify

| File | Action |
|------|--------|
| `frontend/src/components/ExtraKeysBar.vue` | **Create** — new component |
| `frontend/src/composables/useTerminal.js` | **Modify** — add modifier state, input/modifier-toggle/paste handlers, expose new refs/functions |
| `frontend/src/components/TerminalPanel.vue` | **Modify** — import and integrate ExtraKeysBar |

## Future enhancements (not in scope)

- Toggle visibility based on `isTouchDevice`
- User-configurable key layout
- Swipe gestures on modifier keys
- Haptic feedback on mobile

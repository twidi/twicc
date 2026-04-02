// frontend/src/composables/useTerminal.js

import { ref, reactive, watch, onMounted, onUnmounted } from 'vue'
import { Terminal } from '@xterm/xterm'
import { FitAddon } from '@xterm/addon-fit'
import { WebLinksAddon } from '@xterm/addon-web-links'
import { ClipboardAddon } from '@xterm/addon-clipboard'
import { useSettingsStore } from '../stores/settings'
import { useDataStore } from '../stores/data'
import { toast } from '../composables/useToast'
import { resolveSnippetText } from '../utils/snippetPlaceholders'
import '@xterm/xterm/css/xterm.css'

// ── Terminal themes ──────────────────────────────────────────────────────
// Background colors match the code editor and --wa-color-surface-default.

const THEMES = {
    dark: {
        background: '#1b2733',
        foreground: '#e0e0e0',
        cursor: '#e0e0e0',
        cursorAccent: '#1b2733',
        selectionBackground: 'rgba(255, 255, 255, 0.15)',
        selectionForeground: '#ffffff',
        // ANSI colors (normal)
        black: '#1b2733',
        red: '#f87171',
        green: '#4ade80',
        yellow: '#facc15',
        blue: '#60a5fa',
        magenta: '#c084fc',
        cyan: '#22d3ee',
        white: '#e0e0e0',
        // ANSI colors (bright)
        brightBlack: '#4b5563',
        brightRed: '#fca5a5',
        brightGreen: '#86efac',
        brightYellow: '#fde68a',
        brightBlue: '#93c5fd',
        brightMagenta: '#d8b4fe',
        brightCyan: '#67e8f9',
        brightWhite: '#ffffff',
    },
    light: {
        background: '#ffffff',
        foreground: '#24292e',
        cursor: '#24292e',
        cursorAccent: '#ffffff',
        selectionBackground: 'rgba(0, 0, 0, 0.1)',
        selectionForeground: '#24292e',
        // ANSI colors (normal)
        black: '#24292e',
        red: '#d73a49',
        green: '#22863a',
        yellow: '#b08800',
        blue: '#0366d6',
        magenta: '#6f42c1',
        cyan: '#1b7c83',
        white: '#e1e4e8',
        // ANSI colors (bright)
        brightBlack: '#6a737d',
        brightRed: '#cb2431',
        brightGreen: '#28a745',
        brightYellow: '#dbab09',
        brightBlue: '#2188ff',
        brightMagenta: '#8a63d2',
        brightCyan: '#3192aa',
        brightWhite: '#fafbfc',
    },
}

// ── Mobile special-key handling ─────────────────────────────────────────
// On mobile (Android/iOS), xterm.js unreliably translates special keys
// (arrows, Home, Tab, Ctrl+letter, etc.) to ANSI escape sequences.
// This can be caused by the IME reporting keyCode=229, composition state
// interference, or input event side-effects — the root cause varies.
//
// These helpers use event.key (which is always correctly set regardless of
// IME state) to build the right ANSI sequences ourselves. On touch devices,
// they replace xterm.js's own key handling for all recognized special keys.
// Regular character input (letters, digits, punctuation) is unaffected.

/**
 * Compute the xterm modifier parameter from a keyboard event.
 * When ignoreShift is true, the Shift modifier is excluded — needed on mobile
 * where Android keyboards often falsely report shiftKey=true on arrow keys
 * and other special keys.
 */
function _imeModifierParam(event, ignoreShift = false) {
    let bits = 0
    if (!ignoreShift && event.shiftKey) bits |= 1
    if (event.altKey) bits |= 2
    if (event.ctrlKey) bits |= 4
    return bits > 0 ? bits + 1 : 0
}

// CSI cursor keys: \x1b[{letter} — with modifier: \x1b[1;{mod}{letter}
const _CSI_CURSOR_KEYS = {
    ArrowUp: 'A', ArrowDown: 'B', ArrowRight: 'C', ArrowLeft: 'D',
    Home: 'H', End: 'F',
}

// CSI tilde keys: \x1b[{num}~ — with modifier: \x1b[{num};{mod}~
const _CSI_TILDE_KEYS = {
    Insert: 2, Delete: 3, PageUp: 5, PageDown: 6,
    F5: 15, F6: 17, F7: 18, F8: 19, F9: 20, F10: 21, F11: 23, F12: 24,
}

// SS3 keys (F1-F4): \x1bO{letter} — with modifier: \x1b[1;{mod}{letter}
const _SS3_KEYS = { F1: 'P', F2: 'Q', F3: 'R', F4: 'S' }

/**
 * Map a keyboard event (with keyCode=229 from mobile IME) to the correct
 * ANSI escape sequence. Returns null if the key is not a recognized special
 * key, letting xterm.js handle it through its normal composition path.
 *
 * @param {KeyboardEvent} event
 * @param {Object} [options]
 * @param {boolean} [options.ignoreShift=false] - Ignore shiftKey in modifier
 *   calculation. On Android, soft keyboards often falsely report shiftKey=true
 *   for arrow keys and other special keys.
 * @param {boolean} [options.applicationCursorMode=false] - When true, send
 *   SS3 sequences (\x1bO{letter}) for unmodified cursor keys instead of CSI.
 *   Programs like less, vim, and tmux enable DECCKM (application cursor mode).
 * @returns {string|null}
 */
function imeKeyToAnsiSequence(event, { ignoreShift = false, applicationCursorMode = false } = {}) {
    // For CSI/SS3 sequences, use the modifier param (with optional shift ignore).
    // Shift is checked directly for Tab and Backspace below where it matters.
    const mod = _imeModifierParam(event, ignoreShift)

    // CSI cursor keys — in application cursor mode (DECCKM), unmodified
    // cursor keys use SS3 (\x1bO{letter}) instead of CSI (\x1b[{letter}).
    const cursorLetter = _CSI_CURSOR_KEYS[event.key]
    if (cursorLetter) {
        if (mod) return `\x1b[1;${mod}${cursorLetter}`
        return applicationCursorMode ? `\x1bO${cursorLetter}` : `\x1b[${cursorLetter}`
    }

    // CSI tilde keys
    const tildeNum = _CSI_TILDE_KEYS[event.key]
    if (tildeNum !== undefined) {
        return mod ? `\x1b[${tildeNum};${mod}~` : `\x1b[${tildeNum}~`
    }

    // SS3 keys (F1-F4)
    const ss3Letter = _SS3_KEYS[event.key]
    if (ss3Letter) {
        return mod ? `\x1b[1;${mod}${ss3Letter}` : `\x1bO${ss3Letter}`
    }

    // Simple keys
    switch (event.key) {
        case 'Tab':
            return event.shiftKey ? '\x1b[Z' : '\x09'
        case 'Escape':
            return '\x1b'
        case 'Enter':
            return '\x0d'
        case 'Backspace':
            return event.ctrlKey ? '\x08' : '\x7f'
    }

    // Ctrl+letter (a-z): send control character (0x01–0x1a)
    // Shift doesn't affect control characters (Ctrl+Shift+R = Ctrl+R = 0x12)
    if (event.ctrlKey && !event.altKey && event.key.length === 1) {
        const code = event.key.toUpperCase().charCodeAt(0)
        if (code >= 0x41 && code <= 0x5a) {
            return String.fromCharCode(code - 0x40)
        }
    }

    // Alt+letter: send ESC prefix followed by the character
    // (e.g. Alt+B = \x1bb = word back in bash, Alt+F = \x1bf = word forward)
    if (event.altKey && !event.ctrlKey && event.key.length === 1) {
        return `\x1b${event.key}`
    }

    return null
}

/**
 * Composable for managing an xterm.js terminal with a dedicated WebSocket
 * connection to the backend PTY.
 *
 * Each instance manages its own Terminal + WebSocket pair. With KeepAlive,
 * each session has its own TerminalPanel instance, so no singleton map is needed.
 *
 * The terminal is lazily initialized: nothing happens until `start()` is called
 * (typically when the Terminal tab becomes active for the first time).
 *
 * @param {string} sessionId - The session ID (non-reactive, captured once)
 * @returns {{ containerRef: import('vue').Ref, isConnected: import('vue').Ref<boolean>, started: import('vue').Ref<boolean>, start: () => void, reconnect: () => void }}
 */
export function useTerminal(sessionId) {
    const settingsStore = useSettingsStore()
    const dataStore = useDataStore()
    const containerRef = ref(null)
    const isConnected = ref(false)
    const started = ref(false)

    // ── Touch mode (mobile) ────────────────────────────────────────────
    // 'scroll' = normal scroll (default), 'select' = touch-drag selects text
    const touchMode = ref('scroll')
    const hasSelection = ref(false)

    /** Whether the active tmux pane is in alternate screen (less, vim, etc.) */
    const paneAlternate = ref(false)

    // ── Scroll position tracking ────────────────────────────────────────
    /** Last known tmux scroll position (for computing actual scroll delta). */
    let lastTmuxScrollPosition = null
    /** Whether we can scroll up (not at top). */
    const canScrollUp = ref(false)
    /** Whether we can scroll down (not at bottom). */
    const canScrollDown = ref(false)

    /** @type {Terminal | null} */
    let terminal = null
    /** @type {FitAddon | null} */
    let fitAddon = null
    /** @type {WebSocket | null} */
    let ws = null
    /** @type {ResizeObserver | null} */
    let resizeObserver = null
    /** @type {boolean} */
    let intentionalClose = false

    // ── Extra keys bar state ────────────────────────────────────────────
    const activeModifiers = reactive({ ctrl: false, alt: false, shift: false })
    const lockedModifiers = reactive({ ctrl: false, alt: false, shift: false })

    // ── Selection state ────────────────────────────────────────────────
    let selectStartCol = 0
    let selectStartRow = 0
    // Auto-scroll state for selection mode (edge dragging)
    let autoScrollId = null
    let autoScrollLastClientX = 0
    let autoScrollLastClientY = 0
    // Tmux scroll-selection: indexed buffer for selections that extend
    // beyond the visible viewport via auto-scroll in tmux-normal mode.
    // The buffer uses relative indices where 0 = initial viewport top.
    // Scrolling up adds negative indices, scrolling down adds positive
    // indices beyond the initial viewport. The fixed anchor stays at its
    // original index, the moving anchor follows the finger/edge.
    let tmuxScrollSel = null
    //   { lines: { [index]: string },  — indexed line buffer
    //     bufferMin: number,           — lowest index in buffer
    //     bufferMax: number,           — highest index in buffer
    //     viewportStart: number,       — index of current viewport row 0
    //     fixedAnchorIdx: number,      — fixed anchor (where drag started)
    //     fixedAnchorCol: number,
    //     movingAnchorIdx: number,     — moving anchor (finger/edge)
    //     movingAnchorCol: number }
    let tmuxAutoScrollActive = false
    // Scroll physics state
    let scrollLastY = 0
    let scrollLastTime = 0
    let scrollVelocity = 0
    let scrollAccumulator = 0
    let scrollInertiaId = null
    /** @type {AbortController | null} */
    let touchAbortController = null
    // Desktop state (tmux only: drag selection + wheel scroll)
    let desktopDragActive = false
    let desktopWheelAccumulator = 0
    /** @type {AbortController | null} */
    let desktopDragAbortController = null

    /**
     * Check whether tmux should actually be used for this session.
     * Tmux is skipped for draft and archived sessions.
     */
    function shouldUseTmux() {
        if (!settingsStore.isTerminalUseTmux) return false
        const session = dataStore.getSession(sessionId)
        if (session?.draft || session?.archived) return false
        return true
    }

    /**
     * Build the WebSocket URL for the terminal endpoint.
     * Includes the project ID in the path so the backend can resolve the
     * working directory even for draft sessions (which don't exist in the DB).
     * Sends ?tmux=1 only when tmux is applicable for the current session.
     */
    function getWsUrl() {
        const wsProtocol = location.protocol === 'https:' ? 'wss:' : 'ws:'
        const session = dataStore.getSession(sessionId)
        const projectId = session?.project_id || '_'
        const base = `${wsProtocol}//${location.host}/ws/terminal/${projectId}/${sessionId}/`
        return shouldUseTmux() ? `${base}?tmux=1` : base
    }

    /**
     * Connect (or reconnect) the WebSocket to the backend PTY.
     */
    function connectWs() {
        if (ws && (ws.readyState === WebSocket.CONNECTING || ws.readyState === WebSocket.OPEN)) {
            return // already connected or connecting
        }

        ws = new WebSocket(getWsUrl())

        ws.onopen = () => {
            isConnected.value = true
            if (shouldUseTmux()) {
                terminal?.writeln('\x1b[32mConnected (tmux).\x1b[0m \x1b[2m(Session persists across disconnections)\x1b[0m')
            } else {
                terminal?.writeln('\x1b[32mConnected.\x1b[0m \x1b[2m(Ctrl+D or type "exit" to disconnect)\x1b[0m')
                if (settingsStore.isTerminalUseTmux) {
                    const session = dataStore.getSession(sessionId)
                    const reason = session?.draft ? 'draft' : 'archived'
                    terminal?.writeln(`\x1b[2m(tmux disabled for ${reason} sessions)\x1b[0m`)
                }
            }
            // Send current terminal dimensions so the PTY matches xterm.js size.
            // The initial fitAddon.fit() runs before the WebSocket is open,
            // so the backend spawns with default 80x24. This fixes it.
            if (terminal) {
                wsSend({ type: 'resize', cols: terminal.cols, rows: terminal.rows })
            }
        }

        ws.onmessage = (event) => {
            const data = event.data
            // Detect JSON control messages from the server
            if (data.charAt(0) === '{') {
                try {
                    const msg = JSON.parse(data)
                    if (msg.type === 'pane_state') {
                        paneAlternate.value = msg.alternate_on
                        // Update scroll position from tmux (non-alternate only)
                        if (!msg.alternate_on) {
                            if (msg.in_copy_mode) {
                                canScrollUp.value = msg.scroll_position < msg.history_size
                                canScrollDown.value = msg.scroll_position > 0
                            } else {
                                // Not in copy-mode → at bottom, can scroll up if there's history
                                canScrollUp.value = msg.history_size > 0
                                canScrollDown.value = false
                            }
                        }
                        return
                    }
                    if (msg.type === 'scroll_result') {
                        const pos = msg.scroll_position
                        const size = msg.history_size
                        const atTop = pos >= size
                        const atBottom = pos === 0
                        const distanceToTop = size - pos
                        const distanceToBottom = pos
                        // Compute actual scroll delta from last known position
                        let actual = msg.requested
                        if (lastTmuxScrollPosition !== null) {
                            // scroll_position counts from bottom: up increases, down decreases
                            actual = -(pos - lastTmuxScrollPosition)
                        }
                        lastTmuxScrollPosition = pos
                        canScrollUp.value = !atTop
                        canScrollDown.value = !atBottom

                        if (onScrollResult) {
                            const cb = onScrollResult
                            onScrollResult = null
                            cb({ requested: msg.requested, actual, atTop, atBottom })
                        }
                        return
                    }
                } catch {
                    // Not valid JSON — fall through to terminal.write
                }
            }
            // Raw PTY output
            terminal?.write(data)
        }

        ws.onclose = (event) => {
            isConnected.value = false

            if (intentionalClose) return

            // Auth failure — don't reconnect
            if (event.code === 4001) {
                terminal?.writeln('\x1b[31mAuthentication failed.\x1b[0m')
                return
            }

            terminal?.writeln('\x1b[31mDisconnected.\x1b[0m')
        }

        ws.onerror = () => {
            // onclose will fire after onerror, so we handle state there
        }
    }

    /**
     * Send a JSON message to the WebSocket.
     */
    function wsSend(data) {
        if (ws && ws.readyState === WebSocket.OPEN) {
            ws.send(JSON.stringify(data))
        }
    }

    // ── Touch selection helpers (mobile) ──────────────────────────────────

    /**
     * Convert screen pixel coordinates to terminal cell (col, row).
     */
    function screenToTerminalCoords(clientX, clientY) {
        const screenEl = terminal.element?.querySelector('.xterm-screen')
        if (!screenEl) return { col: 0, row: 0 }
        const rect = screenEl.getBoundingClientRect()
        const cellWidth = rect.width / terminal.cols
        const cellHeight = rect.height / terminal.rows
        const col = Math.max(0, Math.min(terminal.cols - 1, Math.floor((clientX - rect.left) / cellWidth)))
        const viewportRow = Math.floor((clientY - rect.top) / cellHeight)
        const bufferRow = viewportRow + terminal.buffer.active.viewportY
        return { col, row: bufferRow }
    }

    /**
     * Copy text to clipboard with execCommand fallback for mobile reliability.
     */
    function clipboardWrite(text) {
        // Try execCommand first — synchronous, more reliable on mobile
        try {
            const textArea = document.createElement('textarea')
            textArea.value = text
            textArea.style.position = 'fixed'
            textArea.style.top = '0'
            textArea.style.left = '0'
            textArea.style.opacity = '0'
            document.body.appendChild(textArea)
            textArea.focus()
            textArea.select()
            const success = document.execCommand('copy')
            document.body.removeChild(textArea)
            if (success) return
        } catch {
            // Fall through to async API
        }
        // Fallback to async clipboard API
        navigator.clipboard.writeText(text).catch(() => {})
    }

    /** Whether the current touch gesture is a selection (vs scrollbar drag). */
    let touchIsSelecting = false

    /**
     * Update the terminal selection from the start anchor to the current
     * screen coordinates (clientX, clientY). Called both from onTouchMove
     * and from the auto-scroll animation loop.
     */
    function updateSelection(clientX, clientY) {
        if (!terminal) return
        const coords = screenToTerminalCoords(clientX, clientY)
        const startOffset = selectStartRow * terminal.cols + selectStartCol
        const currentOffset = coords.row * terminal.cols + coords.col
        const length = currentOffset - startOffset

        if (length > 0) {
            terminal.select(selectStartCol, selectStartRow, length)
        } else if (length < 0) {
            terminal.select(coords.col, coords.row, -length)
        }
    }

    /**
     * Read text lines from the visible terminal screen.
     * @param {number} fromRow - Visual row (0 = top of viewport)
     * @param {number} toRow - Visual row (inclusive)
     * @returns {string[]}
     */
    function readVisibleLines(fromRow, toRow) {
        if (!terminal) return []
        const buf = terminal.buffer.active
        const base = buf.viewportY
        const lines = []
        for (let i = fromRow; i <= toRow; i++) {
            lines.push(buf.getLine(base + i)?.translateToString(true) ?? '')
        }
        return lines
    }

    /**
     * Reset the tmux scroll-selection buffer.
     */
    function resetTmuxScrollSelection() {
        tmuxScrollSel = null
        tmuxAutoScrollActive = false
    }

    /**
     * Cancel any active selection and scroll back to bottom.
     * Used when the user scrolls after a selection, or presses Escape.
     */
    function cancelSelectionAndScrollToBottom() {
        terminal?.clearSelection()
        hasSelection.value = false
        if (tmuxScrollSel) {
            resetTmuxScrollSelection()
            // Scroll to bottom to exit tmux copy-mode
            scrollToEdge('bottom')
        }
    }

    /**
     * Check if we're in tmux-normal mode (tmux active, pane NOT in alternate screen).
     * This is the mode where we need the custom buffer for scroll-selection.
     */
    function isTmuxNormal() {
        return shouldUseTmux() && !paneAlternate.value
    }

    /**
     * Initialize the tmux scroll-selection buffer.
     * Called when auto-scroll first triggers in tmux-normal mode.
     * Captures the full visible viewport into an indexed buffer where
     * index 0 = initial viewport row 0. Negative indices = scrolled-up lines.
     */
    function initTmuxScrollSelection() {
        if (!terminal) return
        const rows = terminal.rows
        const anchorVisualRow = selectStartRow - terminal.buffer.active.viewportY

        // Capture entire viewport into indexed buffer
        const lines = {}
        const visibleLines = readVisibleLines(0, rows - 1)
        for (let i = 0; i < rows; i++) {
            lines[i] = visibleLines[i]
        }

        tmuxScrollSel = {
            lines,
            bufferMin: 0,
            bufferMax: rows - 1,
            viewportStart: 0,
            fixedAnchorIdx: anchorVisualRow,
            fixedAnchorCol: selectStartCol,
            movingAnchorIdx: anchorVisualRow,
            movingAnchorCol: selectStartCol,
        }
    }

    /**
     * Update the buffer after a tmux scroll step.
     * Shifts the viewport window and adds any new lines not yet in the buffer.
     *
     * @param {number} actualLines - Lines actually scrolled (neg=up, pos=down)
     */
    function afterTmuxScroll(actualLines) {
        if (!tmuxScrollSel || !terminal || actualLines === 0) return
        const rows = terminal.rows
        const sel = tmuxScrollSel

        const oldViewportStart = sel.viewportStart
        // Shift viewport: scroll up (neg) → viewport indices decrease
        sel.viewportStart += actualLines

        const newViewportEnd = sel.viewportStart + rows - 1

        if (actualLines < 0) {
            // Scrolled up: new lines appeared at the top (lower indices)
            for (let i = sel.viewportStart; i < sel.bufferMin; i++) {
                const visualRow = i - sel.viewportStart
                sel.lines[i] = readVisibleLines(visualRow, visualRow)[0]
            }
            sel.bufferMin = Math.min(sel.bufferMin, sel.viewportStart)
            // Update moving anchor to top edge
            sel.movingAnchorIdx = sel.viewportStart
            sel.movingAnchorCol = 0
        } else {
            // Scrolled down: new lines appeared at the bottom (higher indices)
            for (let i = sel.bufferMax + 1; i <= newViewportEnd; i++) {
                const visualRow = i - sel.viewportStart
                sel.lines[i] = readVisibleLines(visualRow, visualRow)[0]
            }
            sel.bufferMax = Math.max(sel.bufferMax, newViewportEnd)
            // Update moving anchor to bottom edge
            sel.movingAnchorIdx = newViewportEnd
            sel.movingAnchorCol = terminal.cols
        }
    }

    /**
     * Update the visual selection on screen for tmux scroll-selection.
     * Highlights the visible portion of the selection between the two anchors.
     *
     * @param {number|null} fingerVisualRow - Finger's visual row, or null for auto-scroll edge
     * @param {number|null} fingerCol - Finger's column, or null for auto-scroll edge
     */
    function updateTmuxVisualSelection(fingerVisualRow = null, fingerCol = null) {
        if (!tmuxScrollSel || !terminal) return
        const rows = terminal.rows
        const cols = terminal.cols
        const base = terminal.buffer.active.viewportY
        const sel = tmuxScrollSel

        // Update moving anchor from finger position when provided
        if (fingerVisualRow !== null) {
            sel.movingAnchorIdx = sel.viewportStart + fingerVisualRow
            sel.movingAnchorCol = fingerCol ?? 0
        }

        // Selection range in buffer indices
        const fixedOffset = sel.fixedAnchorIdx * cols + sel.fixedAnchorCol
        const movingOffset = sel.movingAnchorIdx * cols + sel.movingAnchorCol
        const selMinIdx = fixedOffset <= movingOffset ? sel.fixedAnchorIdx : sel.movingAnchorIdx
        const selMinCol = fixedOffset <= movingOffset ? sel.fixedAnchorCol : sel.movingAnchorCol
        const selMaxIdx = fixedOffset <= movingOffset ? sel.movingAnchorIdx : sel.fixedAnchorIdx
        const selMaxCol = fixedOffset <= movingOffset ? sel.movingAnchorCol : sel.fixedAnchorCol

        // Clamp to viewport for visual display
        const viewEnd = sel.viewportStart + rows - 1
        const visStart = Math.max(selMinIdx, sel.viewportStart)
        const visEnd = Math.min(selMaxIdx, viewEnd)

        if (visStart > viewEnd || visEnd < sel.viewportStart) return  // nothing visible

        const startVisualRow = visStart - sel.viewportStart
        const endVisualRow = visEnd - sel.viewportStart
        const startCol = (visStart === selMinIdx) ? selMinCol : 0
        const endCol = (visEnd === selMaxIdx) ? selMaxCol : cols

        const length = (endVisualRow * cols + endCol) - (startVisualRow * cols + startCol)
        if (length > 0) terminal.select(startCol, base + startVisualRow, length)
    }

    /**
     * Handle selection update when tmuxScrollSel is active and finger is
     * in the viewport.
     */
    function updateTmuxInViewportSelection(clientX, clientY) {
        if (!tmuxScrollSel || !terminal) return
        const screenEl = terminal.element?.querySelector('.xterm-screen')
        if (!screenEl) return
        const rect = screenEl.getBoundingClientRect()
        const cellWidth = rect.width / terminal.cols
        const cellHeight = rect.height / terminal.rows
        const col = Math.max(0, Math.min(terminal.cols - 1, Math.floor((clientX - rect.left) / cellWidth)))
        const visualRow = Math.max(0, Math.min(terminal.rows - 1, Math.floor((clientY - rect.top) / cellHeight)))

        updateTmuxVisualSelection(visualRow, col)
    }

    /**
     * Get the full selected text from the tmux scroll-selection buffer.
     * Returns null if no tmux scroll-selection is active.
     */
    function getTmuxScrollSelectionText() {
        if (!tmuxScrollSel) return null
        const sel = tmuxScrollSel
        const cols = terminal?.cols ?? 80

        // Determine selection range from the two anchors
        const fixedOffset = sel.fixedAnchorIdx * cols + sel.fixedAnchorCol
        const movingOffset = sel.movingAnchorIdx * cols + sel.movingAnchorCol
        const startIdx = fixedOffset <= movingOffset ? sel.fixedAnchorIdx : sel.movingAnchorIdx
        const startCol = fixedOffset <= movingOffset ? sel.fixedAnchorCol : sel.movingAnchorCol
        const endIdx = fixedOffset <= movingOffset ? sel.movingAnchorIdx : sel.fixedAnchorIdx
        const endCol = fixedOffset <= movingOffset ? sel.movingAnchorCol : sel.fixedAnchorCol

        // Build text from buffer
        const result = []
        for (let i = startIdx; i <= endIdx; i++) {
            let line = sel.lines[i] ?? ''
            if (i === startIdx && i === endIdx) {
                line = line.substring(startCol, endCol)
            } else if (i === startIdx) {
                line = line.substring(startCol)
            } else if (i === endIdx) {
                line = line.substring(0, endCol)
            }
            result.push(line)
        }
        return result.join('\n')
    }

    // ── Selection auto-scroll (edge dragging) ────────────────────────
    // When the finger goes above or below the terminal viewport during
    // a selection drag, auto-scroll in that direction and keep extending
    // the selection. Uses the same cell-height accumulation as scroll mode.

    const AUTO_SCROLL_EDGE_PX = 30  // activation zone from top/bottom edge

    function stopAutoScroll() {
        if (autoScrollId !== null) {
            cancelAnimationFrame(autoScrollId)
            autoScrollId = null
        }
    }

    function startAutoScroll(pixelsPerFrame) {
        if (autoScrollId !== null) return  // already running
        const cellHeight = getCellHeight()
        if (cellHeight <= 0) return

        let accumulator = 0

        function step() {
            if (!terminal) { autoScrollId = null; return }
            accumulator += pixelsPerFrame
            const lines = Math.trunc(accumulator / cellHeight)
            if (lines !== 0) {
                accumulator -= lines * cellHeight
                scrollByLines(lines)
            }
            // Extend selection to current (clamped) finger position
            updateSelection(autoScrollLastClientX, autoScrollLastClientY)
            autoScrollId = requestAnimationFrame(step)
        }

        autoScrollId = requestAnimationFrame(step)
    }

    /**
     * Async auto-scroll loop for tmux-normal mode.
     * Sends tmux_scroll commands sequentially, waiting for each result
     * before sending the next. Updates the custom text buffer with each step.
     *
     * @param {'up'|'down'} direction
     */
    async function startTmuxAutoScroll(direction) {
        if (tmuxAutoScrollActive) return
        tmuxAutoScrollActive = true

        const sign = direction === 'up' ? -1 : 1
        let step = 0

        // Initialize the buffer on first scroll
        if (!tmuxScrollSel) {
            initTmuxScrollSelection()
        }

        try {
            while (tmuxAutoScrollActive) {
                // Ramp up: 1, 1, 2, 2, 3... capped at half the viewport
                // to ensure we never skip lines when capturing the buffer
                step++
                const maxLines = Math.floor(terminal.rows / 2)
                const lines = sign * Math.min(Math.ceil(step / 2), maxLines)

                const result = await new Promise(resolve => {
                    onScrollResult = resolve
                    scrollByLines(lines)
                })

                if (!tmuxAutoScrollActive) break

                afterTmuxScroll(result.actual)
                updateTmuxVisualSelection()

                if (result.actual === 0 || result.atTop || result.atBottom) break

                // Pause between steps — starts slow (200ms), speeds up to 50ms
                const delay = Math.max(50, 200 - step * 15)
                await new Promise(r => setTimeout(r, delay))
            }
        } finally {
            tmuxAutoScrollActive = false
        }
    }

    function stopTmuxAutoScroll() {
        tmuxAutoScrollActive = false
    }

    /**
     * Check whether the pointer position is outside the terminal viewport
     * and start/stop auto-scrolling accordingly.
     *
     * Three modes:
     * - Normal (no tmux): rAF-based smooth scroll with native xterm selection
     * - Tmux-normal: async sequential scroll with custom text buffer
     * - Alternate (with or without tmux): disabled (program manages viewport)
     */
    function handleAutoScroll(clientY) {
        // In alternate screen, selection is limited to visible content
        if (paneAlternate.value || (!shouldUseTmux() && isAlternateScreen())) {
            stopAutoScroll()
            stopTmuxAutoScroll()
            return
        }

        const screenEl = terminal?.element?.querySelector('.xterm-screen')
        if (!screenEl) return
        const rect = screenEl.getBoundingClientRect()
        const overTop = rect.top - clientY     // >0 when above
        const overBottom = clientY - rect.bottom // >0 when below

        if (isTmuxNormal()) {
            // Tmux-normal: async scroll with buffer
            if (overTop > 0) {
                stopAutoScroll()
                startTmuxAutoScroll('up')
            } else if (overBottom > 0) {
                stopAutoScroll()
                startTmuxAutoScroll('down')
            } else {
                stopTmuxAutoScroll()
            }
        } else {
            // Normal mode: rAF-based smooth scroll
            stopTmuxAutoScroll()
            if (overTop > 0) {
                const speed = -(AUTO_SCROLL_EDGE_PX + overTop) / 4
                stopAutoScroll()
                startAutoScroll(speed)
            } else if (overBottom > 0) {
                const speed = (AUTO_SCROLL_EDGE_PX + overBottom) / 4
                stopAutoScroll()
                startAutoScroll(speed)
            } else {
                stopAutoScroll()
            }
        }
    }

    function onTouchStart(e) {
        // Ignore touches on the custom scrollbar — let xterm.js handle those via pointer events
        if (e.target?.closest('.scrollbar')) {
            touchIsSelecting = false
            return
        }
        touchIsSelecting = true
        stopAutoScroll()
        stopTmuxAutoScroll()
        resetTmuxScrollSelection()
        const touch = e.touches[0]
        const coords = screenToTerminalCoords(touch.clientX, touch.clientY)
        selectStartCol = coords.col
        selectStartRow = coords.row
        terminal?.clearSelection()
    }

    function onTouchMove(e) {
        if (!touchIsSelecting || !terminal) return
        const touch = e.touches[0]

        // Store current finger position for auto-scroll selection updates
        autoScrollLastClientX = touch.clientX
        autoScrollLastClientY = touch.clientY

        if (tmuxScrollSel) {
            updateTmuxInViewportSelection(touch.clientX, touch.clientY)
        } else {
            updateSelection(touch.clientX, touch.clientY)
        }
        handleAutoScroll(touch.clientY)
        e.preventDefault()
    }

    function onTouchEnd() {
        stopAutoScroll()
        stopTmuxAutoScroll()
    }

    // ── Scroll dispatch ────────────────────────────────────────────────
    // Four strategies depending on context:
    //
    // 1. Alternate screen app inside tmux (vim, less, htop…):
    //    paneAlternate=true → send arrow keys (the app handles scrolling)
    //
    // 2. Shell prompt inside tmux (paneAlternate=false, tmux active):
    //    Send SGR mouse wheel events → tmux enters copy-mode scrollback
    //
    // 3. Alternate screen app without tmux (less, vim run directly):
    //    xterm.js detects alternate buffer → send arrow keys
    //
    // 4. Normal shell (no tmux, no alternate screen):
    //    scroll xterm.js viewport buffer directly

    function isAlternateScreen() {
        return terminal?.buffer?.active?.type === 'alternate'
    }

    // ── Scroll boundary detection ───────────────────────────────────────
    // Detects whether a scroll request actually moved the viewport and
    // whether top/bottom boundaries were reached.
    //
    // An optional onScrollResult callback can be set before calling
    // scrollByLines() to receive the result asynchronously. Used by
    // scrollToEdge() to iterate until a boundary is reached.

    /** @type {((result: {requested: number, actual: number, atTop: boolean, atBottom: boolean}) => void) | null} */
    let onScrollResult = null

    /**
     * Capture a fingerprint of the visible screen content.
     * Uses the first and last 3 lines for lightweight comparison.
     */
    function captureScreenFingerprint() {
        if (!terminal) return null
        const buf = terminal.buffer.active
        const base = buf.viewportY
        const rows = terminal.rows
        const lines = []
        // First 3 lines
        for (let i = 0; i < Math.min(3, rows); i++) {
            lines.push(buf.getLine(base + i)?.translateToString(true) ?? '')
        }
        // Last 3 lines
        for (let i = Math.max(0, rows - 3); i < rows; i++) {
            lines.push(buf.getLine(base + i)?.translateToString(true) ?? '')
        }
        return lines.join('\n')
    }

    /** Pending alternate-screen scroll detection (waiting for re-render). */
    let pendingAlternateScroll = null

    /**
     * Detect scroll result for alternate screen modes (with or without tmux).
     * Called before sending arrow keys, sets up a render listener to compare
     * screen content after the program redraws.
     */
    function detectAlternateScrollResult(requested, mode) {
        const before = captureScreenFingerprint()
        pendingAlternateScroll = { requested, mode, before }

        // Wait for terminal re-render, then compare
        const disposable = terminal.onRender(() => {
            disposable.dispose()
            if (!pendingAlternateScroll) return
            const { requested: req, mode: m, before: bfr } = pendingAlternateScroll
            pendingAlternateScroll = null

            const after = captureScreenFingerprint()
            const changed = bfr !== after
            const atTop = req < 0 && !changed
            const atBottom = req > 0 && !changed
            // Estimate actual lines: if unchanged → 0, otherwise assume full request
            // (we can't know exactly how many lines a program scrolled)
            const actual = changed ? req : 0
            // Distances are unknown in alternate screen — only 0 at boundaries
            const distanceToTop = atTop ? 0 : null
            const distanceToBottom = atBottom ? 0 : null


            if (onScrollResult) {
                const cb = onScrollResult
                onScrollResult = null
                cb({ requested: req, actual, atTop, atBottom })
            }
        })

        // Safety timeout: if no render happens within 500ms, the screen
        // didn't change — we're at a boundary.
        setTimeout(() => {
            if (pendingAlternateScroll) {
                const { requested: req, mode: m } = pendingAlternateScroll
                pendingAlternateScroll = null
                disposable.dispose()
                const atTop = req < 0
                const atBottom = req > 0
                const distanceToTop = atTop ? 0 : null
                const distanceToBottom = atBottom ? 0 : null

                if (onScrollResult) {
                    const cb = onScrollResult
                    onScrollResult = null
                    cb({ requested: req, actual: 0, atTop, atBottom })
                }
            }
        }, 500)
    }

    /**
     * Scroll by the given number of lines, using the right strategy
     * for the current screen mode.
     * Positive = scroll down, negative = scroll up.
     */
    function scrollByLines(lines) {
        if (!terminal || lines === 0) return

        if (paneAlternate.value) {
            // Alternate screen app inside tmux (vim, less, htop…):
            // send arrow keys — the app handles scrolling.
            // Use SS3 sequences when application cursor mode is active (DECCKM).

            detectAlternateScrollResult(lines, 'tmux+alternate')
            const appCursor = terminal?.modes?.applicationCursorKeysMode ?? false
            const key = lines > 0
                ? (appCursor ? '\x1bOB' : '\x1b[B')
                : (appCursor ? '\x1bOA' : '\x1b[A')
            wsSend({ type: 'input', data: key.repeat(Math.abs(lines)) })
        } else if (shouldUseTmux()) {
            // Shell prompt inside tmux: use backend tmux command to scroll
            // exactly N lines in copy-mode, bypassing tmux's mouse handling
            // which adds its own scroll on top of our bindings.

            wsSend({ type: 'tmux_scroll', lines })
            // Result comes back via scroll_result WebSocket message
        } else if (isAlternateScreen()) {
            // Alternate screen app without tmux (less, vim run directly):
            // send arrow keys — the app handles scrolling.
            // Position is unknown, always show both scroll buttons.
            canScrollUp.value = true
            canScrollDown.value = true

            detectAlternateScrollResult(lines, 'alternate')
            const appCursor = terminal?.modes?.applicationCursorKeysMode ?? false
            const key = lines > 0
                ? (appCursor ? '\x1bOB' : '\x1b[B')
                : (appCursor ? '\x1bOA' : '\x1b[A')
            wsSend({ type: 'input', data: key.repeat(Math.abs(lines)) })
        } else {
            // Normal shell (no tmux, no alternate screen):
            // scroll xterm.js viewport buffer.
            const before = terminal.buffer.active.viewportY
            terminal.scrollLines(lines)
            const after = terminal.buffer.active.viewportY
            const baseY = terminal.buffer.active.baseY
            const actual = after - before
            const atTop = after === 0
            const atBottom = after === baseY
            const distanceToTop = after
            const distanceToBottom = baseY - after
            canScrollUp.value = !atTop
            canScrollDown.value = !atBottom

            if (onScrollResult) {
                const cb = onScrollResult
                onScrollResult = null
                cb({ requested: lines, actual, atTop, atBottom })
            }
        }
    }

    // ── Scroll to edge ────────────────────────────────────────────────

    /** Whether a scroll-to-edge operation is in progress. */
    const scrollingToEdge = ref(false)
    let scrollToEdgeCancelled = false

    /**
     * Scroll to the top or bottom of the terminal content.
     * In normal mode, uses terminal.scrollToTop/Bottom() directly.
     * In all other modes, scrolls in chunks of 100 lines until
     * the boundary is reached (content stops changing).
     *
     * @param {'top'|'bottom'} direction
     */
    async function scrollToEdge(direction) {
        if (!terminal || scrollingToEdge.value) return

        const isNormal = !paneAlternate.value && !shouldUseTmux() && !isAlternateScreen()

        if (isNormal) {
            if (direction === 'top') terminal.scrollToTop()
            else terminal.scrollToBottom()
            return
        }

        // Chunked scroll for tmux / alternate modes
        scrollingToEdge.value = true
        scrollToEdgeCancelled = false
        const chunkSize = direction === 'top' ? -100 : 100

        try {
            while (!scrollToEdgeCancelled) {
                const result = await new Promise(resolve => {
                    onScrollResult = resolve
                    scrollByLines(chunkSize)
                })
                if (result.atTop || result.atBottom || result.actual === 0) break
            }
        } finally {
            scrollingToEdge.value = false
            scrollToEdgeCancelled = false
        }
    }

    function cancelScrollToEdge() {
        scrollToEdgeCancelled = true
    }

    // ── Touch scroll handlers (mobile, scroll mode) ────────────────────
    // Pixel accumulation for 1:1 finger tracking and momentum inertia
    // on touch release for a native-like feel. Delegates to scrollByLines()
    // which picks the right strategy (buffer scroll vs arrow keys).

    const SCROLL_DECELERATION = 0.92   // friction per frame (~60fps)
    const SCROLL_MIN_VELOCITY = 0.3    // px/frame threshold to stop inertia

    function getCellHeight() {
        const screenEl = terminal?.element?.querySelector('.xterm-screen')
        if (!screenEl) return 0
        return screenEl.getBoundingClientRect().height / terminal.rows
    }

    function stopScrollInertia() {
        if (scrollInertiaId !== null) {
            cancelAnimationFrame(scrollInertiaId)
            scrollInertiaId = null
        }
    }

    function onScrollTouchStart(e) {
        stopScrollInertia()
        // Scrolling after a selection cancels the selection
        if (tmuxScrollSel || terminal?.getSelection()) {
            terminal?.clearSelection()
            hasSelection.value = false
            resetTmuxScrollSelection()
        }
        const touch = e.touches[0]
        scrollLastY = touch.clientY
        scrollLastTime = e.timeStamp
        scrollVelocity = 0
        scrollAccumulator = 0
    }

    function onScrollTouchMove(e) {
        if (!terminal) return
        const touch = e.touches[0]
        const now = e.timeStamp
        const deltaY = scrollLastY - touch.clientY  // positive = scroll down
        const deltaTime = now - scrollLastTime

        // Accumulate pixel delta and convert to whole lines
        const cellHeight = getCellHeight()
        if (cellHeight > 0) {
            scrollAccumulator += deltaY
            const lines = Math.trunc(scrollAccumulator / cellHeight)
            if (lines !== 0) {
                scrollAccumulator -= lines * cellHeight
                scrollByLines(lines)
            }
        }

        // Track velocity (px/ms) with smoothing to avoid jitter
        if (deltaTime > 0) {
            const instantVelocity = deltaY / deltaTime
            scrollVelocity = scrollVelocity * 0.4 + instantVelocity * 0.6
        }

        scrollLastY = touch.clientY
        scrollLastTime = now
        e.preventDefault()
    }

    function onScrollTouchEnd() {
        if (!terminal || Math.abs(scrollVelocity) < 0.05) return

        const cellHeight = getCellHeight()
        if (cellHeight <= 0) return

        // Convert velocity from px/ms to px/frame (~16.67ms at 60fps)
        let frameVelocity = scrollVelocity * 16.67
        let accumulator = scrollAccumulator

        function inertiaStep() {
            frameVelocity *= SCROLL_DECELERATION
            if (Math.abs(frameVelocity) < SCROLL_MIN_VELOCITY) {
                scrollInertiaId = null
                return
            }
            accumulator += frameVelocity
            const lines = Math.trunc(accumulator / cellHeight)
            if (lines !== 0) {
                accumulator -= lines * cellHeight
                scrollByLines(lines)
            }
            scrollInertiaId = requestAnimationFrame(inertiaStep)
        }

        scrollInertiaId = requestAnimationFrame(inertiaStep)
    }

    // ── Attach / detach touch listeners ──────────────────────────────

    /**
     * Attach touch event listeners for the current touchMode.
     * Uses an AbortController for clean removal.
     */
    function attachTouchListeners() {
        if (!containerRef.value || !settingsStore.isTouchDevice) return

        touchAbortController = new AbortController()
        const signal = touchAbortController.signal

        if (touchMode.value === 'select') {
            containerRef.value.addEventListener('touchstart', onTouchStart, { passive: true, signal, capture: true })
            containerRef.value.addEventListener('touchmove', onTouchMove, { passive: false, signal, capture: true })
            containerRef.value.addEventListener('touchend', onTouchEnd, { passive: true, signal, capture: true })
        } else {
            containerRef.value.addEventListener('touchstart', onScrollTouchStart, { passive: true, signal, capture: true })
            containerRef.value.addEventListener('touchmove', onScrollTouchMove, { passive: false, signal, capture: true })
            containerRef.value.addEventListener('touchend', onScrollTouchEnd, { passive: true, signal, capture: true })
        }
    }

    /**
     * Detach touch event listeners and cancel any running inertia animation.
     */
    function detachTouchListeners() {
        stopAutoScroll()
        stopTmuxAutoScroll()
        resetTmuxScrollSelection()
        stopScrollInertia()
        if (touchAbortController) {
            touchAbortController.abort()
            touchAbortController = null
        }
    }

    // ── Desktop mouse drag selection (tmux only) ──────────────────────
    // When tmux has mouse mode enabled, it captures drag events and
    // enters copy-mode, breaking xterm.js native selection. These
    // handlers intercept drag in capture phase to create selection
    // via terminal.select() instead, reusing the same anchor variables
    // and updateSelection() as touch selection.

    function onDesktopMouseDown(e) {
        if (e.button !== 0) return  // only left button
        desktopDragActive = false
        resetTmuxScrollSelection()
        const coords = screenToTerminalCoords(e.clientX, e.clientY)
        selectStartCol = coords.col
        selectStartRow = coords.row
    }

    function onDesktopMouseMove(e) {
        if (!(e.buttons & 1)) return  // left button not held

        if (!desktopDragActive) {
            desktopDragActive = true
            terminal?.clearSelection()
        }

        // Stop event from reaching xterm.js — prevents it from sending
        // MouseDrag escape sequences to tmux (which would enter copy-mode)
        e.stopPropagation()
        e.preventDefault()

        // Store position for auto-scroll (reuses the same vars as touch)
        autoScrollLastClientX = e.clientX
        autoScrollLastClientY = e.clientY

        if (tmuxScrollSel) {
            updateTmuxInViewportSelection(e.clientX, e.clientY)
        } else {
            updateSelection(e.clientX, e.clientY)
        }
        handleAutoScroll(e.clientY)
    }

    function onDesktopMouseUp(e) {
        if (desktopDragActive) {
            desktopDragActive = false
            stopAutoScroll()
            stopTmuxAutoScroll()
            e.stopPropagation()
            e.preventDefault()
        }
    }

    /**
     * Intercept mouse wheel on desktop when in tmux.
     * Uses the same scrollByLines() as mobile touch scroll — sends
     * controlled, batched events instead of letting xterm.js forward
     * rapid-fire wheel events to tmux (which causes copy-mode artifacts).
     */
    function onDesktopWheel(e) {
        e.preventDefault()
        e.stopPropagation()

        // Scrolling after a selection cancels the selection
        if (tmuxScrollSel || terminal?.getSelection()) {
            terminal?.clearSelection()
            hasSelection.value = false
            resetTmuxScrollSelection()
        }

        const cellHeight = getCellHeight()
        if (cellHeight <= 0) return

        // Accumulate pixel delta (same approach as mobile touch scroll)
        desktopWheelAccumulator += e.deltaY
        const lines = Math.trunc(desktopWheelAccumulator / cellHeight)
        if (lines !== 0) {
            desktopWheelAccumulator -= lines * cellHeight
            scrollByLines(lines)
        }
    }

    function attachDesktopDragListeners() {
        if (!containerRef.value || settingsStore.isTouchDevice || !shouldUseTmux()) return

        desktopDragAbortController = new AbortController()
        const signal = desktopDragAbortController.signal

        containerRef.value.addEventListener('mousedown', onDesktopMouseDown, { capture: true, signal })
        containerRef.value.addEventListener('mousemove', onDesktopMouseMove, { capture: true, signal })
        containerRef.value.addEventListener('mouseup', onDesktopMouseUp, { capture: true, signal })
        containerRef.value.addEventListener('wheel', onDesktopWheel, { capture: true, passive: false, signal })
    }

    function detachDesktopDragListeners() {
        if (desktopDragAbortController) {
            desktopDragAbortController.abort()
            desktopDragAbortController = null
        }
    }

    /**
     * Initialize the xterm.js Terminal and attach it to the container,
     * then connect the WebSocket.
     */
    function initTerminal() {
        if (!containerRef.value || !sessionId) return

        const effectiveTheme = settingsStore.getEffectiveTheme
        terminal = new Terminal({
            cursorBlink: true,
            fontSize: settingsStore.getFontSize,
            fontFamily: '"Fira Code", "Cascadia Code", "JetBrains Mono", Menlo, Monaco, "Courier New", monospace',
            theme: THEMES[effectiveTheme] || THEMES.dark,
            scrollback: 5000,
            convertEol: true,
            scrollOnUserInput: true,
            macOptionIsMeta: true,
            macOptionClickForcesSelection: true,
        })

        fitAddon = new FitAddon()
        terminal.loadAddon(fitAddon)
        terminal.loadAddon(new WebLinksAddon())
        terminal.loadAddon(new ClipboardAddon())

        terminal.open(containerRef.value)

        // Tweak the hidden textarea that xterm.js uses for keyboard input.
        // Disables autocorrect, autocomplete, spell-check, and third-party
        // extensions (Grammarly, LastPass, etc.) that interfere on mobile.
        // From: https://github.com/btli/remote-dev/commit/fd17d5b17f87b5c4ff2f6e9d7b8d36cdb9e8cea2
        const textarea = containerRef.value.querySelector('.xterm-helper-textarea')
        if (textarea) {
            const attrs = {
                autocomplete: 'off',
                enterkeyhint: 'send',
                'data-gramm': 'false',
                'data-gramm_editor': 'false',
                'data-enable-grammarly': 'false',
                'data-form-type': 'other',
                'data-lpignore': 'true',
                'x-webkit-speech': 'false',
            }
            for (const [key, value] of Object.entries(attrs)) {
                textarea.setAttribute(key, value)
            }
        }

        // Fit immediately
        fitAddon.fit()

        // Custom key event handler — runs BEFORE xterm.js's own keydown processing.
        // This is where we intercept keys that xterm.js would otherwise mishandle.
        terminal.attachCustomKeyEventHandler((event) => {
            if (event.type !== 'keydown') return true

            // ── Mobile special-key handling (touch devices only) ─────────
            // On mobile, xterm.js unreliably translates special keys (arrows,
            // Home, Tab, Ctrl+letter, etc.) to ANSI sequences — sometimes
            // because the IME reports keyCode=229, sometimes for other reasons
            // (composition state, input event interference, etc.).
            // On touch devices, we bypass xterm.js entirely for any special
            // key we know how to translate, using event.key which is always
            // reliable. Regular character input (letters, digits, punctuation)
            // is unaffected — imeKeyToAnsiSequence returns null for those.
            if (settingsStore.isTouchDevice) {
                // Merge extra-keys-bar modifiers with the event's own modifiers
                const mergedEvent = (activeModifiers.ctrl || activeModifiers.alt || activeModifiers.shift)
                    ? {
                        key: event.key,
                        ctrlKey: event.ctrlKey || activeModifiers.ctrl,
                        altKey: event.altKey || activeModifiers.alt,
                        shiftKey: event.shiftKey || activeModifiers.shift,
                    }
                    : event
                const appCursor = terminal?.modes?.applicationCursorKeysMode ?? false
                const sequence = imeKeyToAnsiSequence(mergedEvent, { ignoreShift: !activeModifiers.shift, applicationCursorMode: appCursor })
                if (sequence) {
                    event.preventDefault()
                    wsSend({ type: 'input', data: sequence })
                    if (mergedEvent !== event) resetOneShotModifiers()
                    return false
                }
                // If no sequence but extra modifiers were active and it's a single char,
                // the modifier was meant for this key
                if (mergedEvent !== event && event.key.length === 1) {
                    event.preventDefault()
                    if (activeModifiers.alt) {
                        wsSend({ type: 'input', data: `\x1b${event.key}` })
                    } else {
                        wsSend({ type: 'input', data: event.key })
                    }
                    resetOneShotModifiers()
                    return false
                }
            }

            // Escape: cancel selection and scroll back to bottom
            if (event.code === 'Escape') {
                if (tmuxScrollSel || terminal.getSelection()) {
                    cancelSelectionAndScrollToBottom()
                    event.preventDefault()
                    return false
                }
                // No selection: also scroll to bottom if scrolled up
                if (canScrollDown.value) {
                    scrollToEdge('bottom')
                    event.preventDefault()
                    return false
                }
            }

            // Intercept Ctrl+C when text is selected: copy to clipboard
            // instead of sending SIGINT to the terminal process.
            // Ctrl+Shift+C also copies (standard terminal emulator shortcut).
            if (event.ctrlKey && (event.code === 'KeyC')) {
                const selection = getTmuxScrollSelectionText() || terminal.getSelection()
                if (selection) {
                    navigator.clipboard.writeText(selection)
                    terminal.clearSelection()
                    resetTmuxScrollSelection()
                    toast.success('Copied to clipboard', { duration: 2000 })
                    event.preventDefault()
                    return false
                }
                // No selection: let Ctrl+C through as SIGINT (default xterm.js behavior)
            }

            return true
        })

        // Track selection state for mobile copy button.
        // No auto-copy: on mobile the user copies via the explicit button,
        // on desktop the user copies via right-click or Ctrl+C.
        // Track selection state for the copy button (all devices).
        terminal.onSelectionChange(() => {
            hasSelection.value = !!terminal.getSelection() || !!tmuxScrollSel
        })

        // Forward user input to the WebSocket
        terminal.onData((data) => {
            wsSend({ type: 'input', data })
        })

        // Track scroll position for normal mode (non-tmux, non-alternate).
        // Updates canScrollUp/canScrollDown reactively when the user scrolls
        // via mouse wheel (handled natively by xterm.js).
        terminal.onScroll(() => {
            if (shouldUseTmux()) return  // tmux: handled by pane_state monitor
            if (isAlternateScreen()) {
                // Non-tmux alternate: position unknown, always show both buttons
                canScrollUp.value = true
                canScrollDown.value = true
            } else {
                const y = terminal.buffer.active.viewportY
                const base = terminal.buffer.active.baseY
                canScrollUp.value = y > 0
                canScrollDown.value = y < base
            }
        })

        // Watch for container resize
        resizeObserver = new ResizeObserver(() => {
            // Debounce slightly to avoid rapid resize events
            requestAnimationFrame(() => {
                if (fitAddon && terminal) {
                    fitAddon.fit()
                    // Notify the backend of new dimensions
                    wsSend({ type: 'resize', cols: terminal.cols, rows: terminal.rows })
                }
            })
        })
        resizeObserver.observe(containerRef.value)

        // Attach touch listeners for mobile text selection
        attachTouchListeners()
        attachDesktopDragListeners()

        // Connect to the backend
        connectWs()
    }

    /**
     * Start the terminal (lazy init). Called once when the tab first becomes active.
     * Subsequent calls are no-ops.
     */
    function start() {
        if (started.value) return
        started.value = true

        if (containerRef.value && sessionId) {
            initTerminal()
        }
        // If containerRef isn't ready yet, the watcher below will pick it up
    }

    /**
     * Manually reconnect after a disconnection.
     */
    function reconnect() {
        if (isConnected.value) return
        terminal?.writeln('\x1b[33mReconnecting...\x1b[0m')
        connectWs()
    }

    /**
     * Intentionally disconnect the terminal session.
     * Closes the WebSocket (which kills the PTY on the backend) and
     * writes a message to the terminal so the user sees the disconnect.
     */
    function disconnect() {
        if (!ws) return
        // Send Ctrl+D (EOF) to the PTY so the shell terminates properly.
        // In tmux this closes the pane and ends the session.
        wsSend({ type: 'input', data: '\x04' })
    }

    /**
     * Copy the current terminal selection to clipboard, clear selection, and show a toast.
     * Used by the explicit copy button on mobile.
     */
    function copySelection() {
        if (!terminal) return
        const selection = getTmuxScrollSelectionText() || terminal.getSelection()
        if (!selection) return
        clipboardWrite(selection)
        terminal.clearSelection()
        hasSelection.value = false
        resetTmuxScrollSelection()
        toast.success('Copied to clipboard', { duration: 2000 })
    }

    // ── Extra keys bar handlers ────────────────────────────────────────

    /**
     * Handle modifier toggle from the extra keys bar.
     * Called with the modifier name and whether it should be locked.
     *
     * Three transitions:
     * - Inactive → one-shot: active=true, locked=false
     * - Inactive → locked (double-tap): active=true, locked=true
     * - Active/locked → inactive: active=false, locked=false
     */
    function handleExtraKeyModifierToggle(modifier, locked) {
        if (activeModifiers[modifier] && !locked) {
            // Was active (one-shot or locked) → deactivate
            activeModifiers[modifier] = false
            lockedModifiers[modifier] = false
        } else if (locked) {
            // Double-tap → lock
            activeModifiers[modifier] = true
            lockedModifiers[modifier] = true
        } else {
            // Single tap → one-shot
            activeModifiers[modifier] = true
            lockedModifiers[modifier] = false
        }
    }

    /**
     * Reset all one-shot (non-locked) modifiers after a key has been sent.
     */
    function resetOneShotModifiers() {
        for (const mod of ['ctrl', 'alt', 'shift']) {
            if (activeModifiers[mod] && !lockedModifiers[mod]) {
                activeModifiers[mod] = false
            }
        }
    }

    /**
     * Handle a key press from the extra keys bar.
     * Constructs a synthetic key descriptor with current modifier state,
     * converts to ANSI via imeKeyToAnsiSequence, and sends to the PTY.
     *
     * @param {string} key - Key identifier (e.g. 'Escape', 'Tab', 'ArrowUp', '/', 'F5')
     */
    function handleExtraKeyInput(key) {
        const syntheticEvent = {
            key,
            ctrlKey: activeModifiers.ctrl,
            altKey: activeModifiers.alt,
            shiftKey: activeModifiers.shift,
        }

        // Try ANSI sequence conversion (handles special keys, Ctrl+letter, Alt+letter)
        const appCursor = terminal?.modes?.applicationCursorKeysMode ?? false
        const sequence = imeKeyToAnsiSequence(syntheticEvent, { ignoreShift: false, applicationCursorMode: appCursor })

        if (sequence) {
            wsSend({ type: 'input', data: sequence })
        } else {
            // Simple character — send directly (with Alt prefix if applicable)
            if (activeModifiers.alt) {
                wsSend({ type: 'input', data: `\x1b${key}` })
            } else {
                wsSend({ type: 'input', data: key })
            }
        }

        resetOneShotModifiers()
        terminal?.focus()
    }

    /**
     * Handle paste from the extra keys bar.
     * Reads clipboard and sends content to the PTY.
     * Must be called from a click event handler (not pointerdown)
     * to preserve the user gesture chain for the Clipboard API.
     */
    async function handleExtraKeyPaste() {
        try {
            const text = await navigator.clipboard.readText()
            if (text) {
                wsSend({ type: 'input', data: text })
            }
        } catch {
            toast.error('Clipboard access denied')
        }
        terminal?.focus()
    }

    /**
     * Handle a custom combo button press. Sends each step's ANSI sequence
     * in order. Combos are self-contained — they don't interact with
     * the one-shot modifier state from the Essentials tab.
     */
    function handleComboPress(combo) {
        for (const step of combo.steps) {
            const syntheticEvent = {
                key: step.key,
                ctrlKey: step.modifiers?.includes('ctrl') ?? false,
                altKey: step.modifiers?.includes('alt') ?? false,
                shiftKey: step.modifiers?.includes('shift') ?? false,
            }
            const appCursor = terminal?.modes?.applicationCursorKeysMode ?? false
            const sequence = imeKeyToAnsiSequence(syntheticEvent, { ignoreShift: false, applicationCursorMode: appCursor })
            if (sequence) {
                wsSend({ type: 'input', data: sequence })
            } else {
                const char = step.key
                if (syntheticEvent.altKey) {
                    wsSend({ type: 'input', data: `\x1b${char}` })
                } else {
                    wsSend({ type: 'input', data: char })
                }
            }
        }
        terminal?.focus()
    }

    /**
     * Handle a snippet button press. Resolves placeholders, then sends
     * the text as raw terminal input, optionally appending a newline.
     */
    function handleSnippetPress(snippet) {
        const placeholders = snippet.placeholders || []
        let text = snippet.snippet
        if (placeholders.length > 0) {
            const session = dataStore.getSession(sessionId)
            const pid = session?.project_id
            const project = pid ? dataStore.getProject(pid) : null
            const projectName = pid ? dataStore.getProjectDisplayName(pid) : null
            text = resolveSnippetText(text, placeholders, { session, project, projectName })
        }
        if (snippet.appendEnter) {
            text += '\n'
        }
        wsSend({ type: 'input', data: text })
        terminal?.focus()
    }

    /**
     * Clean up everything: terminal, WebSocket, observers.
     */
    function cleanup() {
        intentionalClose = true

        detachTouchListeners()
        detachDesktopDragListeners()

        if (resizeObserver) {
            resizeObserver.disconnect()
            resizeObserver = null
        }

        if (ws) {
            ws.close()
            ws = null
        }

        if (terminal) {
            terminal.dispose()
            terminal = null
        }

        fitAddon = null
        isConnected.value = false
    }

    // Watch containerRef in case it's set after start() (v-if scenarios)
    watch(containerRef, (el) => {
        if (el && !terminal && started.value && sessionId) {
            initTerminal()
        }
    })

    // Close the terminal WebSocket when the session is archived
    watch(
        () => dataStore.getSession(sessionId)?.archived,
        (archived) => {
            if (archived && ws) {
                intentionalClose = true
                ws.close()
                ws = null
                isConnected.value = false
                terminal?.writeln('\x1b[31mSession archived — terminal disconnected.\x1b[0m')
            }
        },
    )

    // Toggle touch listeners when touchMode changes (mobile only)
    watch(touchMode, (mode) => {
        if (!settingsStore.isTouchDevice || !terminal) return
        detachTouchListeners()
        attachTouchListeners()
        if (mode === 'scroll') {
            // Switching to scroll mode: clear any existing selection
            terminal.clearSelection()
            hasSelection.value = false
        }
    })

    // Switch theme live when the user toggles dark/light mode
    watch(() => settingsStore.getEffectiveTheme, (newTheme) => {
        if (terminal) {
            terminal.options.theme = THEMES[newTheme] || THEMES.dark
        }
    })

    // Update font size live when the user changes it in settings
    watch(() => settingsStore.getFontSize, (newSize) => {
        if (terminal) {
            terminal.options.fontSize = newSize
            fitAddon?.fit()
        }
    })

    // Full cleanup when the component is destroyed (KeepAlive eviction or leaving view)
    onUnmounted(() => {
        cleanup()
    })

    return {
        containerRef, isConnected, started, start, reconnect, disconnect,
        // Touch mode (mobile)
        touchMode, hasSelection, copySelection,
        // Scroll state
        paneAlternate, canScrollUp, canScrollDown,
        scrollToEdge, scrollingToEdge, cancelScrollToEdge,
        // Extra keys bar
        activeModifiers, lockedModifiers,
        handleExtraKeyInput, handleExtraKeyModifierToggle, handleExtraKeyPaste,
        handleComboPress, handleSnippetPress,
    }
}

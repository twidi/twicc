// frontend/src/composables/useTerminal.js

import { ref, reactive, watch, onMounted, onUnmounted } from 'vue'
import { Terminal } from '@xterm/xterm'
import { FitAddon } from '@xterm/addon-fit'
import { WebLinksAddon } from '@xterm/addon-web-links'
import { ClipboardAddon } from '@xterm/addon-clipboard'
import { useSettingsStore } from '../stores/settings'
import { useDataStore } from '../stores/data'
import { toast } from '../composables/useToast'
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

    // ── Touch state (mobile) ─────────────────────────────────────────────
    let selectStartCol = 0
    let selectStartRow = 0
    // Auto-scroll state for selection mode (edge dragging)
    let autoScrollId = null
    let autoScrollLastClientX = 0
    let autoScrollLastClientY = 0
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
     * Check whether the touch position is outside the terminal viewport
     * and start/stop/update auto-scrolling accordingly.
     *
     * Disabled in alternate screen mode (less, vim…): the program manages
     * its own viewport, so scrolling would rewrite screen content and
     * invalidate the selection coordinates.
     */
    function handleAutoScroll(clientY) {
        // In alternate screen, selection is limited to visible content
        if (isAlternateScreen() || paneAlternate.value) {
            stopAutoScroll()
            return
        }

        const screenEl = terminal?.element?.querySelector('.xterm-screen')
        if (!screenEl) return
        const rect = screenEl.getBoundingClientRect()
        const overTop = rect.top - clientY     // >0 when above
        const overBottom = clientY - rect.bottom // >0 when below

        if (overTop > 0) {
            // Finger above viewport — scroll up (negative)
            const speed = -(AUTO_SCROLL_EDGE_PX + overTop) / 4
            stopAutoScroll()
            startAutoScroll(speed)
        } else if (overBottom > 0) {
            // Finger below viewport — scroll down (positive)
            const speed = (AUTO_SCROLL_EDGE_PX + overBottom) / 4
            stopAutoScroll()
            startAutoScroll(speed)
        } else {
            stopAutoScroll()
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

        updateSelection(touch.clientX, touch.clientY)
        handleAutoScroll(touch.clientY)
        e.preventDefault()
    }

    function onTouchEnd() {
        stopAutoScroll()
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
            const appCursor = terminal?.modes?.applicationCursorKeysMode ?? false
            const key = lines > 0
                ? (appCursor ? '\x1bOB' : '\x1b[B')
                : (appCursor ? '\x1bOA' : '\x1b[A')
            wsSend({ type: 'input', data: key.repeat(Math.abs(lines)) })
        } else if (shouldUseTmux()) {
            // Shell prompt inside tmux: send SGR mouse wheel sequences.
            // tmux copy-mode handles the scrollback.
            // Button 64 = wheel up, 65 = wheel down.
            const button = lines > 0 ? 65 : 64
            const col = Math.floor(terminal.cols / 2)
            const row = Math.floor(terminal.rows / 2)
            const event = `\x1b[<${button};${col};${row}M`
            wsSend({ type: 'input', data: event.repeat(Math.abs(lines)) })
        } else if (isAlternateScreen()) {
            // Alternate screen app without tmux (less, vim run directly):
            // send arrow keys — the app handles scrolling.
            const appCursor = terminal?.modes?.applicationCursorKeysMode ?? false
            const key = lines > 0
                ? (appCursor ? '\x1bOB' : '\x1b[B')
                : (appCursor ? '\x1bOA' : '\x1b[A')
            wsSend({ type: 'input', data: key.repeat(Math.abs(lines)) })
        } else {
            // Normal shell (no tmux, no alternate screen):
            // scroll xterm.js viewport buffer.
            terminal.scrollLines(lines)
        }
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

        updateSelection(e.clientX, e.clientY)
    }

    function onDesktopMouseUp(e) {
        if (desktopDragActive) {
            desktopDragActive = false
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

            // Intercept Ctrl+Shift+C to copy selection instead of opening DevTools
            if (event.ctrlKey && event.shiftKey && event.code === 'KeyC') {
                const selection = terminal.getSelection()
                if (selection) {
                    navigator.clipboard.writeText(selection)
                }
                event.preventDefault()
                return false
            }

            return true
        })

        // Copy selection to system clipboard automatically (desktop only).
        // On mobile, selection tracking is handled via hasSelection ref + explicit copy button.
        terminal.onSelectionChange(() => {
            const selection = terminal.getSelection()

            if (!settingsStore.isTouchDevice) {
                // Desktop: immediate auto-copy
                if (selection) {
                    navigator.clipboard.writeText(selection)
                }
            } else {
                // Mobile: just track whether there is a selection (copy is manual via button)
                hasSelection.value = !!selection
            }
        })

        // Forward user input to the WebSocket
        terminal.onData((data) => {
            wsSend({ type: 'input', data })
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
        intentionalClose = true
        ws.close()
        ws = null
        isConnected.value = false
        terminal?.writeln('\x1b[31mTerminal disconnected.\x1b[0m')
    }

    /**
     * Copy the current terminal selection to clipboard, clear selection, and show a toast.
     * Used by the explicit copy button on mobile.
     */
    function copySelection() {
        if (!terminal) return
        const selection = terminal.getSelection()
        if (!selection) return
        clipboardWrite(selection)
        terminal.clearSelection()
        hasSelection.value = false
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
     * Handle a snippet button press. Sends the text as raw terminal input,
     * optionally appending a newline.
     */
    function handleSnippetPress(snippet) {
        let text = snippet.text
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
        // Extra keys bar
        activeModifiers, lockedModifiers,
        handleExtraKeyInput, handleExtraKeyModifierToggle, handleExtraKeyPaste,
        handleComboPress, handleSnippetPress,
    }
}

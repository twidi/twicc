// frontend/src/composables/useTerminal.js

import { ref, watch, onMounted, onUnmounted } from 'vue'
import { Terminal } from '@xterm/xterm'
import { FitAddon } from '@xterm/addon-fit'
import { WebLinksAddon } from '@xterm/addon-web-links'
// ClipboardAddon intentionally not loaded — Ctrl+V must reach the shell
// (e.g. bash quoted-insert). Paste is handled by right-click / middle-click.
import { useSettingsStore } from '../stores/settings'
import { useDataStore } from '../stores/data'
import { toast } from '../composables/useToast'
import '@xterm/xterm/css/xterm.css'

// ── Terminal themes ──────────────────────────────────────────────────────
// Background colors match the Monaco editor and --wa-color-surface-default.

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

    // ── tmux window management state ────────────────────────────────────
    const windows = ref([])
    const presets = ref([])
    /** Whether the active tmux pane is in alternate screen (less, vim, etc.) */
    const paneAlternate = ref(false)
    const showNavigator = ref(false)
    /** @type {((wins: Array) => void) | null} — resolver for pending listWindows() call */
    let windowsResolver = null

    // ── Touch mode (mobile): scroll (default) vs copy ─────────────────────
    const copyMode = ref(false)

    // ── Touch selection state (mobile) ─────────────────────────────────────
    let selectStartCol = 0
    let selectStartRow = 0
    /** @type {AbortController | null} */
    let touchAbortController = null

    // ── Mouse interception state (desktop, tmux mode) ─────────────────────
    /** @type {AbortController | null} */
    let mouseAbortController = null
    /** Whether a mouse-drag selection is in progress */
    let mouseIsSelecting = false
    /** Start coordinates for mouse selection (terminal cells + screen pixels) */
    let mouseStartCol = 0
    let mouseStartRow = 0
    let mouseStartX = 0
    let mouseStartY = 0
    /** Drag threshold in pixels before selection starts */
    const MOUSE_DRAG_THRESHOLD = 3

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
     * Sends ?tmux=1 only when tmux is applicable for the current session.
     */
    function getWsUrl() {
        const wsProtocol = location.protocol === 'https:' ? 'wss:' : 'ws:'
        const base = `${wsProtocol}//${location.host}/ws/terminal/${sessionId}/`
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
            // Fetch the window list so the tab bar / dropdown appears immediately
            if (shouldUseTmux()) {
                wsSend({ type: 'list_windows' })
            }
        }

        ws.onmessage = (event) => {
            const data = event.data
            // Detect JSON control messages from the server
            if (data.charAt(0) === '{') {
                try {
                    const msg = JSON.parse(data)
                    if (msg.type === 'windows') {
                        windows.value = msg.windows
                        if (msg.presets) presets.value = msg.presets
                        if ('alternate_on' in msg) paneAlternate.value = msg.alternate_on
                        if (windowsResolver) {
                            windowsResolver(msg.windows)
                            windowsResolver = null
                        }
                        return
                    }
                    if (msg.type === 'window_changed') {
                        // Update active flag in local list
                        for (const w of windows.value) {
                            w.active = (w.name === msg.name)
                        }
                        showNavigator.value = false
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
    /** Y coordinate at touch start — used to compute scroll delta in scroll mode. */
    let touchStartY = 0
    /** Accumulated fractional scroll lines (sub-line deltas carry over). */
    let scrollAccumulator = 0

    function onTouchStart(e) {
        // Ignore touches on the custom scrollbar — let xterm.js handle those via pointer events
        if (e.target?.closest('.scrollbar')) {
            touchIsSelecting = false
            return
        }
        const touch = e.touches[0]
        if (copyMode.value) {
            // Copy mode: start text selection
            touchIsSelecting = true
            const coords = screenToTerminalCoords(touch.clientX, touch.clientY)
            selectStartCol = coords.col
            selectStartRow = coords.row
            terminal?.clearSelection()
        } else {
            // Scroll mode: record start position
            touchIsSelecting = false
            touchStartY = touch.clientY
            scrollAccumulator = 0
        }
    }

    function onTouchMove(e) {
        if (!terminal) return
        const touch = e.touches[0]

        if (copyMode.value && touchIsSelecting) {
            // Copy mode: update text selection
            const coords = screenToTerminalCoords(touch.clientX, touch.clientY)
            const startOffset = selectStartRow * terminal.cols + selectStartCol
            const currentOffset = coords.row * terminal.cols + coords.col
            const length = currentOffset - startOffset

            if (length > 0) {
                terminal.select(selectStartCol, selectStartRow, length)
            } else if (length < 0) {
                terminal.select(coords.col, coords.row, -length)
            }
            e.preventDefault()
        } else if (!copyMode.value) {
            // Scroll mode: convert touch swipe into terminal scroll.
            // Uses "natural" scrolling: swipe up → see content below, swipe down → see history.
            const screenEl = terminal.element?.querySelector('.xterm-screen')
            if (!screenEl) return
            const deltaY = touchStartY - touch.clientY
            touchStartY = touch.clientY
            const rect = screenEl.getBoundingClientRect()
            const cellHeight = rect.height / terminal.rows

            scrollAccumulator += deltaY
            // Process one line per cell-height of accumulated movement
            while (Math.abs(scrollAccumulator) >= cellHeight) {
                const direction = scrollAccumulator > 0 ? 1 : -1

                if (paneAlternate.value) {
                    // Pane is in alternate screen (less, vim, htop, etc.):
                    // Send arrow keys — the app handles scrolling.
                    // Natural: swipe up (dir=1) → down arrow, swipe down (dir=-1) → up arrow
                    wsSend({ type: 'input', data: direction > 0 ? '\x1b[B' : '\x1b[A' })
                } else if (shouldUseTmux()) {
                    // Shell prompt inside tmux: send SGR mouse wheel sequences.
                    // tmux copy-mode handles the scrollback.
                    // Natural: swipe up (dir=1) → wheel down (65), swipe down (dir=-1) → wheel up (64)
                    const col = Math.max(1, Math.floor((touch.clientX - rect.left) / (rect.width / terminal.cols)) + 1)
                    const row = Math.max(1, Math.floor((touch.clientY - rect.top) / cellHeight) + 1)
                    const button = direction > 0 ? 65 : 64
                    wsSend({ type: 'input', data: `\x1b[<${button};${col};${row}M` })
                } else {
                    // Raw shell (no tmux): scroll xterm.js viewport buffer.
                    terminal.scrollLines(direction)
                }

                scrollAccumulator -= direction * cellHeight
            }
            e.preventDefault()
        }
    }

    /**
     * Attach touch event listeners for text selection on mobile.
     * Uses an AbortController for clean removal.
     */
    function attachTouchListeners() {
        if (!containerRef.value || !settingsStore.isTouchDevice) return

        touchAbortController = new AbortController()
        const signal = touchAbortController.signal

        containerRef.value.addEventListener('touchstart', onTouchStart, { passive: true, signal })
        containerRef.value.addEventListener('touchmove', onTouchMove, { passive: false, signal })
    }

    /**
     * Detach touch event listeners.
     */
    function detachTouchListeners() {
        if (touchAbortController) {
            touchAbortController.abort()
            touchAbortController = null
        }
    }

    // ── Mouse interception (desktop, tmux mode) ─────────────────────────
    //
    // When tmux enables SGR mouse tracking, xterm.js converts all mouse
    // events into escape sequences sent to the PTY instead of doing local
    // text selection. A single capture-phase mousedown listener blocks all
    // mouse buttons from reaching xterm.js (no SGR reports to tmux).
    //
    // - Left-button: preventDefault + custom selection via terminal.select()
    // - Middle/right-click: stopPropagation only — browser default actions
    //   (paste from X11 selection, context menu) flow through normally.
    //   xterm.js handles the resulting paste event on its internal textarea.
    // - Mouse wheel: NOT intercepted (tmux scrollback continues to work).

    /**
     * Select the word at the given terminal buffer coordinates.
     * Reads the buffer line and expands around the click position
     * using whitespace as word boundary.
     */
    function selectWordAt(col, row) {
        if (!terminal) return
        const line = terminal.buffer.active.getLine(row)
        if (!line) return

        let lineStr = ''
        for (let i = 0; i < terminal.cols; i++) {
            const cell = line.getCell(i)
            lineStr += cell ? cell.getChars() || ' ' : ' '
        }

        // Expand left
        let start = col
        while (start > 0 && !/\s/.test(lineStr[start - 1])) start--
        // Expand right
        let end = col
        while (end < terminal.cols - 1 && !/\s/.test(lineStr[end + 1])) end++

        const length = end - start + 1
        if (length > 0) {
            terminal.select(start, row, length)
        }
    }

    /**
     * Select the entire line at the given terminal buffer row.
     */
    function selectLineAt(row) {
        if (!terminal) return
        terminal.select(0, row, terminal.cols)
    }

    /**
     * Handle mousedown in capture phase on the terminal container.
     * Intercepts left-button clicks to implement local text selection
     * instead of letting xterm.js send SGR mouse reports to tmux.
     */
    function onMouseDownIntercept(e) {
        // Let Shift+click pass through — xterm.js handles it as forced selection
        if (e.shiftKey) return

        // Block all mouse buttons from reaching xterm.js (prevents SGR mouse
        // reports to tmux). Only preventDefault for left button — middle/right-click
        // default actions (paste / context menu) must flow through normally.
        e.stopPropagation()
        if (e.button !== 0) return
        e.preventDefault()

        // Focus the terminal (xterm.js normally does this in its own mousedown)
        terminal?.focus()

        const coords = screenToTerminalCoords(e.clientX, e.clientY)

        if (e.detail === 2) {
            // Double-click: select word
            selectWordAt(coords.col, coords.row)
            return
        }
        if (e.detail >= 3) {
            // Triple-click: select line
            selectLineAt(coords.row)
            return
        }

        // Single click: prepare for potential drag selection
        mouseIsSelecting = false
        mouseStartCol = coords.col
        mouseStartRow = coords.row
        mouseStartX = e.clientX
        mouseStartY = e.clientY

        terminal?.clearSelection()

        // Track drag on document so we capture moves outside the terminal area
        document.addEventListener('mousemove', onMouseMoveIntercept)
        document.addEventListener('mouseup', onMouseUpIntercept)
    }

    /**
     * Handle mousemove during a potential drag selection.
     * Added on document, active only between mousedown and mouseup.
     */
    function onMouseMoveIntercept(e) {
        if (!terminal) return

        // Apply drag threshold before starting selection
        if (!mouseIsSelecting) {
            const dx = e.clientX - mouseStartX
            const dy = e.clientY - mouseStartY
            if (Math.abs(dx) < MOUSE_DRAG_THRESHOLD && Math.abs(dy) < MOUSE_DRAG_THRESHOLD) {
                return
            }
            mouseIsSelecting = true
        }

        const coords = screenToTerminalCoords(e.clientX, e.clientY)
        const startOffset = mouseStartRow * terminal.cols + mouseStartCol
        const currentOffset = coords.row * terminal.cols + coords.col
        const length = currentOffset - startOffset

        if (length > 0) {
            terminal.select(mouseStartCol, mouseStartRow, length)
        } else if (length < 0) {
            terminal.select(coords.col, coords.row, -length)
        }
    }

    /**
     * Handle mouseup — finalize selection and clean up document listeners.
     * The existing onSelectionChange handler auto-copies to clipboard.
     */
    function onMouseUpIntercept() {
        document.removeEventListener('mousemove', onMouseMoveIntercept)
        document.removeEventListener('mouseup', onMouseUpIntercept)
        mouseIsSelecting = false
    }

    /**
     * Attach capture-phase mouse event listeners that intercept left-button
     * click/drag and right/middle-click when in tmux mode on desktop.
     * Mouse wheel events are NOT intercepted — they continue to flow to
     * xterm.js for tmux scrolling.
     */
    function attachMouseInterceptListeners() {
        if (!containerRef.value || settingsStore.isTouchDevice) return
        if (!shouldUseTmux()) return

        mouseAbortController = new AbortController()
        const signal = mouseAbortController.signal

        containerRef.value.addEventListener('mousedown', onMouseDownIntercept, { capture: true, signal })
    }

    /**
     * Detach mouse interception listeners.
     */
    function detachMouseInterceptListeners() {
        if (mouseAbortController) {
            mouseAbortController.abort()
            mouseAbortController = null
        }
        // Also clean up any lingering document-level drag listeners
        document.removeEventListener('mousemove', onMouseMoveIntercept)
        document.removeEventListener('mouseup', onMouseUpIntercept)
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
        // No ClipboardAddon — Ctrl+V goes to the shell, paste via right/middle-click

        terminal.open(containerRef.value)

        // Fit immediately
        fitAddon.fit()

        // Intercept Ctrl+Shift+C to copy selection instead of opening DevTools
        terminal.attachCustomKeyEventHandler((event) => {
            if (event.type === 'keydown' && event.ctrlKey && event.shiftKey && event.code === 'KeyC') {
                const selection = terminal.getSelection()
                if (selection) {
                    navigator.clipboard.writeText(selection)
                }
                event.preventDefault()
                return false
            }
            return true
        })

        // Copy selection to system clipboard automatically.
        // On desktop (mouse): copy immediately on selection change.
        // On mobile (touch): debounce 1s after last selection change, then copy + show toast.
        let selectionDebounceTimer = null
        terminal.onSelectionChange(() => {
            const selection = terminal.getSelection()
            if (!selection) return

            if (!settingsStore.isTouchDevice) {
                // Desktop: immediate copy
                navigator.clipboard.writeText(selection)
            } else {
                // Mobile: debounce — wait 0.5s after user stops adjusting selection
                clearTimeout(selectionDebounceTimer)
                selectionDebounceTimer = setTimeout(() => {
                    const finalSelection = terminal?.getSelection()
                    if (finalSelection) {
                        clipboardWrite(finalSelection)
                        toast.success('Copied to clipboard', { duration: 2000 })
                        // Auto-exit copy mode after successful copy
                        copyMode.value = false
                    }
                }, 500)
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

        // Attach mouse interception for tmux mode on desktop
        // (intercepts left-drag for selection, right/middle-click for paste)
        attachMouseInterceptListeners()

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
     * Send raw input data to the PTY (e.g. control characters).
     * @param {string} data
     */
    function sendInput(data) {
        wsSend({ type: 'input', data })
    }

    // ── tmux window control ─────────────────────────────────────────────

    /**
     * Request the list of tmux windows from the backend.
     * Returns a Promise that resolves with the window list.
     */
    function listWindows() {
        return new Promise((resolve) => {
            windowsResolver = resolve
            wsSend({ type: 'list_windows' })
            // Timeout fallback — resolve with current state after 3s
            setTimeout(() => {
                if (windowsResolver === resolve) {
                    windowsResolver = null
                    resolve(windows.value)
                }
            }, 3000)
        })
    }

    /**
     * Create a new tmux window.
     *
     * Accepts either a plain string (manual create) or a preset object
     * with {name, cwd?, command?} from .twicc-tmux.json presets.
     */
    function createWindow(nameOrPreset) {
        if (typeof nameOrPreset === 'string') {
            wsSend({ type: 'create_window', name: nameOrPreset })
        } else {
            const msg = { type: 'create_window', name: nameOrPreset.name }
            if (nameOrPreset.cwd) msg.preset_cwd = nameOrPreset.cwd
            if (nameOrPreset.command) msg.command = nameOrPreset.command
            wsSend(msg)
        }
    }

    /**
     * Switch to a tmux window by name.
     * The backend responds with window_changed, which hides the navigator.
     */
    function selectWindow(name) {
        wsSend({ type: 'select_window', name })
    }

    /**
     * Focus the xterm.js terminal (e.g. after selecting a window from a dropdown).
     */
    function focusTerminal() {
        terminal?.focus()
    }

    /**
     * Toggle the shell navigator visibility.
     */
    function toggleNavigator() {
        showNavigator.value = !showNavigator.value
    }

    /**
     * Clean up everything: terminal, WebSocket, observers.
     */
    function cleanup() {
        intentionalClose = true

        detachTouchListeners()
        detachMouseInterceptListeners()

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
        containerRef, isConnected, started, start, reconnect, sendInput, focusTerminal, copyMode,
        windows, presets, showNavigator, listWindows, createWindow, selectWindow, toggleNavigator,
    }
}

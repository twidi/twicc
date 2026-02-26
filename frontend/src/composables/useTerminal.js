// frontend/src/composables/useTerminal.js

import { ref, watch, onMounted, onUnmounted } from 'vue'
import { Terminal } from '@xterm/xterm'
import { FitAddon } from '@xterm/addon-fit'
import { WebLinksAddon } from '@xterm/addon-web-links'
import { ClipboardAddon } from '@xterm/addon-clipboard'
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

    // ── Touch selection state (mobile) ─────────────────────────────────────
    let selectStartCol = 0
    let selectStartRow = 0
    /** @type {AbortController | null} */
    let touchAbortController = null

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
        }

        ws.onmessage = (event) => {
            // Server sends raw PTY output as text
            terminal?.write(event.data)
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

    function onTouchStart(e) {
        // Ignore touches on the custom scrollbar — let xterm.js handle those via pointer events
        if (e.target?.closest('.scrollbar')) {
            touchIsSelecting = false
            return
        }
        touchIsSelecting = true
        const touch = e.touches[0]
        const coords = screenToTerminalCoords(touch.clientX, touch.clientY)
        selectStartCol = coords.col
        selectStartRow = coords.row
        terminal?.clearSelection()
    }

    function onTouchMove(e) {
        if (!touchIsSelecting || !terminal) return
        const touch = e.touches[0]
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
     * Clean up everything: terminal, WebSocket, observers.
     */
    function cleanup() {
        intentionalClose = true

        detachTouchListeners()

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

    return { containerRef, isConnected, started, start, reconnect }
}

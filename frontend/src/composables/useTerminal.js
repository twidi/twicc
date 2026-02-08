// frontend/src/composables/useTerminal.js

import { ref, watch, onMounted, onUnmounted } from 'vue'
import { Terminal } from '@xterm/xterm'
import { FitAddon } from '@xterm/addon-fit'
import { useSettingsStore } from '../stores/settings'
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

    /**
     * Build the WebSocket URL for the terminal endpoint.
     */
    function getWsUrl() {
        const wsProtocol = location.protocol === 'https:' ? 'wss:' : 'ws:'
        return `${wsProtocol}//${location.host}/ws/terminal/${sessionId}/`
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
            terminal?.writeln('\x1b[32mConnected.\x1b[0m')
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
        })

        fitAddon = new FitAddon()
        terminal.loadAddon(fitAddon)

        terminal.open(containerRef.value)

        // Fit immediately
        fitAddon.fit()

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

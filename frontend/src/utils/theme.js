// frontend/src/utils/theme.js
// Theme management utilities - extracted to avoid circular imports with main.js

const storedSettings = (() => {
    try {
        const raw = localStorage.getItem('twicc-settings')
        return raw ? JSON.parse(raw) : {}
    } catch { return {} }
})()

let currentColorScheme = storedSettings.colorScheme || storedSettings.themeMode || 'system'  // `themeMode` is legacy key

function applyColorScheme() {
    let isDark
    if (currentColorScheme === 'system') {
        isDark = window.matchMedia('(prefers-color-scheme: dark)').matches
    } else {
        isDark = currentColorScheme === 'dark'
    }
    document.documentElement.classList.toggle('wa-dark', isDark)
    document.documentElement.dataset.colorScheme = isDark ? 'dark' : 'light'
}

// The Web Awesome theme, palette and accent are fixed (not a user choice).
const WA_CLASSES = ['wa-theme-default', 'wa-palette-default', 'wa-brand-cyan']

function applyWaClasses() {
    document.documentElement.classList.add(...WA_CLASSES)
}

// ── Cached theme colors ─────────────────────────────────────────────────
// Recomputed when the color scheme changes.

let _cachedSurfaceColor = ''
let _cachedSelectionColor = ''

/**
 * Resolve a CSS color variable to an rgba() string via 1×1 canvas pixel readback.
 * Needed because modern browsers may return oklch/lab from getComputedStyle,
 * which xterm.js and CodeMirror can't parse.
 */
function resolveColorVariable(varExpr) {
    const el = document.createElement('div')
    el.style.color = varExpr
    document.body.appendChild(el)
    const computed = getComputedStyle(el).color
    el.remove()

    const canvas = document.createElement('canvas')
    canvas.width = canvas.height = 1
    const ctx = canvas.getContext('2d', { willReadFrequently: true })
    ctx.clearRect(0, 0, 1, 1)
    ctx.fillStyle = computed
    ctx.fillRect(0, 0, 1, 1)
    const [r, g, b, a] = ctx.getImageData(0, 0, 1, 1).data
    return `rgba(${r}, ${g}, ${b}, ${(a / 255).toFixed(3)})`
}

function recomputeCachedColors() {
    _cachedSurfaceColor = resolveColorVariable('var(--wa-color-surface-default)')
    _cachedSelectionColor = resolveColorVariable('var(--selection-bg-color)')
}

/** Cached surface background color (e.g. for terminal/editor backgrounds). */
export function getSurfaceColor() {
    return _cachedSurfaceColor
}

/** Cached selection background color (e.g. for terminal/editor selections). */
export function getSelectionColor() {
    return _cachedSelectionColor
}

// ── Public setters (invalidate cache after applying) ────────────────────

export function setColorScheme(mode) {
    currentColorScheme = mode
    applyColorScheme()
    recomputeCachedColors()
}

/**
 * Initialize theme on app startup.
 * Apply initial color scheme and WA classes, listen for system preference changes.
 */
export function initTheme() {
    applyColorScheme()
    applyWaClasses()
    recomputeCachedColors()
    document.documentElement.classList.remove('loading')
    window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => {
        applyColorScheme()
        recomputeCachedColors()
    })
}

// Minimal theme init for the share bundle: apply the default WA theme/brand and a
// viewer color scheme (persisted in the shared viewer-prefs JSON blob, defaulting
// to the OS preference). No dependency on the SPA settings store.
import { loadViewerPrefs, saveViewerPref } from './viewerPrefs'

export function getShareColorScheme() {
    return loadViewerPrefs().colorScheme || 'system'
}

export function applyShareColorScheme(mode) {
    const isDark = mode === 'dark' || (mode !== 'light' && matchMedia('(prefers-color-scheme: dark)').matches)
    document.documentElement.classList.toggle('wa-dark', isDark)
    document.documentElement.dataset.colorScheme = isDark ? 'dark' : 'light'
    saveViewerPref('colorScheme', mode)
}

// Viewer font size, mirroring the SPA: one value drives the root px (scaling the
// whole rem-based UI) AND the reused CodeMirror blocks (via getFontSize). Bounded
// to the SPA's 12–32 range; default 16.
export const SHARE_FONT_SIZE_MIN = 12
export const SHARE_FONT_SIZE_MAX = 32

export function getShareFontSize() {
    const n = Number(loadViewerPrefs().fontSize)
    return Number.isFinite(n) && n >= SHARE_FONT_SIZE_MIN && n <= SHARE_FONT_SIZE_MAX ? n : 16
}

export function applyShareFontSize(size) {
    document.documentElement.style.fontSize = `${size}px`
    saveViewerPref('fontSize', size)
}

export function initShareTheme() {
    document.documentElement.classList.add('wa-theme-default', 'wa-palette-default', 'wa-brand-cyan')
    applyShareColorScheme(getShareColorScheme())
    applyShareFontSize(getShareFontSize())
    matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => {
        if (getShareColorScheme() === 'system') applyShareColorScheme('system')
    })
}

// OS-only color scheme for the artifact shell. It has no options menu and no
// footer switcher — its only chrome is a footer strip, and the iframed artifact
// keeps its own internal theme the shell can't read (sandboxed) — so it simply
// follows the browser, never reading or writing the shared viewer-prefs the
// session/doc switcher use.
export function initArtifactShellColorScheme() {
    document.documentElement.classList.add('wa-theme-default', 'wa-palette-default', 'wa-brand-cyan')
    const mq = matchMedia('(prefers-color-scheme: dark)')
    const apply = () => {
        document.documentElement.classList.toggle('wa-dark', mq.matches)
        document.documentElement.dataset.colorScheme = mq.matches ? 'dark' : 'light'
    }
    apply()
    mq.addEventListener('change', apply)
}

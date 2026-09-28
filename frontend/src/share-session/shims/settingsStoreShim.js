// Viewer-local settings for the share bundle. Color scheme, timestamps and font
// size are the viewer's own choices, persisted across shares via the viewer-prefs
// JSON blob; display mode is transient (re-derived from the share's max each load).
// Cost is never shown in a share (areCostsShown stays false — the reused transcript
// components read it).
import { defineStore } from 'pinia'
import {
    applyShareColorScheme, getShareColorScheme,
    applyShareFontSize, getShareFontSize, SHARE_FONT_SIZE_MIN, SHARE_FONT_SIZE_MAX,
} from '../theme'
import { saveViewerPref } from '../viewerPrefs'

export const useSettingsStore = defineStore('shareSettings', {
    state: () => ({
        displayMode: 'normal',            // bounded to <= max_display_mode by the app
        _colorScheme: getShareColorScheme(),
        fontSize: getShareFontSize(),
        areMessageTimestampsShown: true,
        areCostsShown: false,
    }),
    getters: {
        getDisplayMode: (s) => s.displayMode,
        _effectiveColorScheme: (s) => (s._colorScheme === 'dark'
            || (s._colorScheme !== 'light' && matchMedia('(prefers-color-scheme: dark)').matches)) ? 'dark' : 'light',
        // Names the reused CodeMirror path reads (CodeEditor/DiffEditor/useCodeMirror):
        // without these the code/diff blocks render light regardless of the viewer's
        // dark toggle and the font size is undefined.
        getEffectiveColorScheme() { return this._effectiveColorScheme },
        getFontSize() { return this.fontSize },
        showDiffs: () => false,
        isMac: () => /Mac/i.test(navigator.platform || ''),
        isTouchDevice: () => matchMedia('(pointer: coarse)').matches,
        isToolDiffSideBySide: () => false,
        isToolDiffWordWrap: () => true,
        isClaudeHybridEnabled: () => false,
        isTitleGenerationEnabled: () => false,
        getTitleSystemPrompt: () => '',
    },
    actions: {
        setDisplayMode(mode) { this.displayMode = mode; document.body.dataset.displayMode = mode },
        setColorScheme(mode) { this._colorScheme = mode; applyShareColorScheme(mode) },
        // Viewer toggle — persisted so it carries across shares (unlike displayMode,
        // which is re-derived from the share's max on every load).
        setMessageTimestampsShown(shown) { this.areMessageTimestampsShown = shown; saveViewerPref('showTimestamps', shown) },
        // Font size mirrors the SPA: bounded, persisted, and applied to the root px
        // so it scales the whole viewer (the reused CodeMirror blocks follow via the
        // getFontSize watcher).
        setFontSize(size) {
            const clamped = Math.min(Math.max(Math.round(size), SHARE_FONT_SIZE_MIN), SHARE_FONT_SIZE_MAX)
            this.fontSize = clamped
            applyShareFontSize(clamped)
        },
        setToolDiffSideBySide() {}, setToolDiffWordWrap() {},
    },
})

// frontend/src/composables/useCodeMirror.js
// Foundation composable for CodeMirror 6 editors.
// Provides language resolution, theme/font extensions, and a reactive extension
// composable consumed by CodeEditor.vue and DiffEditor.vue.

import { watch } from 'vue'
import { Compartment, EditorState } from '@codemirror/state'
import { EditorView, keymap, lineNumbers, highlightActiveLine, highlightActiveLineGutter, dropCursor, rectangularSelection, crosshairCursor } from '@codemirror/view'
import { foldGutter, indentOnInput, bracketMatching, indentUnit } from '@codemirror/language'
import { closeBrackets, closeBracketsKeymap } from '@codemirror/autocomplete'
import { indentWithTab } from '@codemirror/commands'
import { search, searchKeymap, highlightSelectionMatches } from '@codemirror/search'
import { minimalSetup } from 'codemirror'
import { githubDark, githubLight } from '@uiw/codemirror-theme-github'
import { getLanguageFromPath } from '../utils/languages'

// ─── a) Language map ─────────────────────────────────────────────────────────

/**
 * Helper to create a loader for legacy CodeMirror modes via StreamLanguage.
 * @param {string} modPath     - Path segment under @codemirror/legacy-modes/mode/
 * @param {string} exportName  - Named export from the legacy mode module
 * @returns {() => Promise<import('@codemirror/language').StreamLanguage>}
 */
const legacyMode = (modPath, exportName) => async () => {
    const [mod, { StreamLanguage }] = await Promise.all([
        import(`@codemirror/legacy-modes/mode/${modPath}`),
        import('@codemirror/language'),
    ])
    return StreamLanguage.define(mod[exportName])
}

/**
 * Maps shiki language IDs to async loader functions that return a CM6
 * LanguageSupport or StreamLanguage instance.
 *
 * Languages intentionally not mapped (fall back to plain text):
 * graphql, json5, mdx, csharp, objective-c, objective-cpp, erb, fish, elixir,
 * zig, proto, terraform, makefile, cmake, nix, dart, asm, gitignore,
 * gitattributes, dotenv, ini.
 */
export const CM6_LANGUAGE_MAP = {
    // ── First-party Lezer packages ───────────────────────────────────────────
    javascript: async () => {
        const { javascript } = await import('@codemirror/lang-javascript')
        return javascript({ jsx: true })
    },
    jsx: async () => {
        const { javascript } = await import('@codemirror/lang-javascript')
        return javascript({ jsx: true })
    },
    typescript: async () => {
        const { javascript } = await import('@codemirror/lang-javascript')
        return javascript({ jsx: true, typescript: true })
    },
    tsx: async () => {
        const { javascript } = await import('@codemirror/lang-javascript')
        return javascript({ jsx: true, typescript: true })
    },
    python: async () => {
        const { python } = await import('@codemirror/lang-python')
        return python()
    },
    html: async () => {
        const { html } = await import('@codemirror/lang-html')
        return html()
    },
    vue: async () => {
        const { html } = await import('@codemirror/lang-html')
        return html()
    },
    svelte: async () => {
        const { html } = await import('@codemirror/lang-html')
        return html()
    },
    css: async () => {
        const { css } = await import('@codemirror/lang-css')
        return css()
    },
    json: async () => {
        const { json } = await import('@codemirror/lang-json')
        return json()
    },
    jsonc: async () => {
        const { json } = await import('@codemirror/lang-json')
        return json()
    },
    markdown: async () => {
        const { markdown } = await import('@codemirror/lang-markdown')
        return markdown()
    },
    rust: async () => {
        const { rust } = await import('@codemirror/lang-rust')
        return rust()
    },
    java: async () => {
        const { java } = await import('@codemirror/lang-java')
        return java()
    },
    cpp: async () => {
        const { cpp } = await import('@codemirror/lang-cpp')
        return cpp()
    },
    c: async () => {
        const { cpp } = await import('@codemirror/lang-cpp')
        return cpp()
    },
    php: async () => {
        const { php } = await import('@codemirror/lang-php')
        return php()
    },
    xml: async () => {
        const { xml } = await import('@codemirror/lang-xml')
        return xml()
    },
    sql: async () => {
        const { sql } = await import('@codemirror/lang-sql')
        return sql()
    },

    // ── Legacy modes via StreamLanguage ─────────────────────────────────────
    go: legacyMode('go', 'go'),
    yaml: legacyMode('yaml', 'yaml'),
    ruby: legacyMode('ruby', 'ruby'),
    bash: legacyMode('shell', 'shell'),
    zsh: legacyMode('shell', 'shell'),
    shell: legacyMode('shell', 'shell'),
    shellscript: legacyMode('shell', 'shell'),
    dockerfile: legacyMode('dockerfile', 'dockerFile'),
    lua: legacyMode('lua', 'lua'),
    kotlin: legacyMode('clike', 'kotlin'),
    swift: legacyMode('swift', 'swift'),
    scss: legacyMode('css', 'sCSS'),
    sass: legacyMode('sass', 'sass'),
    less: legacyMode('css', 'less'),
    perl: legacyMode('perl', 'perl'),
    r: legacyMode('r', 'r'),
    scala: legacyMode('clike', 'scala'),
    clojure: legacyMode('clojure', 'clojure'),
    haskell: legacyMode('haskell', 'haskell'),
    erlang: legacyMode('erlang', 'erlang'),
    toml: legacyMode('toml', 'toml'),
    diff: legacyMode('diff', 'diff'),
    powershell: legacyMode('powershell', 'powerShell'),
}

// ─── b) Language resolution with caching ────────────────────────────────────

/** Cache of in-flight or resolved language import Promises, keyed by language ID. */
const _languageCache = new Map()

/**
 * Resolve a CodeMirror language extension for a given file path or language override.
 *
 * Resolution order:
 * 1. Use `languageOverride` if provided (looked up in CM6_LANGUAGE_MAP)
 * 2. Otherwise detect language from `filePath` via `getLanguageFromPath`
 * 3. Load and return the LanguageSupport/StreamLanguage instance
 * 4. Falls back to null (plain text) when unknown or load fails
 *
 * The Promise itself is cached so concurrent calls for the same language ID
 * share a single import, avoiding duplicate module loading.
 *
 * @param {string|null} filePath          - File path to detect language from
 * @param {string|null} [languageOverride] - Explicit shiki language ID (optional)
 * @returns {Promise<import('@codemirror/language').LanguageSupport|null>}
 */
export async function resolveLanguage(filePath, languageOverride) {
    const langId = languageOverride ?? getLanguageFromPath(filePath)
    if (!langId) return null

    const loader = CM6_LANGUAGE_MAP[langId]
    if (!loader) return null

    if (!_languageCache.has(langId)) {
        _languageCache.set(langId, loader().catch(() => null))
    }

    return _languageCache.get(langId)
}

// ─── c) Theme extension ──────────────────────────────────────────────────────

/** Background color for the dark theme, matching the app's dark bg. */
const DARK_BG_COLOR = '#1b2733'

/**
 * Create a CodeMirror extension array for the given theme.
 * In dark mode, adds a background color override to match the app's dark bg.
 *
 * @param {boolean} isDark - True for dark theme, false for light
 * @returns {import('@codemirror/state').Extension[]}
 */
export function createThemeExtension(isDark) {
    if (!isDark) return [githubLight]

    const bgOverride = EditorView.theme({
        '&.cm-editor': { backgroundColor: DARK_BG_COLOR },
        '.cm-gutters': { backgroundColor: DARK_BG_COLOR },
    })

    return [githubDark, bgOverride]
}

// ─── d) Font size extension ──────────────────────────────────────────────────

/**
 * Create a CodeMirror extension that sets the editor font size.
 *
 * @param {number} fontSize - Font size in pixels
 * @returns {import('@codemirror/state').Extension}
 */
export function createFontSizeExtension(fontSize) {
    return EditorView.theme({
        '.cm-content': { fontSize: `${fontSize}px` },
        '.cm-gutters': { fontSize: `${fontSize}px` },
    })
}

// ─── e) useCodeMirrorExtensions composable ───────────────────────────────────

/**
 * Static extensions shared by all editor instances.
 * minimalSetup already includes: highlightSpecialChars, history, drawSelection,
 * syntaxHighlighting(defaultHighlightStyle, { fallback: true }), defaultKeymap, historyKeymap.
 * We do NOT duplicate those here.
 */
const STATIC_EXTENSIONS = [
    minimalSetup,
    dropCursor(),
    bracketMatching(),
    closeBrackets(),
    keymap.of(closeBracketsKeymap),
    keymap.of([indentWithTab]),
    keymap.of(searchKeymap),
    rectangularSelection(),
    crosshairCursor(),
    indentOnInput(),
    foldGutter(),
    search(),
    highlightSelectionMatches(),
    highlightActiveLineGutter(),
    EditorState.allowMultipleSelections.of(true),
]

// ─── e) Indent detection ────────────────────────────────────────────────────

/**
 * Detect the indentation style used in a document string.
 * Analyzes leading whitespace of indented lines and returns the most common pattern.
 *
 * @param {string} content - The document text
 * @returns {string} The indent unit string ("\t" for tabs, or N spaces). Defaults to 4 spaces.
 */
export function detectIndent(content) {
    if (!content) return '    '

    const lines = content.split('\n')
    let tabCount = 0
    let spaceLineCount = 0
    const diffs = {}  // { diffSize: occurrences }
    let prevIndent = 0

    for (const line of lines) {
        // Skip empty lines and lines with no content after whitespace
        const trimmed = line.trimStart()
        if (!trimmed) continue

        const leading = line.length - trimmed.length
        const char = leading > 0 ? line[0] : null

        if (char === '\t') {
            tabCount++
        } else if (char === ' ') {
            spaceLineCount++
            // Only count indentation increases (not dedents)
            const diff = leading - prevIndent
            if (diff >= 2 && diff <= 8) {
                diffs[diff] = (diffs[diff] || 0) + 1
            }
        }

        prevIndent = leading

        // Stop early once we have enough evidence
        if (tabCount >= 10 || spaceLineCount >= 10) break
    }

    // If tabs dominate, use tabs
    if (tabCount > spaceLineCount) return '\t'

    // Pick the most common indent difference
    let bestSize = 4
    let bestCount = 0
    for (const [size, count] of Object.entries(diffs)) {
        if (count > bestCount) {
            bestCount = count
            bestSize = Number(size)
        }
    }

    return ' '.repeat(bestSize)
}

/**
 * Reactive composable that manages CodeMirror 6 extension compartments.
 *
 * Returns the initial extension array (to pass to `new EditorView({ extensions })`)
 * and a `reconfigure` function to update individual compartments on a live view.
 *
 * Note: language, theme, and fontSize are NOT managed here.
 * - Language: set asynchronously by the caller via `resolveLanguage()` + `reconfigure(view, 'language', ...)`
 * - Theme / fontSize: watched and reconfigured automatically by `useSettingsWatcher()`, unless overridden
 *
 * @param {object} options - Reactive options (use `ref()` or `computed()` values)
 * @param {import('vue').Ref<boolean>}       [options.readOnly]    - Read-only mode (default: false)
 * @param {import('vue').Ref<boolean>}       [options.wordWrap]    - Enable word wrap (default: false)
 * @param {import('vue').Ref<boolean>}       [options.lineNumbers] - Show line numbers (default: true)
 *
 * @returns {{
 *   extensions: import('@codemirror/state').Extension[],
 *   reconfigure: (view: EditorView, option: string, value: any) => void,
 *   languageCompartment: Compartment,
 *   themeCompartment: Compartment,
 *   fontSizeCompartment: Compartment,
 *   readOnlyCompartment: Compartment,
 *   lineWrappingCompartment: Compartment,
 *   lineNumbersCompartment: Compartment,
 *   highlightActiveLineCompartment: Compartment,
 * }}
 */
export function useCodeMirrorExtensions(options, { initialTheme = false, initialFontSize = 16 } = {}) {
    // ── Compartments ─────────────────────────────────────────────────────────
    const languageCompartment = new Compartment()
    const themeCompartment = new Compartment()
    const fontSizeCompartment = new Compartment()
    const readOnlyCompartment = new Compartment()
    const lineWrappingCompartment = new Compartment()
    const lineNumbersCompartment = new Compartment()
    const highlightActiveLineCompartment = new Compartment()
    const indentUnitCompartment = new Compartment()

    // ── Accessors ─────────────────────────────────────────────────────────────
    const isReadOnly = () => !!options.readOnly?.value
    const isEditable = () => !isReadOnly()

    // ── Initial extension array ───────────────────────────────────────────────
    // Theme and font size use the caller-provided initial values so the editor
    // renders correctly on first paint, without waiting for useSettingsWatcher.
    const extensions = [
        ...STATIC_EXTENSIONS,
        languageCompartment.of([]),  // populated async after view creation
        themeCompartment.of(createThemeExtension(initialTheme === 'dark' || initialTheme === true)),
        fontSizeCompartment.of(createFontSizeExtension(initialFontSize)),
        readOnlyCompartment.of(isReadOnly()
            ? [EditorState.readOnly.of(true), EditorView.editable.of(false), EditorView.contentAttributes.of({ tabindex: '0' })]
            : [EditorState.readOnly.of(false), EditorView.editable.of(true)]
        ),
        lineWrappingCompartment.of(options.wordWrap?.value ? EditorView.lineWrapping : []),
        lineNumbersCompartment.of(options.lineNumbers?.value !== false ? lineNumbers() : []),
        highlightActiveLineCompartment.of(isEditable() ? highlightActiveLine() : []),
        indentUnitCompartment.of(indentUnit.of('    ')),  // default 4 spaces, reconfigured per-file
    ]

    // ── reconfigure ───────────────────────────────────────────────────────────

    /**
     * Reconfigure one compartment on a live EditorView.
     *
     * @param {EditorView} view    - The EditorView instance to update
     * @param {string}     option  - One of: 'language' | 'theme' | 'fontSize' |
     *                               'readOnly' | 'wordWrap' | 'lineNumbers' |
     *                               'highlightActiveLine'
     * @param {*}          value   - New value for the option
     */
    function reconfigure(view, option, value) {
        if (!view) return

        const dispatch = (compartment, extension) => {
            view.dispatch({ effects: compartment.reconfigure(extension) })
        }

        switch (option) {
            case 'language':
                dispatch(languageCompartment, value ?? [])
                break
            case 'theme':
                dispatch(themeCompartment, createThemeExtension(value === 'dark'))
                break
            case 'fontSize':
                dispatch(fontSizeCompartment, createFontSizeExtension(value))
                break
            case 'readOnly': {
                const ro = !!value
                dispatch(readOnlyCompartment, ro
                    ? [EditorState.readOnly.of(true), EditorView.editable.of(false), EditorView.contentAttributes.of({ tabindex: '0' })]
                    : [EditorState.readOnly.of(false), EditorView.editable.of(true)]
                )
                dispatch(highlightActiveLineCompartment, ro ? [] : highlightActiveLine())
                break
            }
            case 'wordWrap':
                dispatch(lineWrappingCompartment, value ? EditorView.lineWrapping : [])
                break
            case 'lineNumbers':
                dispatch(lineNumbersCompartment, value ? lineNumbers() : [])
                break
            case 'highlightActiveLine':
                dispatch(highlightActiveLineCompartment, value ? highlightActiveLine() : [])
                break
            case 'indentUnit':
                dispatch(indentUnitCompartment, indentUnit.of(value || '    '))
                break
        }
    }

    return {
        extensions,
        reconfigure,
        languageCompartment,
        themeCompartment,
        fontSizeCompartment,
        readOnlyCompartment,
        lineWrappingCompartment,
        lineNumbersCompartment,
        highlightActiveLineCompartment,
        indentUnitCompartment,
    }
}

// ─── Settings store watchers ─────────────────────────────────────────────────

/**
 * Set up watchers on the settings store that automatically reconfigure a
 * CodeMirror view when the global theme or font size changes.
 *
 * Call this inside a component's onMounted() or setup() after the EditorView
 * is available. Pass a getter so the view reference can be resolved lazily
 * (the view may not exist at the moment watchers are registered).
 *
 * The settings store is imported lazily to prevent a circular import between
 * this composable and stores/settings.js.
 *
 * @param {() => EditorView|null} getView     - Returns the current EditorView or null
 * @param {ReturnType<typeof useCodeMirrorExtensions>} cmExtensions - Result of useCodeMirrorExtensions()
 * @param {object} [overrides]                - Skip store watch if component overrides the value
 * @param {import('vue').Ref|null} [overrides.theme]    - Per-component theme ref (skip store watch)
 * @param {import('vue').Ref|null} [overrides.fontSize] - Per-component fontSize ref (skip store watch)
 */
export function useSettingsWatcher(getView, cmExtensions, overrides = {}) {
    // Lazy import avoids a static circular dependency:
    //   useCodeMirror.js → stores/settings.js  (fine, no back-edge)
    // But if settings.js ever imports from this file, we'd get a cycle.
    // Using async import() here ensures no module-level static edge.
    const stops = []
    let cancelled = false
    ;(async () => {
        const { useSettingsStore } = await import('../stores/settings')
        if (cancelled) return  // stop() was called before the import resolved

        const settingsStore = useSettingsStore()

        if (!overrides.theme) {
            stops.push(watch(
                () => settingsStore.getEffectiveTheme,
                (theme) => {
                    const view = getView()
                    if (view) cmExtensions.reconfigure(view, 'theme', theme)
                },
                { immediate: true }
            ))
        }

        if (!overrides.fontSize) {
            stops.push(watch(
                () => settingsStore.getFontSize,
                (fontSize) => {
                    const view = getView()
                    if (view) cmExtensions.reconfigure(view, 'fontSize', fontSize)
                },
                { immediate: true }
            ))
        }
    })()
    return () => {
        cancelled = true
        stops.forEach(stop => stop())
    }
}

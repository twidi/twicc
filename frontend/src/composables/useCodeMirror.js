import { i18n } from '../i18n/index.js'
// frontend/src/composables/useCodeMirror.js
// Foundation composable for CodeMirror 6 editors.
// Provides language resolution, theme/font extensions, and a reactive extension
// composable consumed by CodeEditor.vue and DiffEditor.vue.

import { watch } from 'vue'
import { Compartment, EditorState } from '@codemirror/state'
import { EditorView, keymap, lineNumbers, highlightActiveLine, highlightActiveLineGutter, dropCursor, rectangularSelection, crosshairCursor } from '@codemirror/view'
import { codeFolding, foldEffect, foldedRanges, unfoldEffect, foldGutter, indentOnInput, bracketMatching, indentUnit } from '@codemirror/language'
import { closeBrackets, closeBracketsKeymap } from '@codemirror/autocomplete'
import { indentWithTab } from '@codemirror/commands'
import { search, searchKeymap, highlightSelectionMatches, openSearchPanel, closeSearchPanel, searchPanelOpen, getSearchQuery, setSearchQuery } from '@codemirror/search'
import { minimalSetup } from 'codemirror'
import { githubDarkInit, githubLightInit } from '@uiw/codemirror-theme-github'
import { getLanguageFromPath } from '../utils/languages'
import { getSurfaceColor, getSelectionColor } from '../utils/theme'

const EDITOR_PHRASE_KEYS = {
    "Find": "session.editor.phrases.find",
    "Replace": "session.editor.phrases.replaceInput",
    "next": "session.editor.phrases.next",
    "previous": "session.editor.phrases.previous",
    "all": "session.editor.phrases.all",
    "match case": "session.editor.phrases.matchCase",
    "regexp": "session.editor.phrases.regexp",
    "by word": "session.editor.phrases.byWord",
    "replace": "session.editor.phrases.replace",
    "replace all": "session.editor.phrases.replaceAll",
    "close": "session.editor.phrases.close",
    "Go to line": "session.editor.phrases.goToLine",
    "go": "session.editor.phrases.go",
    "Control character": "session.editor.phrases.controlCharacter",
    "Selection deleted": "session.editor.phrases.selectionDeleted",
    "Folded lines": "session.editor.phrases.foldedLines",
    "Unfolded lines": "session.editor.phrases.unfoldedLines",
    "to": "session.editor.phrases.to",
    "folded code": "session.editor.phrases.foldedCode",
    "unfold": "session.editor.phrases.unfold",
    "Fold line": "session.editor.phrases.foldLine",
    "Unfold line": "session.editor.phrases.unfoldLine",
    "Completions": "session.editor.phrases.completions",
    "replaced match on line $": "session.editor.phrases.replacedLine",
    "replaced $ matches": "session.editor.phrases.replacedMatches",
    "current match": "session.editor.phrases.currentMatch",
    "on line": "session.editor.phrases.onLine"
}

function editorPhrases() {
    return EditorState.phrases.of(Object.fromEntries(Object.entries(EDITOR_PHRASE_KEYS)
        .map(([phrase, key]) => [phrase, i18n.global.t(key)])))
}

// ─── a) Language map ─────────────────────────────────────────────────────────

/**
 * Helper to create a loader for legacy CodeMirror modes via StreamLanguage.
 * Each loader uses a literal import so Vite can include the mode in the build.
 * @param {() => Promise<object>} loadMode - Legacy mode module loader
 * @param {string} exportName  - Named export from the legacy mode module
 * @returns {() => Promise<import('@codemirror/language').StreamLanguage>}
 */
const legacyMode = (loadMode, exportName) => async () => {
    const [mod, { StreamLanguage }] = await Promise.all([
        loadMode(),
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
    go: legacyMode(() => import('@codemirror/legacy-modes/mode/go'), 'go'),
    yaml: legacyMode(() => import('@codemirror/legacy-modes/mode/yaml'), 'yaml'),
    ruby: legacyMode(() => import('@codemirror/legacy-modes/mode/ruby'), 'ruby'),
    bash: legacyMode(() => import('@codemirror/legacy-modes/mode/shell'), 'shell'),
    zsh: legacyMode(() => import('@codemirror/legacy-modes/mode/shell'), 'shell'),
    shell: legacyMode(() => import('@codemirror/legacy-modes/mode/shell'), 'shell'),
    shellscript: legacyMode(() => import('@codemirror/legacy-modes/mode/shell'), 'shell'),
    dockerfile: legacyMode(() => import('@codemirror/legacy-modes/mode/dockerfile'), 'dockerFile'),
    lua: legacyMode(() => import('@codemirror/legacy-modes/mode/lua'), 'lua'),
    kotlin: legacyMode(() => import('@codemirror/legacy-modes/mode/clike'), 'kotlin'),
    swift: legacyMode(() => import('@codemirror/legacy-modes/mode/swift'), 'swift'),
    scss: legacyMode(() => import('@codemirror/legacy-modes/mode/css'), 'sCSS'),
    sass: legacyMode(() => import('@codemirror/legacy-modes/mode/sass'), 'sass'),
    less: legacyMode(() => import('@codemirror/legacy-modes/mode/css'), 'less'),
    perl: legacyMode(() => import('@codemirror/legacy-modes/mode/perl'), 'perl'),
    r: legacyMode(() => import('@codemirror/legacy-modes/mode/r'), 'r'),
    scala: legacyMode(() => import('@codemirror/legacy-modes/mode/clike'), 'scala'),
    clojure: legacyMode(() => import('@codemirror/legacy-modes/mode/clojure'), 'clojure'),
    haskell: legacyMode(() => import('@codemirror/legacy-modes/mode/haskell'), 'haskell'),
    erlang: legacyMode(() => import('@codemirror/legacy-modes/mode/erlang'), 'erlang'),
    toml: legacyMode(() => import('@codemirror/legacy-modes/mode/toml'), 'toml'),
    diff: legacyMode(() => import('@codemirror/legacy-modes/mode/diff'), 'diff'),
    powershell: legacyMode(() => import('@codemirror/legacy-modes/mode/powershell'), 'powerShell'),
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

/**
 * Create a CodeMirror extension array for the given theme.
 * In dark mode, adds a background color override to match the app's surface color.
 *
 * @param {boolean} isDark - True for dark theme, false for light
 * @returns {import('@codemirror/state').Extension[]}
 */
export function createThemeExtension(isDark) {
    const selBg = getSelectionColor()

    if (!isDark) return [githubLightInit({ settings: { selection: selBg, selectionMatch: selBg } })]

    const bg = getSurfaceColor()
    const bgOverride = EditorView.theme({
        '&.cm-editor': { backgroundColor: bg },
        '.cm-gutters': { backgroundColor: bg },
    })

    return [githubDarkInit({ settings: { selection: selBg, selectionMatch: selBg } }), bgOverride]
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
    search(),
    highlightSelectionMatches(),
    highlightActiveLineGutter(),
    EditorState.allowMultipleSelections.of(true),
]

// ─── e) Search panel toggle ──────────────────────────────────────────────────

/**
 * Toggle the CodeMirror search panel open/closed.
 *
 * The panel adapts automatically to the editor state: in read-only mode it
 * shows search only; in editable mode it includes search + replace fields.
 *
 * @param {EditorView} view - The CodeMirror EditorView instance
 */
export function toggleSearchPanel(view) {
    if (!view) return
    if (searchPanelOpen(view.state)) closeSearchPanel(view)
    else openSearchPanel(view)
}

/**
 * Toggle the search panel of the CodeMirror editor currently holding focus.
 * Resolves the editor via `document.activeElement.closest('.cm-editor')`.
 *
 * Used by the global Ctrl+F handler so a second press can close the panel
 * and fall through to the browser's native Find bar.
 *
 * @returns {'opened'|'closed'|null} 'opened' / 'closed' after the toggle, or null when no CodeMirror is focused.
 */
export function toggleSearchInActiveCodeMirror() {
    const el = document.activeElement?.closest?.('.cm-editor')
    if (!el) return null
    const view = EditorView.findFromDOM(el)
    if (!view) return null
    if (searchPanelOpen(view.state)) {
        closeSearchPanel(view)
        return 'closed'
    }
    openSearchPanel(view)
    return 'opened'
}

// ─── f) Indent detection ────────────────────────────────────────────────────

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
    const localeCompartment = new Compartment()
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
        localeCompartment.of([editorPhrases(), foldGutter(), codeFolding({
            // A new phrase facet value gives folded placeholders a new widget identity.
            preparePlaceholder: state => state.facet(EditorState.phrases),
        })]),
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
            case 'locale': {
                const folds = []
                foldedRanges(view.state).between(0, view.state.doc.length, (from, to) => folds.push({ from, to }))
                view.dispatch({ effects: [
                    localeCompartment.reconfigure([editorPhrases(), foldGutter(), codeFolding({
                        preparePlaceholder: state => state.facet(EditorState.phrases),
                    })]),
                    // Recreate only folding widgets. The document, history and selection stay in state.
                    ...folds.map(range => unfoldEffect.of(range)),
                    ...folds.map(range => foldEffect.of(range)),
                ] })
                break
            }
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
    const stops = [watch(i18n.global.locale, () => {
        const view = getView()
        if (!view) return
        const previousControlPhrase = view.state.phrase('Control character')
        const searchWasOpen = searchPanelOpen(view.state)
        const focused = view.dom.ownerDocument.activeElement
        const searchQuery = searchWasOpen ? getSearchQuery(view.state) : null
        const searchInput = searchWasOpen && focused?.closest?.('.cm-search')
            ? { name: focused.name, from: focused.selectionStart, to: focused.selectionEnd, direction: focused.selectionDirection } : null
        cmExtensions.reconfigure(view, 'locale')
        // These native controls create DOM only when they first open.
        const lineDialog = view.dom.querySelector('.cm-goto-line')
        if (lineDialog) {
            const label = lineDialog.querySelector('label')
            if (label?.firstChild?.nodeType === 3) label.firstChild.nodeValue = `${view.state.phrase('Go to line')}: `
            const submit = lineDialog.querySelector('button[type=submit]')
            if (submit) submit.textContent = view.state.phrase('go')
            lineDialog.querySelector('.cm-dialog-close')?.setAttribute('aria-label', view.state.phrase('close'))
        }
        for (const list of view.dom.querySelectorAll('.cm-tooltip-autocomplete [role=listbox]')) {
            list.setAttribute('aria-label', view.state.phrase('Completions'))
        }
        for (const marker of view.dom.querySelectorAll('.cm-specialChar')) {
            const suffix = marker.getAttribute('aria-label')?.slice(previousControlPhrase.length) || ''
            const description = view.state.phrase('Control character') + suffix
            marker.setAttribute('aria-label', description)
            marker.title = description
        }
        // CodeMirror builds its search labels only when the panel opens.
        // The query lives in editor state, so reopening preserves it.
        if (searchWasOpen) {
            closeSearchPanel(view)
            openSearchPanel(view)
            view.dispatch({ effects: setSearchQuery.of(searchQuery) })
            const target = searchInput ? [...view.dom.querySelectorAll('.cm-search input')]
                .find(input => input.name === searchInput.name) : focused
            if (target?.isConnected) {
                target.focus({ preventScroll: true })
                if (searchInput && typeof searchInput.from === 'number') {
                    target.setSelectionRange(searchInput.from, searchInput.to, searchInput.direction)
                }
            }
        }
    })]
    let cancelled = false
    ;(async () => {
        const { useSettingsStore } = await import('../stores/settings')
        if (cancelled) return  // stop() was called before the import resolved

        const settingsStore = useSettingsStore()

        if (!overrides.theme) {
            stops.push(watch(
                () => settingsStore.getEffectiveColorScheme,
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

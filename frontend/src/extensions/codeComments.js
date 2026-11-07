// frontend/src/extensions/codeComments.js
// CodeMirror 6 extension for inline code comments.
// Renders a textarea widget below annotated lines. Communicates with the
// host component via callbacks (no Vue/Pinia dependency).

import { watch } from 'vue'
import { i18n } from '../i18n/index.js'
const { t } = i18n.global

import { StateField, StateEffect } from '@codemirror/state'
import { EditorView, Decoration, WidgetType, ViewPlugin } from '@codemirror/view'

// ─── Effects ────────────────────────────────────────────────────────────────

/**
 * Sync all comments for this editor from the store.
 * Value: Array<{ lineNumber: number, content: string, lineText: string }>
 * Dispatched by the host component when the store changes (e.g. after hydration).
 */
export const syncCommentsEffect = StateEffect.define()

/** Add a new comment widget at a line. Value: { lineNumber: number, lineText: string } */
const addCommentEffect = StateEffect.define()

/** Remove a comment widget at a line. Value: { lineNumber: number } */
const removeCommentEffect = StateEffect.define()

// ─── Widget ─────────────────────────────────────────────────────────────────

class CodeCommentWidget extends WidgetType {
    constructor(lineNumber, content, callbacks) {
        super()
        this.lineNumber = lineNumber
        this.content = content
        this.callbacks = callbacks
    }

    toDOM(view) {
        // Read content from the store via callback (fresh) instead of this.content
        // (potentially stale). this.content is only updated when syncCommentsEffect
        // fires, but the Vue watcher that dispatches it doesn't track content changes
        // (only identity fields). Using getContent ensures correct content after
        // scrolling a widget out of the viewport and back in.
        const content = this.callbacks.getContent
            ? this.callbacks.getContent(this.lineNumber)
            : this.content

        const wrap = document.createElement('div')
        wrap.className = 'cm-code-comment-wrap'

        const textarea = document.createElement('textarea')
        textarea.className = 'cm-code-comment-textarea'
        textarea.value = content
        textarea.rows = 3
        textarea.placeholder = t('code.comments.placeholder')

        // Track buttons that depend on textarea content
        const contentDependentBtns = []

        /** Update disabled state of content-dependent buttons. */
        const updateBtnState = () => {
            const hasContent = textarea.value.trim().length > 0
            for (const btn of contentDependentBtns) btn.disabled = !hasContent
        }

        // Notify on input — count sync is handled externally by the Vue
        // watcher in CodeEditor/DiffEditor which dispatches a custom event.
        textarea.addEventListener('input', () => {
            this.callbacks.onUpdate(this.lineNumber, textarea.value)
            updateBtnState()
        })

        // Ctrl/Cmd+Enter adds to the message input (same as the "Add to message"
        // button), and Escape closes the widget.
        textarea.addEventListener('keydown', (e) => {
            if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') {
                // Mirror the "Add to message" button: only act when there is
                // content (the button is disabled while the textarea is empty).
                if (this.callbacks.onAddToMessage && textarea.value.trim().length > 0) {
                    e.preventDefault()
                    e.stopPropagation()
                    view.dispatch({ effects: removeCommentEffect.of({ lineNumber: this.lineNumber }) })
                    this.callbacks.onAddToMessage(this.lineNumber)
                }
                return
            }
            if (e.key === 'Escape') {
                e.stopPropagation()
                view.dispatch({ effects: removeCommentEffect.of({ lineNumber: this.lineNumber }) })
                this.callbacks.onRemove(this.lineNumber)
                view.focus()
            }
        })

        const actions = document.createElement('div')
        actions.className = 'cm-code-comment-actions'

        const cancelBtn = document.createElement('button')
        cancelBtn.className = 'cm-code-comment-cancel'
        cancelBtn.textContent = t('common.actions.cancel')
        cancelBtn.type = 'button'
        cancelBtn.addEventListener('click', (e) => {
            e.stopPropagation()
            view.dispatch({ effects: removeCommentEffect.of({ lineNumber: this.lineNumber }) })
            this.callbacks.onRemove(this.lineNumber)
            view.focus()
        })

        actions.appendChild(cancelBtn)

        if (this.callbacks.onAddToMessage) {
            const addBtn = document.createElement('button')
            addBtn.className = 'cm-code-comment-add-to-msg'
            addBtn.textContent = t('code.comments.addToMessage')
            addBtn.type = 'button'
            addBtn.addEventListener('click', (e) => {
                e.stopPropagation()
                view.dispatch({ effects: removeCommentEffect.of({ lineNumber: this.lineNumber }) })
                this.callbacks.onAddToMessage(this.lineNumber)
            })
            contentDependentBtns.push(addBtn)
            actions.appendChild(addBtn)
        }

        // "Add all" button — visibility managed via a 'code-comment-count-changed'
        // custom event dispatched by the Vue watcher in CodeEditor/DiffEditor.
        // The watcher reacts to Pinia store changes (session-wide, all sources)
        // and bridges them to CM6 widgets via this DOM event.
        if (this.callbacks.onAddAllToMessage) {
            const addAllBtn = document.createElement('button')
            addAllBtn.className = 'cm-code-comment-add-all-to-msg'
            addAllBtn.type = 'button'
            addAllBtn.addEventListener('click', (e) => {
                e.stopPropagation()
                view.dispatch({ effects: syncCommentsEffect.of([]) })
                this.callbacks.onAddAllToMessage()
            })

            /** Show/hide the button based on count. */
            const updateAddAll = (count) => {
                if (count >= 2) {
                    addAllBtn.style.display = ''
                    addAllBtn.dataset.count = count
                    addAllBtn.textContent = t('code.comments.addAll', { count }, count)
                } else {
                    addAllBtn.style.display = 'none'
                }
            }

            // Set initial state from store
            const initialCount = this.callbacks.getSessionCommentCount
                ? this.callbacks.getSessionCommentCount() : 0
            updateAddAll(initialCount)

            // Listen for count changes broadcast by the Vue watcher
            view.dom.addEventListener('code-comment-count-changed', (e) => {
                updateAddAll(e.detail.count)
            })

            actions.appendChild(addAllBtn)
        }

        wrap.appendChild(textarea)

        // Dismissible help text (persisted in localStorage)
        const HELP_DISMISSED_KEY = 'twicc-code-comments-help-dismissed'
        if (!localStorage.getItem(HELP_DISMISSED_KEY)) {
            const helpWrap = document.createElement('div')
            helpWrap.className = 'cm-code-comment-help'

            const helpSpan = document.createElement('span')
            helpSpan.textContent = t('code.comments.help')

            const dismissLink = document.createElement('a')
            dismissLink.className = 'cm-code-comment-help-dismiss'
            dismissLink.textContent = t('code.comments.dismissHelp')
            dismissLink.href = '#'
            dismissLink.addEventListener('click', (e) => {
                e.preventDefault()
                e.stopPropagation()
                localStorage.setItem(HELP_DISMISSED_KEY, '1')
                helpWrap.remove()
            })

            helpWrap.appendChild(helpSpan)
            helpWrap.appendChild(document.createTextNode(' '))
            helpWrap.appendChild(dismissLink)
            wrap.appendChild(helpWrap)
        }

        wrap.appendChild(actions)
        // Keep widget text current without replacing the textarea or its selection.
        wrap._ccStopLocale = watch(i18n.global.locale, () => {
            textarea.placeholder = t('code.comments.placeholder')
            cancelBtn.textContent = t('common.actions.cancel')
            const add = wrap.querySelector('.cm-code-comment-add-to-msg')
            if (add) add.textContent = t('code.comments.addToMessage')
            const all = wrap.querySelector('.cm-code-comment-add-all-to-msg')
            if (all?.dataset.count) {
                const count = Number(all.dataset.count)
                all.textContent = t('code.comments.addAll', { count }, count)
            }
            const help = wrap.querySelector('.cm-code-comment-help > span')
            if (help) help.textContent = t('code.comments.help')
            const dismiss = wrap.querySelector('.cm-code-comment-help-dismiss')
            if (dismiss) dismiss.textContent = t('code.comments.dismissHelp')
        })

        // Set initial disabled state for content-dependent buttons
        updateBtnState()

        // Auto-focus only for newly added comments (empty content), not restored ones
        if (!content) {
            requestAnimationFrame(() => textarea.focus())
        }

        return wrap
    }

    destroy(dom) {
        dom._ccStopLocale?.()
    }

    eq(other) {
        return this.lineNumber === other.lineNumber && this.content === other.content
    }

    get estimatedHeight() {
        return 100
    }

    ignoreEvent() {
        return true
    }
}

// ─── State field ────────────────────────────────────────────────────────────

/**
 * Build the DecorationSet from a line→content map.
 */
function buildDecorations(commentsMap, callbacks, state) {
    const decorations = []
    for (const [lineNumber, data] of commentsMap) {
        if (lineNumber < 1 || lineNumber > state.doc.lines) continue
        const line = state.doc.line(lineNumber)
        decorations.push(
            Decoration.widget({
                widget: new CodeCommentWidget(lineNumber, data.content, callbacks),
                block: true,
                side: 1,
            }).range(line.to)
        )
    }
    decorations.sort((a, b) => a.from - b.from)
    return Decoration.set(decorations)
}

function createField(callbacks, initialComments) {
    return StateField.define({
        create(state) {
            const map = new Map()
            for (const { lineNumber, content, lineText } of initialComments) {
                map.set(lineNumber, { content, lineText: lineText || '' })
            }
            return {
                commentsMap: map,
                deco: buildDecorations(map, callbacks, state),
            }
        },

        update(fieldState, tr) {
            let { commentsMap, deco } = fieldState
            let changed = false

            for (const effect of tr.effects) {
                if (effect.is(syncCommentsEffect)) {
                    commentsMap = new Map()
                    for (const { lineNumber, content, lineText } of effect.value) {
                        commentsMap.set(lineNumber, { content, lineText: lineText || '' })
                    }
                    changed = true
                } else if (effect.is(addCommentEffect)) {
                    const { lineNumber, lineText } = effect.value
                    if (!commentsMap.has(lineNumber)) {
                        commentsMap = new Map(commentsMap)
                        commentsMap.set(lineNumber, { content: '', lineText: lineText || '' })
                        changed = true
                    }
                } else if (effect.is(removeCommentEffect)) {
                    const { lineNumber } = effect.value
                    if (commentsMap.has(lineNumber)) {
                        commentsMap = new Map(commentsMap)
                        commentsMap.delete(lineNumber)
                        changed = true
                    }
                }
            }

            if (changed) {
                return {
                    commentsMap,
                    deco: buildDecorations(commentsMap, callbacks, tr.state),
                }
            }

            if (tr.docChanged) {
                return {
                    commentsMap,
                    deco: buildDecorations(commentsMap, callbacks, tr.state),
                }
            }

            return fieldState
        },

        provide(field) {
            return EditorView.decorations.from(field, (val) => val.deco)
        },
    })
}

// ─── Hover "+" button ───────────────────────────────────────────────────────
// A single "+" button is moved between .cm-gutterElement nodes on hover.
// Placed INSIDE the gutter cell (position: absolute, inset: 0), it scrolls
// naturally, needs no coordinate math, and covers the line number text.

function createHoverButtonPlugin(callbacks, field) {
    return ViewPlugin.fromClass(class {
        constructor(view) {
            this.view = view
            this.currentLine = -1

            // Single + button (wa-button + wa-icon), moved between gutter elements
            this.btn = document.createElement('wa-button')
            this.btn.className = 'cm-code-comment-add-btn'
            this.btn.setAttribute('variant', 'brand')
            this.btn.setAttribute('appearance', 'filled-outlined')
            this.btn.setAttribute('size', 'small')
            const icon = document.createElement('wa-icon')
            icon.setAttribute('name', 'comment')
            icon.setAttribute('variant', 'regular')
            icon.className = 'cm-code-comment-add-btn-icon'
            this.btn.appendChild(icon)
            this.stopLocale = watch(i18n.global.locale, () => {
                this.btn.title = t('code.comments.add')
                this.btn.setAttribute('aria-label', this.btn.title)
            }, { immediate: true })

            this.handleMouseMove = this.handleMouseMove.bind(this)
            this.handleMouseLeave = this.handleMouseLeave.bind(this)
            this.handleClick = this.handleClick.bind(this)

            view.dom.addEventListener('mousemove', this.handleMouseMove)
            view.dom.addEventListener('mouseleave', this.handleMouseLeave)
            this.btn.addEventListener('click', this.handleClick)
        }

        /** Find the gutter cell that vertically aligns with a given doc line. */
        _gutterElForLine(line) {
            const gutter = this.view.dom.querySelector('.cm-lineNumbers')
            if (!gutter) return null
            // Use coordsAtPos to get the actual text line coords. lineBlockAt
            // includes block widgets attached before the line (e.g. deletion
            // widgets in unified diff view), which would shift y above the
            // visible text line and miss its gutter cell.
            const coords = this.view.coordsAtPos(line.from)
            if (!coords) return null
            const y = (coords.top + coords.bottom) / 2
            for (const cell of gutter.children) {
                const r = cell.getBoundingClientRect()
                if (r.height > 0 && r.top <= y && y < r.bottom) return cell
            }
            return null
        }

        /** Find the doc line at a screen Y coordinate. */
        _lineAtY(y) {
            const contentRect = this.view.contentDOM.getBoundingClientRect()
            const pos = this.view.posAtCoords({ x: contentRect.left + 1, y })
            if (pos === null) return null
            return this.view.state.doc.lineAt(pos)
        }

        handleMouseMove(event) {
            const line = this._lineAtY(event.clientY)
            if (!line) { this.hide(); return }

            // Same line — no update needed
            if (line.number === this.currentLine) return

            // Hide if line already has a comment
            const fieldValue = this.view.state.field(field)
            if (fieldValue?.commentsMap.has(line.number)) {
                this.hide()
                return
            }

            // Find the gutter element for this line (by line index, not Y) —
            // elementFromPoint-based lookup was giving wrong cells.
            const gutterEl = this._gutterElForLine(line)
            if (!gutterEl) { this.hide(); return }

            // Move button into this gutter cell
            this.hide()
            this.currentLine = line.number
            gutterEl.appendChild(this.btn)
        }

        handleMouseLeave() {
            this.hide()
        }

        handleClick(event) {
            event.preventDefault()
            event.stopPropagation()

            if (this.currentLine < 1 || this.currentLine > this.view.state.doc.lines) return

            const line = this.view.state.doc.line(this.currentLine)
            this.view.dispatch({ effects: addCommentEffect.of({ lineNumber: this.currentLine, lineText: line.text }) })
            callbacks.onAdd(this.currentLine, line.text)

            this.hide()
        }

        hide() {
            this.currentLine = -1
            if (this.btn.parentElement) this.btn.parentElement.removeChild(this.btn)
        }

        destroy() {
            this.stopLocale()
            this.view.dom.removeEventListener('mousemove', this.handleMouseMove)
            this.view.dom.removeEventListener('mouseleave', this.handleMouseLeave)
            this.hide()
        }
    })
}

// ─── Theme ──────────────────────────────────────────────────────────────────

const baseTheme = EditorView.baseTheme({
    '& .cm-lineNumbers': {
        minWidth: '3rem',
    },
    '& .cm-lineNumbers .cm-gutterElement': {
        position: 'relative',
    },
    '& .cm-code-comment-add-btn': {
        position: 'absolute',
        top: '50%',
        bottom: '0',
        left: '0',
        zIndex: '1',
        height: '3rem',
        transform: 'scale(0.8) translateY(-50%)',
        transformOrigin: 'center center',
    },
    '& .cm-code-comment-add-btn-icon': {
        transform: 'scale(1.2)',
    },
    '& .cm-code-comment-wrap': {
        padding: '6px 12px 6px 8px',
        borderTop: '1px solid var(--wa-color-border-default, rgba(128, 128, 128, 0.2))',
        borderBottom: '1px solid var(--wa-color-border-default, rgba(128, 128, 128, 0.2))',
        backgroundColor: 'var(--wa-color-surface-lowered, rgba(128, 128, 128, 0.06))',
    },
    '& .cm-code-comment-textarea': {
        display: 'block',
        width: '100%',
        minHeight: '60px',
        padding: '8px',
        border: '1px solid var(--wa-color-border-default, rgba(128, 128, 128, 0.3))',
        borderRadius: '4px',
        backgroundColor: 'var(--wa-color-surface-default, transparent)',
        color: 'inherit',
        fontFamily: 'inherit',
        fontSize: 'inherit',
        lineHeight: '1.4',
        resize: 'vertical',
        outline: 'none',
        boxSizing: 'border-box',
    },
    '& .cm-code-comment-textarea:focus': {
        borderColor: 'var(--wa-color-primary, rgba(100, 150, 255, 0.5))',
        boxShadow: '0 0 0 2px color-mix(in srgb, var(--wa-color-primary, rgba(100, 150, 255, 0.15)) 20%, transparent)',
    },
    '& .cm-code-comment-textarea::placeholder': {
        color: 'var(--wa-color-text-quiet, rgba(128, 128, 128, 0.6))',
    },
    '& .cm-code-comment-help': {
        fontSize: '0.8em',
        lineHeight: '1.3',
        color: 'var(--wa-color-text-quiet, rgba(128, 128, 128, 0.6))',
        marginTop: '4px',
    },
    '& .cm-code-comment-help-dismiss': {
        color: 'var(--wa-color-text-quiet, rgba(128, 128, 128, 0.6))',
        textDecoration: 'underline',
        cursor: 'pointer',
        whiteSpace: 'nowrap',
    },
    '& .cm-code-comment-help-dismiss:hover': {
        color: 'inherit',
    },
    '& .cm-code-comment-actions': {
        display: 'flex',
        justifyContent: 'flex-end',
        gap: '6px',
        marginTop: '4px',
    },
    '& .cm-code-comment-cancel': {
        padding: '2px 12px',
        border: '1px solid var(--wa-color-border-default, rgba(128, 128, 128, 0.3))',
        backgroundColor: 'transparent',
        color: 'inherit',
        fontSize: '0.85em',
        cursor: 'pointer',
        outline: 'none',
    },
    '& .cm-code-comment-cancel:hover': {
        backgroundColor: 'var(--wa-color-surface-lowered, rgba(128, 128, 128, 0.1))',
    },
    '& .cm-code-comment-add-to-msg, & .cm-code-comment-add-all-to-msg': {
        padding: '2px 12px',
        border: '1px solid var(--wa-color-border-default, rgba(128, 128, 128, 0.3))',
        backgroundColor: 'transparent',
        color: 'inherit',
        fontSize: '0.85em',
        cursor: 'pointer',
        outline: 'none',
    },
    '& .cm-code-comment-add-to-msg:hover:not(:disabled), & .cm-code-comment-add-all-to-msg:hover:not(:disabled)': {
        backgroundColor: 'var(--wa-color-surface-lowered, rgba(128, 128, 128, 0.1))',
    },
    '& .cm-code-comment-add-to-msg:disabled, & .cm-code-comment-add-all-to-msg:disabled': {
        opacity: '0.4',
        cursor: 'default',
    },
})

// ─── Public API ─────────────────────────────────────────────────────────────

/**
 * Create a CodeMirror extension for inline code comments.
 *
 * @param {Object} options
 * @param {Array<{lineNumber: number, content: string, lineText: string}>} options.initialComments
 * @param {(lineNumber: number) => string} [options.getContent] - Read current content from the store (avoids stale widget content after scroll)
 * @param {(lineNumber: number, lineText: string) => void} options.onAdd
 * @param {(lineNumber: number, content: string) => void} options.onUpdate
 * @param {(lineNumber: number) => void} options.onRemove
 * @param {((lineNumber: number) => void)|null} options.onAddToMessage
 * @param {(() => void)|null} options.onAddAllToMessage
 * @returns {import('@codemirror/state').Extension[]}
 */
export function createCodeCommentsExtension({ initialComments = [], getContent, onAdd, onUpdate, onRemove, onAddToMessage, onAddAllToMessage, getSessionCommentCount }) {
    const callbacks = { getContent, onAdd, onUpdate, onRemove, onAddToMessage, onAddAllToMessage, getSessionCommentCount }
    const field = createField(callbacks, initialComments)
    const hoverPlugin = createHoverButtonPlugin(callbacks, field)
    return [field, hoverPlugin, baseTheme]
}

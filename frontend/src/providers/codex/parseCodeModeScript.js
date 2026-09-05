/**
 * Static extraction from Codex "code mode" ``exec`` scripts (GPT-5.6+).
 *
 * GPT-5.6 Codex models and GPT-6 Astra run in code mode: every action is a
 * ``custom_tool_call`` named ``exec`` whose ``input`` is JavaScript executed
 * in a V8 isolate by the CLI. The JS calls nested tools on a global
 * ``tools`` object (``await tools.exec_command({...})``,
 * ``await tools.apply_patch("...")``, ``tools.mcp__server__tool({...})``) —
 * and the rollout JSONL only persists the outer script, never the nested
 * calls. This module statically recovers those nested calls WITHOUT
 * executing anything: a string/comment-aware scanner finds
 * ``tools.<name>(...)`` call sites and a small literal parser resolves
 * their arguments when they are compile-time constants.
 *
 * EXACT mirror of ``src/twicc/providers/codex/code_mode_script.py`` — any
 * change here must be replicated there (same precedent as
 * ``parseCommand.js`` ↔ ``parse_command.rs``). The pytest fixture list in
 * ``tests/test_codex_code_mode.py`` is the shared behavioural contract.
 *
 * Also hosts ``parseCodeModeOutput``, the parser for the formatted status
 * header Codex prepends to every ``exec`` / ``wait`` output
 * (``format_script_status`` / ``prepend_script_status`` in
 * ``codex-rs/core/src/tools/code_mode/mod.rs``).
 *
 * Design: ``docs/plans/2026-07-10-codex-code-mode-display-design.md``.
 */

// First-line pragma the model may emit to tune the cell's execution
// (``// @exec: {"yield_time_ms": 500}``). Parsed for display only.
const PRAGMA_RE = /^[ \t]*\/\/[ \t]*@exec:[ \t]*(\{.*\})[ \t]*$/

// Status header prepended to every code-mode output by
// ``prepend_script_status`` (exact format, incl. the trailing newline).
const OUTPUT_HEADER_RE = /^Script (completed|failed|terminated|running with cell ID ([^\n]*))\nWall time (\d+(?:\.\d+)?) seconds\nOutput:\n/

const SCRIPT_ERROR_PREFIX = 'Script error:\n'

const IDENT_CHAR_RE = /[A-Za-z0-9_$]/

const WHITESPACE = new Set([' ', '\t', '\r', '\n'])

// Sentinel for "parse failed" — distinct from a successfully parsed null
// (JS ``null`` / ``undefined`` both resolve to null).
const FAIL = Symbol('parse-failed')

// ═══════════════════════════════════════════════════════════════════════
// Low-level scanning (string / comment aware)
// ═══════════════════════════════════════════════════════════════════════

/**
 * Skip a string literal starting at ``i`` (quote char), return the index
 * just past the closing quote (or ``source.length`` when unterminated).
 *
 * Template literals are skipped to the closing backtick with escape
 * handling; ``${...}`` interpolations are treated as raw text, so a
 * ``tools.*`` call inside one is not detected (accepted limitation).
 */
function skipString(source, i) {
    const quote = source[i]
    i += 1
    const n = source.length
    while (i < n) {
        const ch = source[i]
        if (ch === '\\') {
            i += 2
            continue
        }
        if (ch === quote) return i + 1
        if (ch === '\n' && quote !== '`') {
            // Unterminated single/double quote — stop at the line break so
            // a malformed script doesn't swallow the rest of the source.
            return i
        }
        i += 1
    }
    return n
}

function skipLineComment(source, i) {
    const end = source.indexOf('\n', i)
    return end === -1 ? source.length : end + 1
}

function skipBlockComment(source, i) {
    const end = source.indexOf('*/', i + 2)
    return end === -1 ? source.length : end + 2
}

function skipWsAndComments(source, i) {
    const n = source.length
    while (i < n) {
        const ch = source[i]
        if (WHITESPACE.has(ch)) {
            i += 1
        } else if (ch === '/' && i + 1 < n && source[i + 1] === '/') {
            i = skipLineComment(source, i)
        } else if (ch === '/' && i + 1 < n && source[i + 1] === '*') {
            i = skipBlockComment(source, i)
        } else {
            break
        }
    }
    return i
}

/**
 * Return ``[inner, end]`` for the balanced ``(...)`` starting at ``i``.
 * ``inner`` excludes the outer parentheses; ``end`` is the index just past
 * the closing one. String literals and comments inside are skipped, so
 * parentheses within them don't unbalance the scan. Returns
 * ``[null, source.length]`` when unbalanced.
 */
function captureParenSpan(source, i) {
    const n = source.length
    let depth = 0
    const start = i + 1
    while (i < n) {
        const ch = source[i]
        if (ch === "'" || ch === '"' || ch === '`') {
            i = skipString(source, i)
        } else if (ch === '/' && i + 1 < n && source[i + 1] === '/') {
            i = skipLineComment(source, i)
        } else if (ch === '/' && i + 1 < n && source[i + 1] === '*') {
            i = skipBlockComment(source, i)
        } else if (ch === '(') {
            depth += 1
            i += 1
        } else if (ch === ')') {
            depth -= 1
            if (depth === 0) return [source.slice(start, i), i + 1]
            i += 1
        } else {
            i += 1
        }
    }
    return [null, n]
}

// ═══════════════════════════════════════════════════════════════════════
// Literal parsing (recursive descent over a JS literal expression)
// ═══════════════════════════════════════════════════════════════════════

const STRING_ESCAPES = {
    n: '\n', t: '\t', r: '\r', b: '\b', f: '\f', v: '\v',
    0: '\0', '\n': '',  // line continuation
}

/**
 * Parse one quoted string starting at ``i``; template literals are
 * accepted only when they contain no ``${`` interpolation.
 * Every parser below returns ``[value, nextIndex]`` with ``value === FAIL``
 * on any non-literal construct.
 */
function parseStringLiteral(source, i) {
    const quote = source[i]
    i += 1
    const n = source.length
    const parts = []
    while (i < n) {
        const ch = source[i]
        if (ch === '\\') {
            if (i + 1 >= n) return [FAIL, i]
            const esc = source[i + 1]
            if (esc === 'u') {
                if (i + 5 < n && source[i + 2] === '{') {
                    const end = source.indexOf('}', i + 3)
                    if (end === -1) return [FAIL, i]
                    const code = parseInt(source.slice(i + 3, end), 16)
                    if (!Number.isFinite(code)) return [FAIL, i]
                    parts.push(String.fromCodePoint(code))
                    i = end + 1
                } else {
                    const code = parseInt(source.slice(i + 2, i + 6), 16)
                    if (!Number.isFinite(code) || source.length < i + 6) return [FAIL, i]
                    parts.push(String.fromCharCode(code))
                    i += 6
                }
                continue
            }
            if (esc === 'x') {
                const code = parseInt(source.slice(i + 2, i + 4), 16)
                if (!Number.isFinite(code)) return [FAIL, i]
                parts.push(String.fromCharCode(code))
                i += 4
                continue
            }
            parts.push(Object.prototype.hasOwnProperty.call(STRING_ESCAPES, esc) ? STRING_ESCAPES[esc] : esc)
            i += 2
            continue
        }
        if (ch === quote) return [parts.join(''), i + 1]
        if (quote === '`' && ch === '$' && i + 1 < n && source[i + 1] === '{') return [FAIL, i]
        if (ch === '\n' && quote !== '`') return [FAIL, i]
        parts.push(ch)
        i += 1
    }
    return [FAIL, i]
}

const NUMBER_RE = /[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?/y

function parseNumber(source, i) {
    NUMBER_RE.lastIndex = i
    const match = NUMBER_RE.exec(source)
    if (match === null) return [FAIL, i]
    const text = match[0]
    // JS has no int/float split — Number() covers both branches of the
    // Python mirror (int(text) vs float(text)) with identical values.
    return [Number(text), i + text.length]
}

function readIdentifier(source, i) {
    const start = i
    const n = source.length
    while (i < n && IDENT_CHAR_RE.test(source[i])) i += 1
    return [source.slice(start, i), i]
}

/** Parse a string literal / const identifier, optionally ``+``-chained. */
function parseStringExpr(source, i, consts) {
    let [value, next] = parseStringOperand(source, i, consts)
    if (value === FAIL) return [FAIL, next]
    i = next
    for (;;) {
        const j = skipWsAndComments(source, i)
        if (j >= source.length || source[j] !== '+') return [value, i]
        const [operand, end] = parseStringOperand(source, skipWsAndComments(source, j + 1), consts)
        if (operand === FAIL) return [FAIL, i]
        value += operand
        i = end
    }
}

function parseStringOperand(source, i, consts) {
    const ch = source[i]
    if (i < source.length && (ch === "'" || ch === '"' || ch === '`')) {
        return parseStringLiteral(source, i)
    }
    const [ident, j] = readIdentifier(source, i)
    if (ident && Object.prototype.hasOwnProperty.call(consts, ident)) {
        return [consts[ident], j]
    }
    return [FAIL, i]
}

/**
 * Parse one JS literal value at ``i``; returns ``[FAIL, i]`` on any
 * non-literal construct (call, spread, computed key, interpolation, …).
 */
function parseValue(source, i, consts) {
    i = skipWsAndComments(source, i)
    if (i >= source.length) return [FAIL, i]
    const ch = source[i]
    if (ch === "'" || ch === '"' || ch === '`') return parseStringExpr(source, i, consts)
    if (ch === '{') return parseObject(source, i, consts)
    if (ch === '[') return parseArray(source, i, consts)
    if (ch === '+' || ch === '-' || ch === '.' || (ch >= '0' && ch <= '9')) {
        return parseNumber(source, i)
    }
    const [ident, j] = readIdentifier(source, i)
    if (!ident) return [FAIL, i]
    if (ident === 'true') return [true, j]
    if (ident === 'false') return [false, j]
    if (ident === 'null' || ident === 'undefined') return [null, j]
    if (Object.prototype.hasOwnProperty.call(consts, ident)) {
        // Const string binding — may itself chain with ``+``.
        return parseStringExpr(source, i, consts)
    }
    return [FAIL, i]
}

function parseObject(source, i, consts) {
    const obj = {}
    i = skipWsAndComments(source, i + 1)
    const n = source.length
    if (i < n && source[i] === '}') return [obj, i + 1]
    while (i < n) {
        // Key: quoted string or bare identifier.
        let key
        if (source[i] === "'" || source[i] === '"') {
            ;[key, i] = parseStringLiteral(source, i)
            if (key === FAIL) return [FAIL, i]
        } else {
            ;[key, i] = readIdentifier(source, i)
            if (!key) return [FAIL, i]
        }
        i = skipWsAndComments(source, i)
        if (i >= n || source[i] !== ':') return [FAIL, i]
        let value
        ;[value, i] = parseValue(source, i + 1, consts)
        if (value === FAIL) return [FAIL, i]
        obj[key] = value
        i = skipWsAndComments(source, i)
        if (i < n && source[i] === ',') {
            i = skipWsAndComments(source, i + 1)
            if (i < n && source[i] === '}') return [obj, i + 1]  // trailing comma
            continue
        }
        if (i < n && source[i] === '}') return [obj, i + 1]
        return [FAIL, i]
    }
    return [FAIL, i]
}

function parseArray(source, i, consts) {
    const arr = []
    i = skipWsAndComments(source, i + 1)
    const n = source.length
    if (i < n && source[i] === ']') return [arr, i + 1]
    while (i < n) {
        let value
        ;[value, i] = parseValue(source, i, consts)
        if (value === FAIL) return [FAIL, i]
        arr.push(value)
        i = skipWsAndComments(source, i)
        if (i < n && source[i] === ',') {
            i = skipWsAndComments(source, i + 1)
            if (i < n && source[i] === ']') return [arr, i + 1]  // trailing comma
            continue
        }
        if (i < n && source[i] === ']') return [arr, i + 1]
        return [FAIL, i]
    }
    return [FAIL, i]
}

// ═══════════════════════════════════════════════════════════════════════
// Source-level passes
// ═══════════════════════════════════════════════════════════════════════

/**
 * One pass over the source collecting ``const <id> = <string-expr>;``
 * bindings (string literals, optionally ``+``-joined, incl. previously
 * collected consts). Only string bindings are recorded — that's all the
 * argument resolver dereferences (the canonical apply_patch wrapper binds
 * the patch envelope to a const).
 */
function buildConstTable(source) {
    const consts = {}
    let i = 0
    const n = source.length
    while (i < n) {
        const ch = source[i]
        if (ch === "'" || ch === '"' || ch === '`') {
            i = skipString(source, i)
        } else if (ch === '/' && i + 1 < n && source[i + 1] === '/') {
            i = skipLineComment(source, i)
        } else if (ch === '/' && i + 1 < n && source[i + 1] === '*') {
            i = skipBlockComment(source, i)
        } else if (
            source.startsWith('const', i)
            && (i === 0 || !IDENT_CHAR_RE.test(source[i - 1]))
            && i + 5 < n
            && WHITESPACE.has(source[i + 5])
        ) {
            let j = skipWsAndComments(source, i + 5)
            let name
            ;[name, j] = readIdentifier(source, j)
            if (!name) {
                i += 5
                continue
            }
            j = skipWsAndComments(source, j)
            if (j >= n || source[j] !== '=') {
                i = j
                continue
            }
            const [value, end] = parseStringExpr(source, skipWsAndComments(source, j + 1), consts)
            if (value !== FAIL) {
                consts[name] = value
                i = end
            } else {
                i = j + 1
            }
        } else {
            i += 1
        }
    }
    return consts
}

/**
 * Find every ``tools.<name>(`` call site, returning ``[name, argSpan]``
 * pairs in source order (``argSpan`` is ``null`` when unbalanced).
 */
function scanCalls(source) {
    const calls = []
    let i = 0
    const n = source.length
    while (i < n) {
        const ch = source[i]
        if (ch === "'" || ch === '"' || ch === '`') {
            i = skipString(source, i)
        } else if (ch === '/' && i + 1 < n && source[i + 1] === '/') {
            i = skipLineComment(source, i)
        } else if (ch === '/' && i + 1 < n && source[i + 1] === '*') {
            i = skipBlockComment(source, i)
        } else if (
            source.startsWith('tools.', i)
            && (i === 0 || (!IDENT_CHAR_RE.test(source[i - 1]) && source[i - 1] !== '.'))
        ) {
            const [name, j] = readIdentifier(source, i + 6)
            const k = skipWsAndComments(source, j)
            if (name && k < n && source[k] === '(') {
                const [span, end] = captureParenSpan(source, k)
                calls.push([name, span])
                i = end
            } else {
                i = j > i ? j : i + 6
            }
        } else {
            i += 1
        }
    }
    return calls
}

// ═══════════════════════════════════════════════════════════════════════
// Public API
// ═══════════════════════════════════════════════════════════════════════

/**
 * Statically extract the nested tool calls of a code-mode script.
 *
 * Returns ``{ calls, pragma }`` where ``calls`` is an ordered array of
 * ``{ name, arg, resolved }`` (``arg`` is the statically-resolved argument
 * value when ``resolved`` is true, else ``null``) and ``pragma`` is the
 * parsed first-line ``// @exec: {...}`` object (or ``null``).
 *
 * Never throws: any malformed construct degrades to unresolved calls (or
 * no calls at all) so the consumer can fall back to the generic "Run
 * code" rendering. Consumers classify the result in three tiers:
 *
 * - exactly one resolved call → dedicated per-tool rendering;
 * - calls detected but not all resolved (or several of them) → generic
 *   rendering enriched with the call list;
 * - nothing detected → raw-JS rendering.
 */
export function parseCodeModeScript(source) {
    if (typeof source !== 'string' || !source) return { calls: [], pragma: null }

    let pragma = null
    const newlineIdx = source.indexOf('\n')
    const firstLine = newlineIdx === -1 ? source : source.slice(0, newlineIdx)
    const pragmaMatch = PRAGMA_RE.exec(firstLine)
    if (pragmaMatch !== null) {
        let decoded = null
        try {
            decoded = JSON.parse(pragmaMatch[1])
        } catch {
            decoded = null
        }
        if (decoded && typeof decoded === 'object' && !Array.isArray(decoded)) {
            pragma = decoded
        }
    }

    const consts = buildConstTable(source)
    const calls = []
    for (const [name, span] of scanCalls(source)) {
        if (span !== null) {
            const stripped = span.trim()
            if (!stripped) {
                // Zero-argument call — nothing to resolve, but the call
                // itself is fully known.
                calls.push({ name, arg: null, resolved: true })
                continue
            }
            const [value, end] = parseValue(span, 0, consts)
            if (value !== FAIL && skipWsAndComments(span, end) >= span.length) {
                calls.push({ name, arg: value, resolved: true })
                continue
            }
        }
        calls.push({ name, arg: null, resolved: false })
    }
    return { calls, pragma }
}

/**
 * Parse a code-mode ``exec`` / ``wait`` tool output.
 *
 * ``output`` is the raw ``*_call_output.output`` payload value: either a
 * plain string or an array of ``{type: "input_text", text}`` segments
 * (Codex serialises single-segment outputs as a bare string). Returns
 * ``null`` when the value doesn't start with the code-mode status header —
 * the caller's signal that this is NOT a code-mode output. Otherwise
 * returns ``{ status, cellId, wallTimeSeconds, errorText, body }`` with
 * ``status`` one of ``completed`` / ``failed`` / ``terminated`` /
 * ``running`` (``cellId`` only set for ``running``; ``errorText`` from the
 * ``Script error:`` segment when present; ``body`` is the script's own
 * output with header and error segment stripped).
 */
export function parseCodeModeOutput(output) {
    let segments
    if (typeof output === 'string') {
        segments = [output]
    } else if (Array.isArray(output)) {
        segments = output
            .filter((item) => (
                item && typeof item === 'object'
                && item.type === 'input_text'
                && typeof item.text === 'string'
            ))
            .map((item) => item.text)
        if (segments.length === 0) return null
    } else {
        return null
    }

    const match = OUTPUT_HEADER_RE.exec(segments[0])
    if (match === null) return null
    const rawStatus = match[1]
    const status = rawStatus.startsWith('running') ? 'running' : rawStatus
    const cellId = status === 'running' ? (match[2] || null) : null
    const wallTimeSeconds = Number(match[3])

    let errorText = null
    const bodyParts = [segments[0].slice(match[0].length)]
    for (const segment of segments.slice(1)) {
        if (segment.startsWith(SCRIPT_ERROR_PREFIX)) {
            errorText = segment.slice(SCRIPT_ERROR_PREFIX.length)
        } else {
            bodyParts.push(segment)
        }
    }
    return {
        status,
        cellId,
        wallTimeSeconds: Number.isFinite(wallTimeSeconds) ? wallTimeSeconds : null,
        errorText,
        body: bodyParts.join(''),
    }
}

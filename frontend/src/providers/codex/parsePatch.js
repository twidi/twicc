/**
 * Parser for Codex's ``apply_patch`` v4a envelope.
 *
 * The model passes a single string of the form::
 *
 *     *** Begin Patch
 *     *** Update File: /abs/path/to/file
 *     *** Move to: /abs/path/to/new_name        (optional, only for Update File)
 *     @@ [optional context line]
 *      context line               (space-prefixed)
 *     -line to remove
 *     +line to add
 *     *** End of File             (optional EOF marker; ignored)
 *     *** Add File: /abs/path/to/new
 *     +new content line
 *     *** Delete File: /abs/path/to/old
 *     *** End Patch
 *
 * (Authoritative grammar: ``codex-rs/apply-patch/src/parser.rs``.)
 *
 * Used purely as a *pre-result* fallback — once the matching
 * canonical ``FileChange`` item arrives, we switch to the official
 * ``changes`` map (real ``unified_diff`` per file) which carries
 * accurate line numbers. Before that, this parser is enough to know
 * which files the call touches and to render a fragment-style diff
 * for each of them.
 */

const BEGIN_PATCH = '*** Begin Patch'
const END_PATCH = '*** End Patch'
const ADD_FILE = '*** Add File: '
const UPDATE_FILE = '*** Update File: '
const DELETE_FILE = '*** Delete File: '
const MOVE_TO = '*** Move to: '
const EOF_MARKER = '*** End of File'

/**
 * Strip a leading heredoc wrapper if present (``<<EOF\n…\nEOF``).
 * The model occasionally ships the patch wrapped in a shell heredoc
 * when it would otherwise be interpreted as a shell command.
 */
function unwrapHeredoc(input) {
    const trimmed = input.trimStart()
    const heredocStart = /^<<-?\s*['"]?(\w+)['"]?\s*\n/u.exec(trimmed)
    if (!heredocStart) return input
    const tag = heredocStart[1]
    const rest = trimmed.slice(heredocStart[0].length)
    const closer = new RegExp(`\\n${tag}\\s*$`, 'u')
    const m = closer.exec(rest)
    if (!m) return input
    return rest.slice(0, m.index)
}

function findBeginIndex(lines) {
    for (let i = 0; i < lines.length; i++) {
        if (lines[i].trim() === BEGIN_PATCH) return i
    }
    return -1
}

/**
 * Parse one ``Update File`` block's hunks into raw old/new fragments.
 *
 * v4a hunks don't carry ``@@ -a,b +c,d @@`` line ranges (only an
 * optional anchor identifier after ``@@``), so we can't recover the
 * file's true line numbers from the call alone. We just concatenate
 * the ``-`` and context lines into an ``oldString`` and the ``+`` and
 * context lines into a ``newString`` — the diff viewer renders them
 * fragment-style without gutter line numbers, exactly the way Claude
 * Code's Edit fallback does for ``old_string`` / ``new_string``.
 *
 * Hunks are joined by an empty line so the viewer doesn't merge them
 * visually when the patch touches several non-contiguous regions.
 */
function parseUpdateBody(bodyLines) {
    const oldChunks = []
    const newChunks = []
    let oldChunk = []
    let newChunk = []
    let firstHunk = true
    let linesAdded = 0
    let linesRemoved = 0

    function flushHunk() {
        if (oldChunk.length === 0 && newChunk.length === 0) return
        if (!firstHunk) {
            oldChunks.push('')
            newChunks.push('')
        }
        oldChunks.push(...oldChunk)
        newChunks.push(...newChunk)
        oldChunk = []
        newChunk = []
        firstHunk = false
    }

    for (const line of bodyLines) {
        if (line.startsWith('@@')) {
            flushHunk()
            continue
        }
        if (line === EOF_MARKER || line.startsWith(EOF_MARKER + ' ')) continue
        if (line.startsWith('\\')) continue
        if (line.startsWith('+')) {
            newChunk.push(line.slice(1))
            linesAdded++
        } else if (line.startsWith('-')) {
            oldChunk.push(line.slice(1))
            linesRemoved++
        } else {
            const text = line.startsWith(' ') ? line.slice(1) : line
            oldChunk.push(text)
            newChunk.push(text)
        }
    }
    flushHunk()

    return {
        oldString: oldChunks.join('\n'),
        newString: newChunks.join('\n'),
        linesAdded,
        linesRemoved,
    }
}

function parseAddBody(bodyLines) {
    const out = []
    for (const line of bodyLines) {
        if (line.startsWith('+')) out.push(line.slice(1))
        else if (line === EOF_MARKER || line.startsWith(EOF_MARKER + ' ')) continue
        else if (line.startsWith('\\')) continue
        else out.push(line)
    }
    return out.join('\n')
}

/**
 * Parse a Codex v4a apply_patch envelope.
 *
 * @param {string} input - Raw patch text (with or without a heredoc wrapper)
 * @returns {Array<{
 *     path: string,
 *     action: 'add' | 'update' | 'delete',
 *     movePath: string | null,
 *     content: string | null,
 *     oldString: string | null,
 *     newString: string | null,
 *     linesAdded: number,
 *     linesRemoved: number,
 * }>}
 *   One entry per file touched by the patch. Empty array when the
 *   envelope is unrecognisable — caller should treat that as "no
 *   structured info yet" and fall back to the raw text.
 */
export function parseApplyPatchEnvelope(input) {
    if (typeof input !== 'string' || !input) return []
    const text = unwrapHeredoc(input)
    const lines = text.split('\n')
    const start = findBeginIndex(lines)
    if (start < 0) return []

    const files = []
    let i = start + 1

    function startNewFile(action, path) {
        const file = {
            path,
            action,
            movePath: null,
            content: null,
            oldString: null,
            newString: null,
            _body: [],
        }
        files.push(file)
        return file
    }

    let current = null

    while (i < lines.length) {
        const line = lines[i]
        const trimmed = line.trim()
        if (trimmed === END_PATCH) break

        if (line.startsWith(ADD_FILE)) {
            current = startNewFile('add', line.slice(ADD_FILE.length).trim())
        } else if (line.startsWith(UPDATE_FILE)) {
            current = startNewFile('update', line.slice(UPDATE_FILE.length).trim())
        } else if (line.startsWith(DELETE_FILE)) {
            current = startNewFile('delete', line.slice(DELETE_FILE.length).trim())
        } else if (line.startsWith(MOVE_TO) && current && current.action === 'update') {
            current.movePath = line.slice(MOVE_TO.length).trim()
        } else if (current) {
            current._body.push(line)
        }
        i++
    }

    for (const file of files) {
        const body = file._body
        delete file._body
        if (file.action === 'update') {
            const { oldString, newString, linesAdded, linesRemoved } = parseUpdateBody(body)
            file.oldString = oldString
            file.newString = newString
            file.linesAdded = linesAdded
            file.linesRemoved = linesRemoved
        } else if (file.action === 'add') {
            file.content = parseAddBody(body)
            file.linesAdded = file.content
                ? file.content.split('\n').length - (file.content.endsWith('\n') ? 1 : 0)
                : 0
            file.linesRemoved = 0
        } else {
            // ``delete`` files have no body in v4a — ``content`` stays
            // null until the result arrives. We can't know the
            // pre-deletion line count from the call alone.
            file.linesAdded = 0
            file.linesRemoved = 0
        }
    }

    return files
}

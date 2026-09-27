/**
 * Utilities for TodoWrite tool data.
 */

/**
 * Validate that todos has the expected format.
 *
 * Must be a non-empty array of objects where every entry has:
 *  - `status` (string)
 *  - at least one of `content` or `activeForm` (string)
 *
 * All-or-nothing: if any single entry is invalid, the whole list is invalid.
 *
 * @param {*} todos
 * @returns {boolean}
 */
export function isValidTodos(todos) {
    if (!Array.isArray(todos) || todos.length === 0) return false
    return todos.every(t =>
        t != null && typeof t === 'object' &&
        typeof t.status === 'string' &&
        (typeof t.content === 'string' || typeof t.activeForm === 'string')
    )
}

/**
 * Get the display label for a todo entry, preferring `activeForm` with fallback to `content`.
 * @param {{content?: string, activeForm?: string}} todo
 * @returns {string}
 */
function getLabel(todo) {
    return todo.activeForm || todo.content
}

/**
 * Get the detail text for a todo entry, preferring `content` with fallback to `activeForm`.
 * @param {{content?: string, activeForm?: string}} todo
 * @returns {string}
 */
export function getDetail(todo) {
    return todo.content || todo.activeForm
}

/**
 * Build a description from a TodoWrite todos array.
 *
 * Returns an array of part objects to be joined with a separator (e.g. " — "),
 * or null if the list is empty/missing.
 *
 * Each part has:
 *  - text: the display string
 *  - status (optional): 'completed' | 'in_progress' | 'pending' — present only on
 *    parts that display a label, used to render a colored icon after the text.
 *  - invalid (optional): true if the todos data is malformed.
 *
 * Rules (in priority order):
 *  0. Invalid format → [{ text: "Invalid todo list", invalid: true }]
 *  1. Empty / missing array → null
 *  2. All completed → [{ text: "Task completed" }] or [{ text: "All x tasks completed" }]
 *  3. At least one in_progress → [{ text: "x/n" }, { text: label, status: "in_progress" }]
 *  4. No in_progress, some completed + some pending →
 *     [{ text: "x/n" }, { text: "done: …", status: "completed" }, { text: "next: …", status: "pending" }]
 *  5. All pending → [{ text: "n tasks" }, { text: "next: …", status: "pending" }]
 *
 * @param {*} todos
 * @returns {Array<{text: string, status?: string, invalid?: boolean}>|null}
 */
export function getTodoDescription(todos) {
    if (!todos || (Array.isArray(todos) && todos.length === 0)) return null

    if (!isValidTodos(todos)) {
        return [{ text: 'Invalid todo list', invalid: true }]
    }

    const total = todos.length
    const completedCount = todos.filter(t => t.status === 'completed').length

    // Case 1: all completed
    if (completedCount === total) {
        return [
            { text: total === 1 ? 'Task completed' : `All ${total} tasks completed`, status: 'completed' },
        ]
    }

    // Case 2: at least one in_progress — pick the last one
    const lastInProgress = findLast(todos, t => t.status === 'in_progress')
    if (lastInProgress) {
        return [
            { text: `${completedCount + 1}/${total}` },
            { text: getLabel(lastInProgress), status: 'in_progress' },
        ]
    }

    // Case 3: no in_progress, some completed → show last completed
    if (completedCount > 0) {
        const lastCompleted = findLast(todos, t => t.status === 'completed')
        return [
            { text: `${completedCount}/${total}` },
            { text: getLabel(lastCompleted), status: 'completed' },
        ]
    }

    // Case 4: all pending
    const firstPending = todos[0]
    return [
        { text: total === 1 ? '1 task' : `${total} tasks` },
        { text: `next: ${getLabel(firstPending)}`, status: 'pending' },
    ]
}

/**
 * Indices of the todos that became completed between two snapshots of the same list.
 *
 * An index counts when `nextTodos[i]` is completed, `previousTodos[i]` exists and is not,
 * and both items have the same identity (`content` when both have one, else `activeForm`,
 * else the index alone). The identity check stops a false positive when an item is
 * inserted before others and statuses shift by index.
 *
 * @param {Array<{status: string, content?: string, activeForm?: string}>|null|undefined} previousTodos
 * @param {Array<{status: string, content?: string, activeForm?: string}>|null|undefined} nextTodos
 * @returns {Set<number>}
 */
export function findNewlyCompleted(previousTodos, nextTodos) {
    const indices = new Set()
    if (!Array.isArray(previousTodos) || !Array.isArray(nextTodos)) return indices
    const shared = Math.min(previousTodos.length, nextTodos.length)
    for (let i = 0; i < shared; i++) {
        const previous = previousTodos[i]
        const next = nextTodos[i]
        if (!previous || !next) continue
        if (next.status !== 'completed' || previous.status === 'completed') continue
        if (sameTodoIdentity(previous, next)) indices.add(i)
    }
    return indices
}

function sameTodoIdentity(a, b) {
    if (typeof a.content === 'string' && typeof b.content === 'string') return a.content === b.content
    if (typeof a.activeForm === 'string' && typeof b.activeForm === 'string') return a.activeForm === b.activeForm
    return true
}

/**
 * Find the last element matching a predicate (Array.findLast polyfill-safe).
 */
function findLast(arr, predicate) {
    for (let i = arr.length - 1; i >= 0; i--) {
        if (predicate(arr[i])) return arr[i]
    }
    return undefined
}

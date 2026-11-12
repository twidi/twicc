import { i18n } from '../../i18n/index.js'

// frontend/src/utils/timeline/filters.js
//
// The filters of the Timeline tab (filters design sections 3.1 to 3.5 and 4.5): the view, the panel groups
// (Events, Provider, Done by), the session filter, the query they send to `GET /api/timeline/` and the pills
// that show them. Pure: no store, no DOM.
//
// The keys are copied from `twicc/core/services/timeline_filters.py` (and the `Provider` enum);
// `tests/test_timeline_filters_sync.py` pins that both sides agree.

/** The views of section 4.1, in display order. `short` is the label of a narrow band (section 3.1). */
export const VIEWS = [
    { value: 'all', get label() { return i18n.global.t('session.timeline.filters.all') }, get short() { return i18n.global.t('session.timeline.filters.all') } },
    { value: 'messages', get label() { return i18n.global.t('session.timeline.filters.messages') }, get short() { return i18n.global.t('session.timeline.filters.messages') } },
    { value: 'without_turns', get label() { return i18n.global.t('session.timeline.filters.activity') }, get short() { return i18n.global.t('session.timeline.filters.activity') } },
    { value: 'waits_problems', get label() { return i18n.global.t('session.timeline.filters.waitsProblems') }, get short() { return i18n.global.t('session.timeline.filters.waits') } },
]

/** The event families of section 4.2, in display order, each with a hint of what it holds. */
export const FAMILIES = [
    { value: 'sessions', get label() { return i18n.global.t('session.timeline.filters.sessions') }, get hint() { return i18n.global.t('session.timeline.filters.createdRenamed') } },
    { value: 'processes', get label() { return i18n.global.t('session.timeline.filters.processes') }, get hint() { return i18n.global.t('session.timeline.filters.startedStopped') } },
    { value: 'turns', get label() { return i18n.global.t('session.timeline.filters.turns') }, get hint() { return i18n.global.t('session.timeline.filters.startedEnded') } },
    { value: 'messages', get label() { return i18n.global.t('session.timeline.filters.messages') }, get hint() { return i18n.global.t('session.timeline.filters.messagesHint') } },
    { value: 'compactions', get label() { return i18n.global.t('session.timeline.filters.compactions') }, get hint() { return i18n.global.t('session.timeline.filters.requestedCompleted') } },
    { value: 'waits', get label() { return i18n.global.t('session.timeline.filters.waitingYou') }, get hint() { return i18n.global.t('session.timeline.filters.approvalsQuestions') } },
    { value: 'settings', get label() { return i18n.global.t('session.timeline.filters.settings') }, get hint() { return i18n.global.t('session.timeline.filters.settingsHint') } },
    { value: 'organization', get label() { return i18n.global.t('session.timeline.filters.organization') }, get hint() { return i18n.global.t('session.timeline.filters.organizationHint') } },
    { value: 'workflows', get label() { return i18n.global.t('session.timeline.filters.workflows') }, get hint() { return i18n.global.t('session.timeline.filters.startedResumedEnded') } },
    { value: 'twicc', label: 'TwiCC', get hint() { return i18n.global.t('session.timeline.filters.startedStopped') } },
]

/** The "Done by" values of section 4.3, in rule order. */
export const AUTHORS = [
    { value: 'unknown', get label() { return i18n.global.t('session.timeline.filters.unknown') } },
    { value: 'automatic', get label() { return i18n.global.t('session.timeline.filters.automatic') } },
    { value: 'you', get label() { return i18n.global.t('session.timeline.filters.you') } },
    { value: 'agent_cli_mcp', get label() { return i18n.global.t('session.timeline.filters.agentCliMcp') } },
    { value: 'external_mcp', get label() { return i18n.global.t('session.timeline.filters.externalMcp') } },
    { value: 'session_agent', get label() { return i18n.global.t('session.timeline.filters.sessionAgent') } },
]

/** The provider keys (the backend `Provider` values). Their labels come from the provider helpers. */
export const PROVIDERS = ['claude_code', 'codex']

const DEFAULT_VIEW = 'all'
const SHOWN_VALUES = 2
const SHORT_ID_LENGTH = 8

const FAMILY_KEYS = FAMILIES.map((family) => family.value)
const AUTHOR_KEYS = AUTHORS.map((author) => author.value)
const FAMILY_LABELS = Object.fromEntries(FAMILIES.map((family) => [family.value, () => family.label]))
const AUTHOR_LABELS = Object.fromEntries(AUTHORS.map((author) => [author.value, () => author.label]))

/**
 * The panel groups, in pill order (section 3.3): the filters field, the query parameter, the pill label, the
 * constant order of the values and their labels (null for the providers: the caller names them).
 */
const GROUPS = [
    { group: 'families', param: 'families', get label() { return i18n.global.t('session.timeline.filters.events') }, order: FAMILY_KEYS, valueLabels: FAMILY_LABELS },
    { group: 'providers', param: 'provider', get label() { return i18n.global.t('session.timeline.filters.provider') }, order: PROVIDERS, valueLabels: null },
    { group: 'authors', param: 'authors', get label() { return i18n.global.t('session.timeline.filters.doneBy') }, order: AUTHOR_KEYS, valueLabels: AUTHOR_LABELS },
]

/**
 * @typedef {Object} TimelineFilters
 * @property {string} view - One of the `VIEWS` values.
 * @property {string[]} families - Checked `FAMILIES` values.
 * @property {string[]} providers - Checked `PROVIDERS` values.
 * @property {string[]} authors - Checked `AUTHORS` values.
 * @property {boolean} includeMuted - Include muted sessions in this timeline and its automatic speech.
 * @property {({id: string, title: (string|null)}|null)} session - The session filter, with the title captured
 *   when it was set (section 3.4).
 */

/** @returns {TimelineFilters} The unfiltered state: a new object on each call. */
export function createFilters() {
    return { view: DEFAULT_VIEW, families: [], providers: [], authors: [], session: null, includeMuted: false }
}

/**
 * Check or uncheck one value of a group.
 *
 * @param {string[]} list - The checked values.
 * @param {string} value
 * @param {string[]} order - The constant order of the group.
 * @returns {string[]} A new list in the order of `order` (a value outside it is dropped).
 */
export function toggleValue(list, value, order) {
    const checked = new Set(list)
    if (checked.has(value)) checked.delete(value)
    else checked.add(value)
    return order.filter((key) => checked.has(key))
}

/** The checked values of a group: its known keys only, in constant order. Every function below reads these. */
function checkedValues(list, order) {
    return order.filter((key) => list.includes(key))
}

/** The checked values of a group, or null when the group does not filter (none or all, 4.5). */
function effectiveValues(list, order) {
    const values = checkedValues(list, order)
    return values.length === 0 || values.length === order.length ? null : values
}

/** The view, `all` when unset. */
function viewOf(filters) {
    return filters.view || DEFAULT_VIEW
}

/** The session filter when it names a session (an id), else null. */
function sessionOf(filters) {
    return filters.session?.id ? filters.session : null
}

/**
 * The query parameters of `GET /api/timeline/` for these filters: only the entries that filter.
 *
 * @param {TimelineFilters} filters
 * @returns {Object<string, string>} `view` (unless `all`), `families`, `provider`, `authors` (comma lists in
 *   constant order; omitted when empty or complete), `session` (the id), `include_muted` (only when enabled).
 */
export function toQuery(filters) {
    const query = {}
    const view = viewOf(filters)
    if (view !== DEFAULT_VIEW) query.view = view
    for (const { group, param, order } of GROUPS) {
        const values = effectiveValues(filters[group], order)
        if (values) query[param] = values.join(',')
    }
    const session = sessionOf(filters)
    if (session) query.session = session.id
    if (filters.includeMuted) query.include_muted = '1'
    return query
}

/**
 * @param {Object<string, string>} query - A `toQuery` result.
 * @returns {string} A stable string: equal queries give equal keys, whatever their key order.
 */
export function queryKey(query) {
    return JSON.stringify(Object.keys(query).sort().map((key) => [key, query[key]]))
}

/**
 * Whether the list shows the turn boundaries (`turn.started` and `turn.ended`), so it can draw them as the turn
 * rail (see rows.js). The views `all` and `messages` hold them, `without_turns` and `waits_problems` do not.
 * With the Events group checked, Turns must be one of the values, and not the only one: a list of turns alone has
 * nothing for a rail to join (every turn looks empty), so the boundaries stay plain rows. A Done by filter can keep one boundary of a
 * turn and drop the other: the rail needs both, so it is off then.
 *
 * @param {TimelineFilters} filters
 * @returns {boolean}
 */
export function showsTurnRail(filters) {
    const view = viewOf(filters)
    if (view !== 'all' && view !== 'messages') return false
    if (effectiveValues(filters.authors, AUTHOR_KEYS)) return false
    const families = effectiveValues(filters.families, FAMILY_KEYS)
    return families === null || (families.includes('turns') && families.length > 1)
}

/**
 * @param {TimelineFilters} filters
 * @returns {number} The number of checked panel values (the Filters button count, section 3.1). The view and
 *   the session filter do not count.
 */
export function activeCount(filters) {
    return GROUPS.reduce((count, { group, order }) => count + checkedValues(filters[group], order).length, 0)
        + (filters.includeMuted ? 1 : 0)
}

function pill(group, label, values) {
    return {
        group, label, values,
        shown: values.slice(0, SHOWN_VALUES),
        more: Math.max(0, values.length - SHOWN_VALUES),
        title: new Intl.ListFormat(i18n.global.locale.value, { style: 'short', type: 'unit' }).format(values),
    }
}

/**
 * @typedef {Object} FilterPill
 * @property {'families'|'providers'|'authors'|'includeMuted'|'session'} group
 * @property {string} label - `Events`, `Provider`, `Done by` or `Session`.
 * @property {string[]} values - Every value label, in constant order.
 * @property {string[]} shown - The first value labels the pill shows.
 * @property {number} more - The count of the other ones (the "+N").
 * @property {string} title - Every value label, joined by ", " (the tooltip).
 */

/**
 * The active filter pills of section 3.3: one per group with checked values, in the order Events, Provider,
 * Done by, then the session pill. The view is not a pill.
 *
 * @param {TimelineFilters} filters
 * @param {Object} labels
 * @param {(provider: string) => string} labels.providerLabel - The label of a provider key.
 * @returns {FilterPill[]}
 */
export function groupPills(filters, { providerLabel }) {
    const pills = []
    for (const { group, label, order, valueLabels } of GROUPS) {
        const values = checkedValues(filters[group], order)
        const labelOf = valueLabels ? (value) => valueLabels[value]() : providerLabel
        if (values.length) pills.push(pill(group, label, values.map(labelOf)))
    }
    if (filters.includeMuted) pills.push(pill('includeMuted', i18n.global.t('session.timeline.filters.sessions'), [i18n.global.t('session.timeline.filters.mutedIncluded')]))
    const session = sessionOf(filters)
    if (session) {
        pills.push(pill('session', i18n.global.t('session.timeline.filters.session'), [session.title || String(session.id).slice(0, SHORT_ID_LENGTH)]))
    }
    return pills
}

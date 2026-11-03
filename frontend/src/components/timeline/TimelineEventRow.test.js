import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import * as Vue from 'vue'
import { Message } from '../../../tests/helpers/timelineMessageComponent.js'

import { declarationsOf } from '../../../tests/helpers/cssDeclarations.js'
import { compileComponent, descendants, flush, makeRenderer } from '../../../tests/helpers/scrollerComponentHarness.js'
import * as describeEventModule from '../../utils/timeline/describeEvent.js'
import * as filtersModule from '../../utils/timeline/filters.js'
import * as partsModule from '../../utils/timeline/parts.js'
import { buildTimelineRows } from '../../utils/timeline/rows.js'

const RouterLink = { props: ['to'], setup: (props, { slots }) => () => Vue.h('a', { 'data-to': JSON.stringify(props.to) }, slots.default?.()) }
const ProviderIcon = { props: ['provider'], setup: (props) => () => Vue.h('i', { 'data-provider': props.provider }) }
const ProjectBadge = { props: ['projectId'], setup: (props) => () => Vue.h('b', props.projectId) }

const knownProjects = new Set(['p1'])
const dataStore = { getProject: (id) => (knownProjects.has(id) ? { id } : null) }
const route = { name: 'project', params: { projectId: 'p1' }, query: {} }

const Mention = compileComponent(new URL('./TimelineSessionMention.vue', import.meta.url), {
    'vue-router': { RouterLink, useRoute: () => route },
    '../../utils/sessionRoute': { sessionRouteLocation: (target) => ({ name: 'session', params: { projectId: 'p1', sessionId: target.id } }) },
    '../ui/ProviderIcon.vue': ProviderIcon,
})
const WordingPart = compileComponent(new URL('./TimelineWordingPart.vue', import.meta.url), { './TimelineSessionMention.vue': Mention })
const AppTooltip = { props: ['for', 'trigger'], setup: (_, { slots }) => () => Vue.h('span', { class: 'app-tooltip' }, slots.default?.()) }
const Line = compileComponent(new URL('./TimelineEventLine.vue', import.meta.url), {
    '../../stores/settings': { useSettingsStore: () => ({ isTouchDevice: false }) },
    '../ui/AppTooltip.vue': AppTooltip,
    '../../utils/timeline/parts': partsModule,
    './TimelineSessionMention.vue': Mention,
    './TimelineWordingPart.vue': WordingPart,
})
const Row = compileComponent(new URL('./TimelineEventRow.vue', import.meta.url), {
    'vue-router': { RouterLink },
    '../../stores/data': { useDataStore: () => dataStore },
    '../../utils/timeline/describeEvent': describeEventModule,
    '../project/ProjectBadge.vue': ProjectBadge,
    './TimelineSessionMention.vue': Mention,
    './TimelineEventLine.vue': Line,
    './TimelineMessage.vue': Message,
})

const sessions = {
    s1: { title: 'Fix the build', project_id: 'p1', provider: 'codex', hidden: false, archived: false },
    secret: { title: 'Hidden one', project_id: 'p1', provider: 'claude_code', hidden: true, archived: true },
    parent: { title: 'The parent', project_id: 'p1', provider: 'codex', hidden: false, archived: false },
}
let nextId = 1
const make = (kind, fields = {}) => ({
    id: nextId++, kind, occurred_at: '2026-10-14T14:32:07.000000Z', precision: 'exact', actor: 'agent', via: null,
    actor_ref: null, actor_name: null, reason: '', data: {}, session_id: 's1', project_id: 'p1', provider: 'codex', ...fields,
})

const rawText = (node) => node.type === '#comment' ? '' : `${node.text ?? ''}${node.children.map(rawText).join('')}`

/** The visible text: a tooltip's text is shown only on hover, never in the row's text. */
function textOf(node) {
    if (node.type === '#comment' || String(node.props.class ?? '') === 'app-tooltip') return ''
    return `${node.text ?? ''}${node.children.map(textOf).join('')}`
}

/** The links inside the session mentions (the project name is a link too). */
const sessionLinks = (root) => byClass(root, 'timeline-session').flatMap((mention) => descendants(mention, (n) => n.type === 'a'))

/** `emitted`: the `toggle-session` payloads of the row, in order. */
async function render(t, events, { scope = { kind: 'global' }, index = 0, sessionTable = sessions, sessionFilterId = null, emitted = [] } = {}) {
    const rows = buildTimelineRows(events, { sessions: sessionTable, scope, dayKeyOf: () => 'd', dayLabelOf: () => 'Day' })
        .filter((row) => row.type === 'event')
    const { renderer, root } = makeRenderer()
    const context = { sessions: sessionTable, labelsFor: () => null, time: { locale: 'en-GB', timeZone: 'UTC' } }
    const app = renderer.createApp({
        render: () => Vue.h(Row, { row: rows[index], context, sessionFilterId, onToggleSession: (payload) => emitted.push(payload) }),
    })
    app.mount(root)
    t.after(() => app.unmount())
    await flush()
    return root
}

const byClass = (root, name) => descendants(root, (node) => String(node.props.class ?? '').split(' ').includes(name))
const classesOf = (node) => String(node.props.class ?? '')

test('a plain row: icon with severity, wording and the time on the right with the tooltip', async (t) => {
    const root = await render(t, [make('process.started', { data: { cause: 'resume', hybrid: true } })])
    const [row] = byClass(root, 'timeline-row')
    assert.match(classesOf(row), /timeline-row--success/)
    assert.equal(row.props['data-kind'], 'process.started')
    assert.equal(descendants(root, (n) => n.type === 'wa-icon' && String(n.props.class).includes('timeline-row-icon'))[0].props.name, 'play')
    assert.equal(textOf(byClass(root, 'timeline-wording')[0]), 'Process resumed hybrid CLI')
    assert.deepEqual(byClass(root, 'timeline-tag').map(textOf), ['hybrid CLI'])
    const [time] = byClass(root, 'timeline-time')
    assert.match(textOf(time), /^14:32/)
    // The time's tooltip shows the date in the format of the chat's message timestamps.
    assert.match(rawText(byClass(root, 'app-tooltip')[0]), /^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d$/)
})

test('the group head: a header line (provider icon, title link) above its own event line', async (t) => {
    const root = await render(t, [make('turn.started')])
    const [row] = byClass(root, 'timeline-row')
    assert.equal(byClass(root, 'timeline-row-header').length, 1)
    const [header] = byClass(root, 'timeline-row-header')
    const [line] = byClass(root, 'timeline-event-line')
    // The header and the event line are siblings in the card: the header is a line of its own, above the event.
    const [card] = row.children.filter((n) => n.type !== '#comment')
    assert.equal(classesOf(card), 'timeline-row-card')
    // Elements only: the space between the two lines is for a copy, it renders nothing between two blocks.
    assert.deepEqual(card.children.filter((n) => n.type !== '#comment' && n.type !== '#text').map(classesOf), ['timeline-row-header', 'timeline-event-line timeline-event-line--neutral timeline-event-line--indented'])
    assert.equal(descendants(line, (n) => n === header).length, 0)
    const link = descendants(header, (n) => n.type === 'a')[0]
    assert.equal(JSON.parse(link.props['data-to']).params.sessionId, 's1')
    assert.equal(textOf(link), 'Fix the build')
    assert.equal(descendants(header, (n) => n.type === 'i')[0].props['data-provider'], 'codex')
    // The event line carries the event only: icon, wording, time; no session mention.
    assert.equal(byClass(line, 'timeline-session').length, 0)
    assert.equal(descendants(line, (n) => n.type === 'wa-icon' && classesOf(n).includes('timeline-row-icon')).length, 1)
    assert.equal(byClass(line, 'timeline-time').length, 1)
})

test('the header title is one truncating line with the full title as tooltip, link or plain text', async (t) => {
    const long = 'A very long session title that will never fit on one narrow mobile line'
    const linked = await render(t, [make('turn.started')], { sessionTable: { s1: { ...sessions.s1, title: long } } })
    const [title] = byClass(byClass(linked, 'timeline-row-header')[0], 'timeline-session-title')
    assert.equal(title.type, 'a')
    assert.equal(title.props.title, long)
    assert.match(classesOf(byClass(linked, 'timeline-session')[0]), /timeline-session--truncate/)
    const plain = await render(t, [make('turn.started', { session_id: 'secret', provider: 'claude_code' })])
    const [plainTitle] = byClass(byClass(plain, 'timeline-row-header')[0], 'timeline-session-title')
    assert.equal(plainTitle.type, 'span')
    assert.equal(plainTitle.props.title, 'Hidden one')
})

test('the mention inside the wording keeps the plain variant; the author label cuts its title with the full one as tooltip', async (t) => {
    const created = await render(t, [make('session.created', { actor: 'user', via: 'ui', data: { origin: 'twicc', spawned_by: 'parent' } })])
    const [inWording] = byClass(byClass(created, 'timeline-wording')[0], 'timeline-session')
    assert.doesNotMatch(classesOf(inWording), /timeline-session--(truncate|ellipsis)/)
    assert.equal(byClass(inWording, 'timeline-session-title')[0].props.title ?? null, null)
    const labelled = await render(t, [make('archived.started', { actor: 'agent', via: 'cli', actor_ref: 'parent' })])
    const [byAuthor] = byClass(byClass(labelled, 'timeline-author')[0], 'timeline-session')
    assert.match(classesOf(byAuthor), /timeline-session--ellipsis/)
    assert.doesNotMatch(classesOf(byAuthor), /timeline-session--truncate/)
    assert.equal(byClass(byAuthor, 'timeline-session-title')[0].props.title, 'The parent')
})

test('the author line CSS: a line of its own that never wraps; the session title shrinks, "by" and the source do not', () => {
    const line = readFileSync(new URL('./TimelineEventLine.vue', import.meta.url), 'utf8')
    const style = line.slice(line.indexOf('<style'))
    const author = declarationsOf(style, '.timeline-author')
    assert.deepEqual(['display', 'white-space', 'min-width'].map((name) => author.get(name)), ['flex', 'nowrap', '0'])
    assert.equal(declarationsOf(style, '.timeline-author-text').get('flex'), 'none')
    const alone = declarationsOf(style, '.timeline-author-text:only-child')
    assert.deepEqual(['flex', 'overflow', 'text-overflow'].map((name) => alone.get(name)), ['0 1 auto', 'hidden', 'ellipsis'])
    const mention = readFileSync(new URL('./TimelineSessionMention.vue', import.meta.url), 'utf8')
    assert.equal(declarationsOf(mention, '.timeline-session--ellipsis').get('flex'), '0 1 auto')
    const title = declarationsOf(mention, '.timeline-session--ellipsis .timeline-session-title')
    assert.deepEqual(['overflow', 'text-overflow', 'white-space', 'min-width'].map((name) => title.get(name)), ['hidden', 'ellipsis', 'nowrap', '0'])
})

test('a following row has no header, and its event line has the same indent as the head one', async (t) => {
    const root = await render(t, [make('turn.ended', { occurred_at: '2026-10-14T14:32:09.000000Z', data: { background_work: null } }), make('turn.started')], { index: 1 })
    assert.doesNotMatch(classesOf(byClass(root, 'timeline-row')[0]), /timeline-row--indented/, 'no class without a rule')
    assert.equal(byClass(root, 'timeline-row-header').length, 0)
    assert.equal(byClass(root, 'timeline-session').length, 0)
    assert.match(classesOf(byClass(root, 'timeline-event-line')[0]), /timeline-event-line--indented/)
})

test('an instance event is one line: no header, not indented, the instance class for its semibold wording', async (t) => {
    const root = await render(t, [make('backend.started', { session_id: null, project_id: null, provider: null, actor: 'system', data: {} })])
    const [row] = byClass(root, 'timeline-row')
    assert.match(classesOf(row), /timeline-row--instance/)
    assert.equal(byClass(root, 'timeline-row-header').length, 0)
    const [line] = byClass(root, 'timeline-event-line')
    assert.doesNotMatch(classesOf(line), /timeline-event-line--indented/)
    assert.equal(byClass(line, 'timeline-time').length, 1)
})

test('every event line has its time: head, following and instance rows', async (t) => {
    const events = [
        make('turn.ended', { occurred_at: '2026-10-14T14:32:09.000000Z', data: { background_work: null } }),
        make('turn.started'),
        make('backend.started', { session_id: null, project_id: null, provider: null, actor: 'system', data: {} }),
    ]
    for (const index of [0, 1, 2]) {
        const root = await render(t, events, { index })
        const lines = byClass(root, 'timeline-event-line')
        assert.equal(lines.length, 1, `row ${index}`)
        assert.equal(byClass(lines[0], 'timeline-time').length, 1, `row ${index}`)
        assert.match(textOf(byClass(lines[0], 'timeline-time')[0]), /^\d\d:\d\d$/, `row ${index}`)
    }
})

const onlyControls = (root) => byClass(root, 'timeline-row-only')

test('a head row has an "Only this session" control after the title, before the project, not pressed', async (t) => {
    const root = await render(t, [make('turn.started')])
    const [header] = byClass(root, 'timeline-row-header')
    const [control] = onlyControls(header)
    assert.equal(control.type, 'button')
    assert.equal(control.props.type, 'button')
    assert.equal(control.props['aria-pressed'], 'false')
    assert.equal(control.props.title, 'Only this session')
    // The visible part is the filter icon (no label: hidden from assistive tech); the name is a visually hidden text.
    const [icon] = descendants(control, (n) => n.type === 'wa-icon')
    assert.deepEqual([icon.props.name, icon.props.label], ['filter', undefined])
    assert.deepEqual(byClass(control, 'timeline-row-only-text').map(textOf), ['Only this session'])
    // The header's flex items, in order: the title, the control, the project.
    const items = header.children.filter((n) => n.type !== '#comment' && n.type !== '#text')
    const name = (n) => ['timeline-session', 'timeline-row-only', 'timeline-project'].find((c) => classesOf(n).split(' ').includes(c))
    assert.deepEqual(items.map(name), ['timeline-session', 'timeline-row-only', 'timeline-project'])
    // Only the head row carries it: one per block.
    assert.equal(onlyControls(root).length, 1)
})

test('with the session filter on the row\'s session, the control is pressed: same name, the tooltip says what a click does', async (t) => {
    const states = [[null, 'false', 'Only this session'], ['s1', 'true', 'Show every session'], ['parent', 'false', 'Only this session']]
    for (const [sessionFilterId, pressed, tooltip] of states) {
        const [control] = onlyControls(await render(t, [make('turn.started')], { sessionFilterId }))
        // A toggle button keeps its name in both states (WAI-ARIA APG): `aria-pressed` alone carries the state.
        assert.deepEqual(byClass(control, 'timeline-row-only-text').map(textOf), ['Only this session'], `filter ${sessionFilterId}`)
        assert.equal(control.props['aria-pressed'], pressed, `filter ${sessionFilterId}`)
        assert.equal(control.props.title, tooltip, `filter ${sessionFilterId}`)
        assert.equal(control.props['aria-label'], undefined, 'the name is the hidden text')
    }
})

test('a click emits toggle-session with the id and the side-table title, pressed or not', async (t) => {
    const emitted = []
    const root = await render(t, [make('turn.started')], { emitted })
    onlyControls(root)[0].props.onClick()
    assert.equal(emitted.length, 1)
    assert.deepEqual(Object.entries(emitted[0]), [['id', 's1'], ['title', 'Fix the build']])
    const pressedEmitted = []
    const pressed = await render(t, [make('turn.started')], { sessionFilterId: 's1', emitted: pressedEmitted })
    onlyControls(pressed)[0].props.onClick()
    assert.deepEqual(Object.entries(pressedEmitted[0]), [['id', 's1'], ['title', 'Fix the build']])
})

test('an untitled session emits title null (not the full id of the header), and its pill shows the short id', async (t) => {
    const untitled = { 'abcdef0123456789': { title: '', project_id: 'p1', provider: 'codex', hidden: false, archived: false } }
    for (const sessionTable of [untitled, {}]) {
        const emitted = []
        const root = await render(t, [make('turn.started', { session_id: 'abcdef0123456789' })], { sessionTable, emitted })
        // The header itself shows the full id.
        assert.equal(textOf(byClass(root, 'timeline-session-title')[0]), 'abcdef0123456789')
        onlyControls(root)[0].props.onClick()
        assert.deepEqual(Object.entries(emitted[0]), [['id', 'abcdef0123456789'], ['title', null]])
        const filters = { ...filtersModule.createFilters(), session: emitted[0] }
        const pills = filtersModule.groupPills(filters, { providerLabel: (p) => p })
        assert.equal(pills.length, 1)
        assert.deepEqual([pills[0].group, pills[0].label, pills[0].shown.join()], ['session', 'Session', 'abcdef01'])
    }
})

test('a following row and an instance row have no "Only this session" control', async (t) => {
    const following = await render(t, [make('turn.ended', { occurred_at: '2026-10-14T14:32:09.000000Z', data: { background_work: null } }), make('turn.started')], { index: 1 })
    assert.equal(onlyControls(following).length, 0)
    const instance = await render(t, [make('backend.started', { session_id: null, project_id: null, provider: null, actor: 'system', data: {} })])
    assert.equal(onlyControls(instance).length, 0)
    assert.equal(descendants(instance, (n) => n.type === 'button').length, 0)
})

test('the "Only this session" CSS: a native button reset, never copied, hidden until hover or focus only with a hover pointer', () => {
    const row = readFileSync(new URL('./TimelineEventRow.vue', import.meta.url), 'utf8')
    const style = row.slice(row.indexOf('<style'))
    const control = declarationsOf(style, '.timeline-row-only')
    // Web Awesome's native.css makes every <button> a form control: reset like the error toggle.
    assert.deepEqual(['appearance', 'border', 'background', 'padding', 'height', 'flex'].map((name) => control.get(name)),
        ['none', 'none', 'none', 'var(--wa-space-3xs)', 'auto', '0 0 auto'])
    // Visible by default (a touch screen has no hover): never hidden outside the hover media query.
    assert.ok(!control.has('opacity') && !control.has('visibility') && control.get('display') !== 'none')
    assert.ok(!/\n\.timeline-row-only[^{]*\{[^}]*opacity:\s*0[;\s]/.test(style.slice(0, style.indexOf('@media (hover: hover)'))))
    // A copy of the row never gets its label.
    assert.deepEqual([control.get('-webkit-user-select'), control.get('user-select')], ['none', 'none'])
    // With a hover pointer: hidden until the header is hovered or the control has the keyboard focus (or is pressed).
    const start = style.indexOf('@media (hover: hover) {')
    assert.notEqual(start, -1)
    const media = style.slice(start, style.indexOf('\n}', start)).replace(/\n {4}/g, '\n')
    assert.equal(declarationsOf(media, '.timeline-row-only').get('opacity'), '0')
    const shown = declarationsOf(media, '.timeline-row-header:hover .timeline-row-only,\n.timeline-row-only:focus-visible,\n.timeline-row-only[aria-pressed="true"]')
    assert.equal(shown.get('opacity'), '1')
    assert.equal(declarationsOf(style, '.timeline-row-only:focus-visible').get('outline'), 'var(--wa-focus-ring)')
    // The hidden text stays in the flow (no position): the positioned pattern is blockified.
    const text = declarationsOf(style, '.timeline-row-only-text')
    assert.deepEqual(['display', 'width', 'height', 'overflow', 'clip-path', 'white-space'].map((name) => text.get(name)),
        ['inline-block', '1px', '1px', 'hidden', 'inset(50%)', 'nowrap'])
    assert.ok(!text.has('position'))
})

test('the header carries no state marker: a hidden and archived session is plain text, an archived one keeps its link', async (t) => {
    const stateIcon = (n) => n.type === 'wa-icon' && ['box-archive', 'eye-slash', 'thumbtack'].includes(n.props.name)
    const hidden = await render(t, [make('turn.started', { session_id: 'secret', provider: 'claude_code' })])
    assert.equal(sessionLinks(hidden).length, 0)
    assert.equal(textOf(byClass(hidden, 'timeline-session-title--plain')[0]), 'Hidden one')
    assert.equal(descendants(hidden, stateIcon).length, 0)
    assert.equal(byClass(hidden, 'timeline-marker').length, 0)
    const archivedOnly = { s1: { ...sessions.s1, archived: true } }
    const archived = await render(t, [make('turn.started')], { sessionTable: archivedOnly })
    assert.equal(sessionLinks(archived).length, 1)
    assert.equal(descendants(archived, stateIcon).length, 0)
    assert.equal(byClass(archived, 'timeline-marker').length, 0)
})

test('a session with no entry shows its id as plain text', async (t) => {
    const root = await render(t, [make('turn.started', { session_id: 'purged-session-id' })], { sessionTable: {} })
    assert.equal(sessionLinks(root).length, 0)
    assert.equal(textOf(byClass(root, 'timeline-session-title')[0]), 'purged-session-id')
})

test('the project name: a link on a global page, plain text for an unknown project, absent on a worktree page', async (t) => {
    const global = await render(t, [make('turn.started')])
    const links = descendants(global, (n) => n.type === 'a')
    assert.equal(JSON.parse(links[1].props['data-to']).params.projectId, 'p1')
    assert.equal(sessionLinks(global).length, 1)
    assert.equal(descendants(global, (n) => n.type === 'b')[0].text, 'p1')
    assert.equal(byClass(byClass(global, 'timeline-row-header')[0], 'timeline-project').length, 1, 'the project is on the header line')
    const unknown = await render(t, [make('turn.started', { project_id: 'gone' })])
    assert.equal(descendants(unknown, (n) => n.type === 'a').length, 1, 'the session link only')
    assert.equal(textOf(byClass(unknown, 'timeline-project--plain')[0]), 'gone')
    const worktree = await render(t, [make('turn.started')], { scope: { kind: 'worktree', projectId: 'p1' } })
    assert.equal(byClass(worktree, 'timeline-project').length, 0)
    assert.equal(byClass(worktree, 'timeline-row-header').length, 1, 'the header stays, without the project')
})

test('the author label follows the wording, and an agent label links its session', async (t) => {
    const root = await render(t, [make('archived.started', { actor: 'agent', via: 'cli', actor_ref: 's1' })])
    assert.equal(textOf(byClass(root, 'timeline-author')[0]), 'by Fix the build (CLI)')
    assert.equal(descendants(byClass(root, 'timeline-author')[0], (n) => n.type === 'a').length, 1)
    assert.equal(byClass(root, 'timeline-row-header').length, 1)
})

test('no marker anywhere: the session of an author label and a parent carry none either', async (t) => {
    // The actor is a hidden AND archived session; the parent of session.created is archived too.
    const table = { ...sessions, parent: { ...sessions.parent, archived: true } }
    const labelled = await render(t, [make('archived.started', { actor: 'agent', via: 'cli', actor_ref: 'secret' })], { sessionTable: table })
    assert.equal(textOf(byClass(labelled, 'timeline-author')[0]), 'by Hidden one (CLI)')
    assert.equal(byClass(labelled, 'timeline-marker').length, 0)
    const created = await render(t, [make('session.created', { actor: 'user', via: 'ui', data: { origin: 'twicc', spawned_by: 'parent' } })], { sessionTable: table })
    assert.equal(byClass(created, 'timeline-marker').length, 0)
})

test('the parent of session.created is rendered as a session mention with its link', async (t) => {
    const root = await render(t, [make('session.created', { actor: 'user', via: 'ui', data: { origin: 'twicc', spawned_by: 'parent' } })])
    const wording = byClass(root, 'timeline-wording')[0]
    assert.equal(textOf(wording), 'Session created by The parent')
    const link = descendants(wording, (n) => n.type === 'a')[0]
    assert.equal(JSON.parse(link.props['data-to']).params.sessionId, 'parent')
})

test('the source tag, the background-work badge and the estimated time', async (t) => {
    const tag = await render(t, [make('title.changed', { data: { source: 'auto', new: 'T', old: null } })])
    assert.equal(textOf(byClass(tag, 'timeline-tag')[0]), 'automatic')
    const badge = await render(t, [make('turn.ended', { data: { background_work: { subagents: 2, shells: 0, monitors: 0, scheduled_wakeup_at: null, goal: false } } })])
    const [element] = descendants(badge, (n) => n.type === 'wa-badge')
    assert.deepEqual([textOf(element), element.props.variant, element.props.appearance], ['2 subagents', 'warning', 'outlined'])
    const estimated = await render(t, [make('process.ended', { reason: 'lost', precision: 'estimated' })])
    const [time] = byClass(estimated, 'timeline-time')
    assert.equal(textOf(time), '~14:32')
    assert.match(classesOf(time), /timeline-time--estimated/)
})

test('an error shows its first line, with the rest behind an expand control', async (t) => {
    const root = await render(t, [make('process.ended', { reason: 'error', data: { error: 'Boom\nat file.js:1' } })])
    assert.equal(textOf(byClass(root, 'timeline-error-line')[0]), 'Boom')
    assert.equal(byClass(root, 'timeline-error-rest').length, 0)
    const [toggle] = byClass(root, 'timeline-row__expand')
    assert.equal(textOf(toggle), 'Show more')
    assert.equal(toggle.props['aria-expanded'], 'false')
    toggle.props.onClick()
    await flush()
    const expandedToggle = byClass(root, 'timeline-row__expand')[0]
    assert.equal(expandedToggle.props['aria-expanded'], 'true')
    assert.equal(expandedToggle.props['aria-controls'], byClass(root, 'timeline-error-rest')[0].props.id)
    assert.equal(textOf(byClass(root, 'timeline-error-rest')[0]), 'at file.js:1')
    assert.equal(textOf(byClass(root, 'timeline-row__expand')[0]), 'Show less')
})

test('an error with a single line has no expand control', async (t) => {
    const root = await render(t, [make('process.ended', { reason: 'error', data: { error: 'Boom' } })])
    assert.equal(byClass(root, 'timeline-row__expand').length, 0)
    // The only button left is the header's "Only this session".
    assert.deepEqual(descendants(root, (n) => n.type === 'button').map(classesOf), ['timeline-row-only'])
})

test('strings from data are text, never markup', async (t) => {
    const hostile = '<img src=x onerror=alert(1)>'
    const root = await render(t, [
        make('title.changed', { data: { source: 'user', new: hostile, old: null }, actor: 'external', via: 'mcp_external', actor_name: hostile }),
    ])
    assert.equal(descendants(root, (n) => n.type === 'img').length, 0)
    assert.ok(textOf(byClass(root, 'timeline-wording')[0]).includes(hostile))
    assert.ok(textOf(byClass(root, 'timeline-author')[0]).includes(hostile))
    for (const file of ['TimelineEventRow.vue', 'TimelineSessionMention.vue', 'TimelineWordingPart.vue']) {
        assert.ok(!readFileSync(new URL(`./${file}`, import.meta.url), 'utf8').includes('v-html'), file)
    }
})

test('an unknown kind does not break the row', async (t) => {
    const root = await render(t, [make('plan.approved', { data: 'garbage' })])
    assert.equal(textOf(byClass(root, 'timeline-wording')[0]), 'plan.approved')
})

test('the layout CSS: a fixed time column that never wraps, a wrapping text column, a truncating header title', () => {
    const row = readFileSync(new URL('./TimelineEventLine.vue', import.meta.url), 'utf8')
    const line = declarationsOf(row, '.timeline-event-line')
    assert.equal(line.get('display'), 'grid')
    assert.equal(line.get('grid-template-columns'), 'var(--timeline-icon-width) minmax(0, 1fr) auto')
    assert.equal(line.get('align-items'), 'baseline')
    const time = declarationsOf(row, '.timeline-time')
    assert.equal(time.get('white-space'), 'nowrap')
    assert.equal(time.get('font-variant-numeric'), 'tabular-nums')
    // The width of the locale's widest time on every line (the sizer the panel sets), so the text column ends at the
    // same place: an invisible line of no height after the text (the time keeps the baseline of its text).
    assert.ok(!time.has('min-width'), 'no fixed width in characters: a 12-hour time is wider')
    const sizer = declarationsOf(row, '.timeline-time::after')
    assert.deepEqual(['content', 'display', 'height', 'overflow', 'visibility', 'white-space'].map((name) => sizer.get(name)),
        ['var(--timeline-time-sizer, "")', 'block', '0', 'hidden', 'hidden', 'pre'])
    const indented = declarationsOf(row, '.timeline-event-line--indented')
    assert.equal(indented.get('--timeline-line-indent'), 'calc(var(--timeline-provider-width, 0rem) + var(--timeline-provider-gap, 0rem))')
    assert.equal(indented.get('padding-inline-start'), 'var(--timeline-line-indent)')
    const mention = readFileSync(new URL('./TimelineSessionMention.vue', import.meta.url), 'utf8')
    const title = declarationsOf(mention, '.timeline-session--truncate .timeline-session-title')
    assert.deepEqual([title.get('overflow'), title.get('text-overflow'), title.get('white-space')], ['hidden', 'ellipsis', 'nowrap'])
})

test('the event icon and the badge follow the text baseline, so a badge never moves the icon off the first line', () => {
    const row = readFileSync(new URL('./TimelineEventLine.vue', import.meta.url), 'utf8')
    const icon = declarationsOf(row, '.timeline-row-icon')
    // Baseline-aligned like the text (the grid default), never pinned to the top of its cell.
    assert.ok(!icon.has('align-self') || icon.get('align-self') === 'baseline', icon.get('align-self'))
    assert.ok(!icon.has('height'))
    assert.equal(icon.get('inset-block-start'), '0.125em')
    // The main column is an inline flow: the badge sits on the text baseline (Web Awesome puts it on `middle`).
    assert.equal(declarationsOf(row, '.timeline-badge').get('vertical-align'), 'baseline')
})

test('the main column CSS: one inline flow, so its real spaces are rendered and copied; the error detail on its own line', () => {
    const row = readFileSync(new URL('./TimelineEventLine.vue', import.meta.url), 'utf8')
    const style = row.slice(row.indexOf('<style'))
    const main = declarationsOf(style, '.timeline-row-main')
    assert.ok(!main.has('display') && !main.has('column-gap'), 'no flex items: a copy would drop the spaces between them')
    assert.equal(main.get('overflow-wrap'), 'anywhere')
    // The old flex gaps, with the real space (about 4px) counted: 12px before the badge, 8px before the error toggle.
    for (const [selector, margin] of [['.timeline-badge', 'var(--wa-space-xs)'], ['.timeline-row__expand', 'var(--wa-space-2xs)']]) {
        assert.equal(declarationsOf(style, selector).get('margin-inline-start'), margin, selector)
    }
    assert.equal(declarationsOf(style, '.timeline-error').get('display'), 'block')
    // The expanded rest keeps the old 8px gap under the first line (the error is a block now, no flex gap).
    assert.match(style, /\n\.timeline-error-rest \{\s*margin: var\(--wa-space-xs\) 0 0;/)
    // The native <button> styles of Web Awesome (inline-flex, form-control height) are reset to an inline box of text.
    const toggle = declarationsOf(style, '.timeline-row__expand')
    assert.deepEqual([toggle.get('display'), toggle.get('vertical-align'), toggle.get('height')], ['inline-block', 'baseline', 'auto'])
})

test('a session row is a part of its block card: the head opens it, the last row closes it; an instance row is no card', async (t) => {
    const three = [
        make('turn.ended', { occurred_at: '2026-10-14T14:32:09.000000Z', data: { background_work: null } }),
        make('turn.started', { occurred_at: '2026-10-14T14:32:08.000000Z' }),
        make('turn.started'),
    ]
    const rowOf = async (events, index) => byClass(await render(t, events, { index }), 'timeline-row')[0]
    const flags = (row) => ['block', 'head', 'last'].filter((flag) => classesOf(row).split(' ').includes(`timeline-row--${flag}`))
    assert.deepEqual(flags(await rowOf(three, 0)), ['block', 'head'])
    assert.deepEqual(flags(await rowOf(three, 1)), ['block'])
    assert.deepEqual(flags(await rowOf(three, 2)), ['block', 'last'])
    assert.deepEqual(flags(await rowOf([make('turn.started')], 0)), ['block', 'head', 'last'], 'a one-row block is head and end at once')
    const instance = await rowOf([make('backend.started', { session_id: null, project_id: null, provider: null, actor: 'system', data: {} })], 0)
    assert.deepEqual(flags(instance), [])
    // The card is one box inside the row: the header line and the event line both sit in it.
    const head = await render(t, three, { index: 0 })
    const [card] = byClass(head, 'timeline-row-card')
    assert.equal(byClass(card, 'timeline-row-header').length, 1)
    assert.equal(byClass(card, 'timeline-event-line').length, 1)
})

test('the block card CSS: the shared block-card look, drawn in parts across the rows, without a state border', () => {
    const row = readFileSync(new URL('./TimelineEventRow.vue', import.meta.url), 'utf8')
    const style = row.slice(row.indexOf('<style'))
    const card = declarationsOf(style, '.timeline-row--block .timeline-row-card')
    assert.equal(card.get('background'), 'var(--block-card-bg)')
    assert.equal(card.get('border-inline'), '1px solid var(--block-card-border)')
    assert.equal(card.get('padding-inline'), 'var(--timeline-card-padding)')
    const head = declarationsOf(style, '.timeline-row--head .timeline-row-card')
    assert.equal(head.get('border-block-start'), '1px solid var(--block-card-border)')
    assert.equal(head.get('border-start-start-radius'), 'var(--wa-border-radius-m)')
    assert.equal(head.get('border-start-end-radius'), 'var(--wa-border-radius-m)')
    const last = declarationsOf(style, '.timeline-row--last .timeline-row-card')
    assert.equal(last.get('border-block-end'), '1px solid var(--block-card-border)')
    assert.equal(last.get('border-end-start-radius'), 'var(--wa-border-radius-m)')
    assert.equal(last.get('border-end-end-radius'), 'var(--wa-border-radius-m)')
    // The gap between two blocks is outside the cards, in the rows the scroller measures, half above the head and half
    // below the end: an instance line (or a day separator) between two blocks has the same space above and below it.
    assert.equal(declarationsOf(style, '.timeline-row--head').get('padding-block-start'), 'calc(var(--timeline-block-gap) / 2)')
    assert.equal(declarationsOf(style, '.timeline-row--last').get('padding-block-end'), 'calc(var(--timeline-block-gap) / 2)')
    const lineStyle = readFileSync(new URL('./TimelineEventLine.vue', import.meta.url), 'utf8')
    const instanceLine = declarationsOf(lineStyle.slice(lineStyle.indexOf('<style')), '.timeline-event-line')
    assert.equal(instanceLine.get('padding-block'), 'var(--wa-space-2xs)', 'the instance line itself is symmetric')
    assert.ok(!/\.timeline-row--instance \{[^}]*padding-block/.test(style), 'no extra space on one side of an instance row')
    // The Orchestration cards carry their state in a thick left border; a session block has no state to show.
    assert.ok(!/border-left|border-inline-start:\s*[2-9]/.test(style))
    // An instance row is no card, but its content lines up with the content of the cards.
    assert.equal(declarationsOf(style, '.timeline-row--instance .timeline-row-card').get('padding-inline'), 'calc(var(--timeline-card-padding) + 1px)')
})

test('the block-card look is declared once and the Orchestration cards read it', () => {
    const surfaces = readFileSync(new URL('../../styles/surfaces.css', import.meta.url), 'utf8')
    const dark = surfaces.slice(surfaces.indexOf('.wa-dark {'))
    const light = surfaces.slice(0, surfaces.indexOf('.wa-dark {'))
    for (const token of ['--block-card-bg:', '--block-card-border:', '--block-card-shadow:']) {
        assert.ok(light.includes(token), `light ${token}`)
        assert.ok(dark.slice(0, dark.indexOf('}')).includes(token), `dark ${token}`)
    }
    const orchestration = readFileSync(new URL('../orchestration/OrchestrationPanel.vue', import.meta.url), 'utf8')
    assert.match(orchestration, /--orch-card-bg: var\(--block-card-bg\);\s*--orch-card-border: var\(--block-card-border\);\s*--orch-card-shadow: var\(--block-card-shadow\);/)
    assert.ok(!orchestration.includes('.wa-dark .orchestration-panel'), 'no second definition of the look')
})

test('a rename: the two titles are values of their own, the arrow an icon with a hidden text, the source tag at the end of the wording', async (t) => {
    const root = await render(t, [make('title.changed', { data: { source: 'auto', old: 'Old one', new: 'New one' } })])
    const [wording] = byClass(root, 'timeline-wording')
    assert.ok(!textOf(wording).includes('"') && !textOf(wording).includes('->'), 'no raw quotes, no ASCII arrow')
    const [arrow] = descendants(wording, (n) => n.type === 'wa-icon')
    // No label: Web Awesome hides an unlabelled icon from assistive tech, the hidden text says "to" once.
    assert.deepEqual([arrow.props.name, arrow.props.label, classesOf(arrow)], ['arrow-right-long', undefined, 'timeline-arrow'])
    assert.deepEqual(byClass(wording, 'timeline-arrow-text').map(textOf), ['to'])
    // The copied text of the row is complete: real spaces (a no-break one before the arrow), the word of the arrow,
    // the tag. What a copy gets is the text content: the tag text is selectable (see the CSS test).
    assert.equal(textOf(byClass(root, 'timeline-row-main')[0]), 'Renamed Old one\u00A0to New one automatic')
    // The order of the flow: "Renamed", old, arrow, new, then the tag, all inside the wording; the last word and the
    // tag are glued in one box, so they wrap together.
    const flow = wording.children.filter((n) => n.type !== '#comment' && n.type !== '#text')
    const label = (n) => (n.type === 'wa-icon' ? 'arrow' : n.type === 'wa-tag' ? `tag:${textOf(n)}` : textOf(n))
    // The empty text nodes are the anchors of the tags' fragment.
    const glued = (n) => n.children.filter((c) => c.type !== '#comment' && !(c.type === '#text' && c.text === '')).map(label)
    assert.deepEqual(flow.map((n) => (classesOf(n) === 'timeline-tag-glue' ? glued(n) : label(n))),
        ['Renamed ', 'Old one', 'arrow', 'to', 'New ', ['one', ' ', 'tag:automatic']])
    assert.deepEqual(byClass(wording, 'timeline-value').map(textOf), ['Old one', 'New ', 'one'], 'the glued word keeps the value style')
    const [tag] = descendants(wording, (n) => n.type === 'wa-tag')
    assert.deepEqual([tag.props.size, tag.props.variant, classesOf(tag)], ['small', 'neutral', 'timeline-tag'])
    // No tag left outside the wording flow.
    assert.equal(byClass(byClass(root, 'timeline-row-main')[0], 'timeline-tag').length, 1)
})

test('a first title and a settings change use the same parts: the value, the same arrow icon', async (t) => {
    const titled = await render(t, [make('title.changed', { data: { source: 'user', old: null, new: 'Fresh' } })])
    assert.deepEqual(byClass(titled, 'timeline-value').map(textOf), ['Fresh'])
    assert.equal(byClass(titled, 'timeline-tag').length, 0)
    const settings = await render(t, [make('settings.changed', { data: { changes: { effort: ['low', 'high'] } } })])
    const [settingsWording] = byClass(settings, 'timeline-wording')
    const [arrow] = descendants(settingsWording, (n) => n.type === 'wa-icon')
    assert.deepEqual([arrow.props.name, arrow.props.label], ['arrow-right-long', undefined])
    assert.deepEqual(byClass(settingsWording, 'timeline-value').map(textOf), ['low', 'high'], 'the setting values are values, like titles')
    assert.equal(textOf(settingsWording), 'effort: low\u00A0to high')
})

test('the arrow never starts a line alone: a no-break space glues it to the old value, a normal space follows its word', async (t) => {
    const root = await render(t, [make('title.changed', { data: { source: 'user', old: 'Old', new: 'New' } })])
    const [wording] = byClass(root, 'timeline-wording')
    // The empty text nodes are the anchors of the arrow's fragment.
    const nodes = wording.children.filter((n) => n.type !== '#comment' && !(n.type === '#text' && n.text === ''))
    const at = nodes.findIndex((n) => n.type === 'wa-icon')
    const codePoints = (text) => [...text].map((c) => `U+${c.codePointAt(0).toString(16).toUpperCase().padStart(4, '0')}`).join(' ')
    assert.deepEqual(nodes.slice(at - 2, at + 4).map((n) => (n.type === '#text' ? `text:${codePoints(n.text)}` : textOf(n) || n.type)),
        ['Old', 'text:U+00A0', 'wa-icon', 'to', 'text:U+0020', 'New'])
})

test('the wording CSS: a value is set apart from the prose, the arrow sits on the text, the tag flows inline', () => {
    const row = readFileSync(new URL('./TimelineEventLine.vue', import.meta.url), 'utf8')
    const style = row.slice(row.indexOf('<style'))
    const part = readFileSync(new URL('./TimelineWordingPart.vue', import.meta.url), 'utf8')
    const partStyle = part.slice(part.indexOf('<style'))
    assert.equal(declarationsOf(partStyle, '.timeline-value').get('font-weight'), 'var(--wa-font-weight-bold)')
    const arrow = declarationsOf(partStyle, '.timeline-arrow')
    // Lighter than the bold values around it, so the eye finds the split between the two titles.
    assert.equal(arrow.get('color'), 'var(--wa-color-text-quiet)')
    assert.equal(arrow.get('opacity'), '0.6')
    assert.equal(arrow.get('vertical-align'), '-0.125em')
    assert.equal(arrow.get('margin-inline'), '0.2em', 'a little more room than the spaces alone')
    // Real spaces stay around the arrow and before the tag: a copy or a screen reader gets separate words. A no-break
    // space before the arrow (never alone at the start of a line), its word hidden from the eye after it.
    assert.match(part, /\{\{ '\\u00A0' \}\}<wa-icon name="arrow-right-long" auto-width class="timeline-arrow"><\/wa-icon><span class="timeline-arrow-text">\{\{ part\.label \}\}<\/span>\{\{ ' ' \}\}/)
    // The word is hidden in the flow, not taken out of it (no position): a copy reads it inline, without line breaks.
    const arrowText = declarationsOf(partStyle, '.timeline-arrow-text')
    assert.deepEqual(['display', 'width', 'height', 'overflow', 'clip-path', 'white-space'].map((name) => arrowText.get(name)),
        ['inline-block', '1px', '1px', 'hidden', 'inset(50%)', 'nowrap'])
    assert.ok(!arrowText.has('position'))
    // Web Awesome sets `user-select: none` on the tag and badge hosts; the page rule wins over `:host`, so a copy
    // keeps the source and the background work.
    for (const selector of ['.timeline-tag', '.timeline-badge']) {
        const rule = declarationsOf(style, selector)
        assert.deepEqual([rule.get('-webkit-user-select'), rule.get('user-select')], ['text', 'text'], selector)
    }
    assert.match(row, /<TimelineWordingPart v-if="wording\.last" :part="wording\.last" \/><template v-for="tag in vm\.tags" :key="tag">\{\{ ' ' \}\}<wa-tag /)
    const tag = declarationsOf(style, '.timeline-tag')
    assert.equal(tag.get('vertical-align'), 'baseline')
    assert.equal(tag.get('font-weight'), 'var(--wa-font-weight-normal)', 'not the semibold of an instance wording')
    assert.ok(!tag.has('display') && !tag.has('flex-basis'), 'never forced onto a line of its own')
    assert.equal(tag.get('margin-inline-start'), '0.25em', 'a little more room before the tag than the space alone')
    assert.equal(tag.get('padding-inline'), '0.5em', 'narrower than the Web Awesome default (0.75em)')
    assert.equal(declarationsOf(style, '.timeline-tag-glue').get('display'), 'inline-block')
    // What makes the glue safe on a narrow screen: a last word longer than the line still breaks inside it.
    assert.equal(declarationsOf(style, '.timeline-row-main').get('overflow-wrap'), 'anywhere')
})

// The text a copy of a whole row gets: every adjacent pair of the row is separated by a real space (the visible
// spaces of the inline flow, and spaces between the flex and grid items, which render nothing).
// The "Only this session" control is not copied (`user-select: none`, pinned in its CSS test).
const copiedText = (node) => (['timeline-row-only', 'app-tooltip'].some((name) => classesOf(node).split(' ').includes(name)) ? '' : node.type === '#comment' ? ''
    : `${node.text ?? ''}${node.children.map(copiedText).join('')}`)
const rowText = (root) => copiedText(byClass(root, 'timeline-row')[0])

test('the copied text of every row variant reads with a space between its pieces', async (t) => {
    const plain = await render(t, [make('process.started', { data: { cause: 'resume', hybrid: true } })])
    assert.equal(rowText(plain), 'Fix the build p1 Process resumed hybrid CLI 14:32')
    const rename = await render(t, [make('title.changed', { data: { source: 'auto', old: 'Old one', new: 'New one' } })])
    assert.equal(rowText(rename), 'Fix the build p1 Renamed Old one to New one automatic 14:32')
    const badge = await render(t, [make('turn.ended', { data: { background_work: { subagents: 2, shells: 0, monitors: 0, scheduled_wakeup_at: null, goal: false } } })])
    assert.match(rowText(badge), /^Fix the build p1 \S.* 2 subagents 14:32$/)
    const author = await render(t, [make('archived.started', { actor: 'agent', via: 'cli', actor_ref: 's1' })])
    assert.match(rowText(author), /^Fix the build p1 \S.* by Fix the build \(CLI\) 14:32$/)
    const error = await render(t, [make('process.ended', { reason: 'error', actor: 'agent', via: 'cli', actor_ref: 's1', data: { error: 'Boom\nat file.js:1' } })])
    assert.match(rowText(error), /^Fix the build p1 \S.* by Fix the build \(CLI\) Boom Show more 14:32$/)
    byClass(error, 'timeline-row__expand')[0].props.onClick()
    await flush()
    assert.match(rowText(error), / Boom Show less at file\.js:1 14:32$/)
    const following = await render(t, [make('turn.ended', { occurred_at: '2026-10-14T14:32:09.000000Z', data: { background_work: null } }), make('turn.started')], { index: 1 })
    assert.match(rowText(following), /^\S.* 14:32$/)
    const instance = await render(t, [make('backend.started', { session_id: null, project_id: null, provider: null, actor: 'system', data: {} })])
    assert.match(rowText(instance), /^\S.* 14:32$/)
    // No double spaces anywhere, and nothing glued to the time.
    for (const root of [plain, rename, badge, author, error, following, instance]) assert.doesNotMatch(rowText(root), / {2}|\S14:32/)
})

test('an automatic row shows an "automatic" tag glued to its wording, not a quiet author label', async (t) => {
    const root = await render(t, [make('process.started', { actor: 'system', via: null, data: { cause: 'resume', hybrid: true } })])
    const [wording] = byClass(root, 'timeline-wording')
    assert.deepEqual(byClass(wording, 'timeline-tag').map(textOf), ['hybrid CLI', 'automatic'])
    assert.equal(byClass(root, 'timeline-author').length, 0)
    assert.equal(textOf(byClass(root, 'timeline-row-main')[0]), 'Process resumed hybrid CLI automatic')
    // The cause says it: no "automatic" after "TwiCC shutdown".
    const stopped = await render(t, [make('process.ended', { actor: 'system', via: null, reason: 'shutdown' })])
    assert.deepEqual(byClass(stopped, 'timeline-tag').map(textOf), ['TwiCC shutdown'])
    assert.equal(byClass(stopped, 'timeline-author').length, 0)
})

test('the version of a TwiCC start is a tag, and several tags follow the last word in order', async (t) => {
    const started = await render(t, [make('backend.started', {
        session_id: null, project_id: null, provider: null, actor: 'system', data: { version: '1.97.0', machine_restarted: true },
    })])
    const [wording] = byClass(started, 'timeline-wording')
    assert.equal(textOf(wording), 'TwiCC started after a machine restart v1.97.0')
    assert.deepEqual(byClass(wording, 'timeline-tag').map(textOf), ['v1.97.0'])
    const titled = await render(t, [make('title.changed', { actor: 'system', via: null, data: { source: 'agent', new: 'B' } })])
    const [glue] = byClass(titled, 'timeline-tag-glue')
    const label = (n) => (n.type === 'wa-tag' ? `tag:${textOf(n)}` : textOf(n))
    assert.deepEqual(glue.children.filter((n) => n.type !== '#comment' && !(n.type === '#text' && n.text === '')).map(label),
        ['B', ' ', 'tag:from the provider', ' ', 'tag:automatic'])
})

test('the tool of a request is a value of the wording, like a title', async (t) => {
    const root = await render(t, [make('awaiting_input.started', { data: { request_type: 'tool_approval', tool_name: 'Bash' } })])
    const [wording] = byClass(root, 'timeline-wording')
    assert.equal(textOf(wording), 'Waiting for approval (Bash)')
    assert.deepEqual(byClass(wording, 'timeline-value').map(textOf), ['Bash'])
    assert.equal(byClass(root, 'timeline-tag').length, 0)
})

test('an author run: the label once, under its last row; a rule joins the rows; another author starts a new run', async (t) => {
    const by = (ref, time) => make('archived.started', { actor: 'agent', via: 'mcp_internal', actor_ref: ref, occurred_at: `2026-10-14T14:32:0${time}.000000Z` })
    // Newest first: rows 0 and 1 by "parent", row 2 by "s1".
    const events = [by('parent', 3), by('parent', 2), by('s1', 1)]
    const labels = []
    const runs = []
    for (const index of [0, 1, 2]) {
        const root = await render(t, events, { index })
        labels.push(byClass(root, 'timeline-author').map(textOf).join(''))
        runs.push(classesOf(byClass(root, 'timeline-event-line')[0]).match(/timeline-event-line--run-(\w+)/)?.[1] ?? null)
    }
    assert.deepEqual(labels, ['', 'by The parent (MCP)', 'by Fix the build (MCP)'])
    assert.deepEqual(runs, ['first', 'last', null])
})

test('the author run rule CSS: in the gap between the icons and the text, from the first row down to the author line', () => {
    const line = readFileSync(new URL('./TimelineEventLine.vue', import.meta.url), 'utf8')
    const style = line.slice(line.indexOf('<style'))
    const rule = declarationsOf(style, '.timeline-event-line--run-first::before,\n.timeline-event-line--run-middle::before,\n.timeline-event-line--run-last::before')
    assert.equal(rule.get('inset-inline-start'), 'calc(var(--timeline-line-indent) + var(--timeline-icon-width) + var(--wa-space-s) / 2 - 1px)')
    assert.equal(rule.get('inset-block'), '0')
    assert.equal(rule.get('border-inline-start'), '2px solid var(--block-card-border)')
    // The last row's own rule comes after the shared one, whose selector list ends with the same selector.
    const own = style.slice(style.indexOf('\n.timeline-event-line--run-first::before {'))
    assert.equal(declarationsOf(own, '.timeline-event-line--run-last::before').get('inset-block-end'), 'calc(var(--wa-space-2xs) + var(--timeline-author-height))')
    // The height the last row's rule leaves for the author line is the line's own height.
    assert.equal(declarationsOf(style, '.timeline-author').get('line-height'), '1.5')
    assert.equal(declarationsOf(style, '.timeline-event-line').get('--timeline-author-height'), 'calc(var(--wa-font-size-xs) * 1.5)')
})


test('message rows expand saved text without navigation, filter actions, or read-state changes', async t => {
    const fetch = t.mock.method(globalThis, 'fetch', () => { throw new Error('Message expansion must not request or update a session') })
    for (const [kind, label] of [['user_message.sent', 'User message'], ['assistant_message.sent', 'Assistant final message']]) {
        const emitted = []
        const event = make(kind, { data: { text: '**Saved** ' + 'x'.repeat(500) } })
        const root = await render(t, [event], { emitted, sessionFilterId: 's1' })
        assert.equal(textOf(byClass(root, 'timeline-wording')[0]), label)
        assert.equal(textOf(byClass(root, 'timeline-session-title')[0]), 'Fix the build')
        assert.equal(textOf(byClass(root, 'timeline-time')[0]), '14:32')
        const linksBefore = sessionLinks(root).map(link => link.props['data-to'])
        const [toggle] = descendants(root, node => node.type === 'button' && node.props['aria-expanded'] !== undefined)
        assert.ok(toggle, 'the existing row contains its saved-message control')
        assert.equal(toggle.props.type, 'button', 'native buttons support Enter and Space activation')
        assert.equal(toggle.props.tabindex, undefined, 'the control remains in native keyboard focus order')
        // A native button dispatches a click with detail 0 for Enter/Space activation.
        toggle.props.onClick({ detail: 0 })
        await flush()
        assert.equal(toggle.props['aria-expanded'], 'true')
        assert.equal(descendants(root, node => node.type === 'article')[0].props['data-markdown-source'], event.data.text)
        assert.deepEqual(emitted, [])
        assert.equal(fetch.mock.callCount(), 0)
        assert.deepEqual(sessionLinks(root).map(link => link.props['data-to']), linksBefore)
        assert.equal(onlyControls(root)[0].props['aria-pressed'], 'true')
        for (const node of descendants(root, () => true)) {
            assert.ok(!String(node.props.title ?? '').includes(event.data.text), 'full text stays out of titles')
            assert.ok(!String(node.props['data-to'] ?? '').includes(event.data.text), 'full text stays out of routes')
        }
    }
})


test('a recycled timeline row collapses the previous message before it shows the next event', async t => {
    const context = { sessions, labelsFor: () => null, time: { locale: 'en-GB', timeZone: 'UTC' } }
    const rowFor = event => buildTimelineRows([event], { sessions, scope: { kind: 'global' }, dayKeyOf: () => 'd', dayLabelOf: () => 'Day' })
        .find(row => row.type === 'event')
    const row = Vue.ref(rowFor(make('assistant_message.sent', { data: { text: '**First final**' } })))
    const { renderer, root } = makeRenderer()
    const app = renderer.createApp({ render: () => Vue.h(Row, { row: row.value, context }) })
    app.mount(root)
    t.after(() => app.unmount())
    await flush()
    const controls = () => descendants(root, node => node.type === 'button' && node.props['aria-expanded'] !== undefined)
    const firstControl = controls()[0]
    firstControl.props.onClick()
    await flush()
    assert.equal(descendants(root, node => node.type === 'article').length, 1)
    row.value = rowFor(make('assistant_message.sent', { data: { text: '**Second final**' } }))
    await flush()
    assert.equal(controls()[0], firstControl, 'the row reuses its existing message component')
    assert.equal(controls()[0].props['aria-expanded'], 'false')
    assert.equal(descendants(root, node => node.type === 'article').length, 0)
    assert.match(textOf(root), /Second final/)
    assert.doesNotMatch(textOf(root), /First final/)
})

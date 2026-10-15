import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import * as Vue from 'vue'

import { compileComponent, descendants, flush, makeRenderer } from '../../../tests/helpers/scrollerComponentHarness.js'
import * as describeEventModule from '../../utils/timeline/describeEvent.js'
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
const Row = compileComponent(new URL('./TimelineEventRow.vue', import.meta.url), {
    'vue-router': { RouterLink },
    '../../stores/data': { useDataStore: () => dataStore },
    '../../utils/timeline/describeEvent': describeEventModule,
    '../project/ProjectBadge.vue': ProjectBadge,
    './TimelineSessionMention.vue': Mention,
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

function textOf(node) {
    if (node.type === '#comment') return ''
    return `${node.text ?? ''}${node.children.map(textOf).join('')}`
}

/** The links inside the session mentions (the project name is a link too). */
const sessionLinks = (root) => byClass(root, 'timeline-session').flatMap((mention) => descendants(mention, (n) => n.type === 'a'))

async function render(t, events, { scope = { kind: 'global' }, index = 0, sessionTable = sessions } = {}) {
    const rows = buildTimelineRows(events, { sessions: sessionTable, scope, dayKeyOf: () => 'd', dayLabelOf: () => 'Day' })
        .filter((row) => row.type === 'event')
    const { renderer, root } = makeRenderer()
    const context = { sessions: sessionTable, labelsFor: () => null, time: { locale: 'en-GB', timeZone: 'UTC' } }
    const app = renderer.createApp({ render: () => Vue.h(Row, { row: rows[index], context }) })
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
    assert.equal(textOf(byClass(root, 'timeline-wording')[0]), 'Process resumed (hybrid CLI)')
    const [time] = byClass(root, 'timeline-time')
    assert.equal(textOf(time), '14:32')
    assert.match(time.props.title, /Precision: exact/)
})

test('the group head shows the provider icon and the session title as a link, once', async (t) => {
    const root = await render(t, [make('turn.started')])
    const link = descendants(root, (n) => n.type === 'a')[0]
    assert.equal(JSON.parse(link.props['data-to']).params.sessionId, 's1')
    assert.equal(textOf(link), 'Fix the build')
    assert.equal(descendants(root, (n) => n.type === 'i')[0].props['data-provider'], 'codex')
    assert.equal(byClass(root, 'timeline-identity').length, 1)
})

test('a following row is indented and has no identity', async (t) => {
    const root = await render(t, [make('turn.ended', { occurred_at: '2026-10-14T14:32:09.000000Z', data: { background_work: null } }), make('turn.started')], { index: 1 })
    assert.match(classesOf(byClass(root, 'timeline-row')[0]), /timeline-row--indented/)
    assert.equal(byClass(root, 'timeline-identity').length, 0)
})

test('a hidden and archived session is plain text with both markers; an archived one keeps its link', async (t) => {
    const hidden = await render(t, [make('turn.started', { session_id: 'secret', provider: 'claude_code' })])
    assert.equal(sessionLinks(hidden).length, 0)
    assert.equal(textOf(byClass(hidden, 'timeline-session-title--plain')[0]), 'Hidden one')
    assert.deepEqual(descendants(hidden, (n) => n.type === 'wa-icon' && String(n.props.class).includes('timeline-marker')).map((n) => n.props.name), ['box-archive', 'eye-slash'])
    const archivedOnly = { s1: { ...sessions.s1, archived: true } }
    const archived = await render(t, [make('turn.started')], { sessionTable: archivedOnly })
    assert.equal(sessionLinks(archived).length, 1)
    assert.deepEqual(descendants(archived, (n) => n.type === 'wa-icon' && String(n.props.class).includes('timeline-marker')).map((n) => n.props.name), ['box-archive'])
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
    const unknown = await render(t, [make('turn.started', { project_id: 'gone' })])
    assert.equal(descendants(unknown, (n) => n.type === 'a').length, 1, 'the session link only')
    assert.equal(textOf(byClass(unknown, 'timeline-project--plain')[0]), 'gone')
    const worktree = await render(t, [make('turn.started')], { scope: { kind: 'worktree', projectId: 'p1' } })
    assert.equal(byClass(worktree, 'timeline-project').length, 0)
})

test('the author label follows the wording, and an agent label links its session', async (t) => {
    const root = await render(t, [make('archived.started', { actor: 'agent', via: 'cli', actor_ref: 's1' })])
    assert.equal(textOf(byClass(root, 'timeline-author')[0]), 'by Fix the build (CLI)')
    assert.equal(descendants(byClass(root, 'timeline-author')[0], (n) => n.type === 'a').length, 1)
    assert.equal(byClass(root, 'timeline-identity').length, 1)
})

test('the markers are on the group head only: the session of an author label and a parent carry none', async (t) => {
    // The actor is a hidden AND archived session; the parent of session.created is archived too.
    const table = { ...sessions, parent: { ...sessions.parent, archived: true } }
    const labelled = await render(t, [make('archived.started', { actor: 'agent', via: 'cli', actor_ref: 'secret' })], { sessionTable: table })
    assert.equal(textOf(byClass(labelled, 'timeline-author')[0]), 'by Hidden one (CLI)')
    assert.equal(descendants(byClass(labelled, 'timeline-author')[0], (n) => String(n.props.class).includes('timeline-marker')).length, 0)
    const created = await render(t, [make('session.created', { actor: 'user', via: 'ui', data: { origin: 'twicc', spawned_by: 'parent' } })], { sessionTable: table })
    assert.equal(descendants(byClass(created, 'timeline-wording')[0], (n) => String(n.props.class).includes('timeline-marker')).length, 0)
    assert.equal(descendants(created, (n) => String(n.props.class).includes('timeline-marker')).length, 0, 'no marker anywhere: the head session is not archived')
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
    const [toggle] = descendants(root, (n) => n.type === 'wa-button')
    assert.equal(textOf(toggle), 'Show more')
    toggle.props.onClick()
    await flush()
    assert.equal(textOf(byClass(root, 'timeline-error-rest')[0]), 'at file.js:1')
    assert.equal(textOf(descendants(root, (n) => n.type === 'wa-button')[0]), 'Show less')
})

test('an error with a single line has no expand control', async (t) => {
    const root = await render(t, [make('process.ended', { reason: 'error', data: { error: 'Boom' } })])
    assert.equal(descendants(root, (n) => n.type === 'wa-button').length, 0)
})

test('strings from data are text, never markup', async (t) => {
    const hostile = '<img src=x onerror=alert(1)>'
    const root = await render(t, [
        make('title.changed', { data: { source: 'user', new: hostile, old: null }, actor: 'external', via: 'mcp_external', actor_name: hostile }),
    ])
    assert.equal(descendants(root, (n) => n.type === 'img').length, 0)
    assert.ok(textOf(byClass(root, 'timeline-wording')[0]).includes(hostile))
    assert.ok(textOf(byClass(root, 'timeline-author')[0]).includes(hostile))
    for (const file of ['TimelineEventRow.vue', 'TimelineSessionMention.vue']) {
        assert.ok(!readFileSync(new URL(`./${file}`, import.meta.url), 'utf8').includes('v-html'), file)
    }
})

test('an unknown kind does not break the row', async (t) => {
    const root = await render(t, [make('plan.approved', { data: 'garbage' })])
    assert.equal(textOf(byClass(root, 'timeline-wording')[0]), 'plan.approved')
})

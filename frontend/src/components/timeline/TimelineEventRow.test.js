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

test('the group head: a header line (provider icon, title link) above its own event line', async (t) => {
    const root = await render(t, [make('turn.started')])
    const [row] = byClass(root, 'timeline-row')
    assert.equal(byClass(root, 'timeline-row-header').length, 1)
    const [header] = byClass(root, 'timeline-row-header')
    const [line] = byClass(root, 'timeline-event-line')
    // The header and the event line are siblings in the card: the header is a line of its own, above the event.
    const [card] = row.children.filter((n) => n.type !== '#comment')
    assert.equal(classesOf(card), 'timeline-row-card')
    assert.deepEqual(card.children.filter((n) => n.type !== '#comment').map(classesOf), ['timeline-row-header', 'timeline-event-line timeline-event-line--indented'])
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

test('the mentions inside the wording and the author label keep the plain variant: no tooltip, no truncation', async (t) => {
    const created = await render(t, [make('session.created', { actor: 'user', via: 'ui', data: { origin: 'twicc', spawned_by: 'parent' } })])
    const labelled = await render(t, [make('archived.started', { actor: 'agent', via: 'cli', actor_ref: 'parent' })])
    for (const container of [byClass(created, 'timeline-wording')[0], byClass(labelled, 'timeline-author')[0]]) {
        const [mention] = byClass(container, 'timeline-session')
        assert.doesNotMatch(classesOf(mention), /timeline-session--truncate/)
        assert.equal(byClass(mention, 'timeline-session-title')[0].props.title ?? null, null)
    }
})

test('a following row has no header, and its event line has the same indent as the head one', async (t) => {
    const root = await render(t, [make('turn.ended', { occurred_at: '2026-10-14T14:32:09.000000Z', data: { background_work: null } }), make('turn.started')], { index: 1 })
    assert.match(classesOf(byClass(root, 'timeline-row')[0]), /timeline-row--indented/)
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
    const [toggle] = descendants(root, (n) => n.type === 'button')
    assert.equal(textOf(toggle), 'Show more')
    assert.equal(toggle.props['aria-expanded'], 'false')
    toggle.props.onClick()
    await flush()
    const expandedToggle = descendants(root, (n) => n.type === 'button')[0]
    assert.equal(expandedToggle.props['aria-expanded'], 'true')
    assert.equal(expandedToggle.props['aria-controls'], byClass(root, 'timeline-error-rest')[0].props.id)
    assert.equal(textOf(byClass(root, 'timeline-error-rest')[0]), 'at file.js:1')
    assert.equal(textOf(descendants(root, (n) => n.type === 'button')[0]), 'Show less')
})

test('an error with a single line has no expand control', async (t) => {
    const root = await render(t, [make('process.ended', { reason: 'error', data: { error: 'Boom' } })])
    assert.equal(descendants(root, (n) => n.type === 'button').length, 0)
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

/** The declarations of the first `selector { ... }` rule of a style block, as a property -> value map. */
function declarationsOf(source, selector) {
    const start = source.indexOf(`\n${selector} {`)
    assert.notEqual(start, -1, `rule ${selector}`)
    const body = source.slice(source.indexOf('{', start) + 1, source.indexOf('}', start))
    return new Map(body.replace(/\/\*[\s\S]*?\*\//g, '').split(';').map((d) => d.trim()).filter(Boolean)
        .map((d) => [d.slice(0, d.indexOf(':')).trim(), d.slice(d.indexOf(':') + 1).trim()]))
}

test('the layout CSS: a fixed time column that never wraps, a wrapping text column, a truncating header title', () => {
    const row = readFileSync(new URL('./TimelineEventRow.vue', import.meta.url), 'utf8')
    const line = declarationsOf(row, '.timeline-event-line')
    assert.equal(line.get('display'), 'grid')
    assert.equal(line.get('grid-template-columns'), 'var(--timeline-icon-width) minmax(0, 1fr) auto')
    assert.equal(line.get('align-items'), 'baseline')
    const time = declarationsOf(row, '.timeline-time')
    assert.equal(time.get('white-space'), 'nowrap')
    assert.equal(time.get('font-variant-numeric'), 'tabular-nums')
    assert.equal(declarationsOf(row, '.timeline-event-line--indented').get('padding-inline-start'),
        'calc(var(--timeline-provider-width) + var(--timeline-provider-gap))')
    const mention = readFileSync(new URL('./TimelineSessionMention.vue', import.meta.url), 'utf8')
    const title = declarationsOf(mention, '.timeline-session--truncate .timeline-session-title')
    assert.deepEqual([title.get('overflow'), title.get('text-overflow'), title.get('white-space')], ['hidden', 'ellipsis', 'nowrap'])
})

test('the event icon and the badge follow the text baseline, so a badge never moves the icon off the first line', () => {
    const row = readFileSync(new URL('./TimelineEventRow.vue', import.meta.url), 'utf8')
    const icon = declarationsOf(row, '.timeline-row-icon')
    // Baseline-aligned like the text (the grid default), never pinned to the top of its cell.
    assert.ok(!icon.has('align-self') || icon.get('align-self') === 'baseline', icon.get('align-self'))
    assert.ok(!icon.has('height'))
    assert.equal(icon.get('inset-block-start'), '0.125em')
    assert.equal(declarationsOf(row, '.timeline-badge').get('align-self'), 'baseline')
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
    const instanceLine = declarationsOf(style, '.timeline-event-line')
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

import test from 'node:test'
import assert from 'node:assert/strict'
import * as Vue from 'vue'
import { compileComponent, makeRenderer, descendants, flush, virtualScroller } from '../../../../tests/helpers/scrollerComponentHarness.js'
import * as description from '../../../utils/timeline/describeEvent.js'
import * as parts from '../../../utils/timeline/parts.js'
const Mention = compileComponent(new URL('../../timeline/TimelineSessionMention.vue', import.meta.url), {
    'vue-router': { RouterLink: { props: ['to'], setup: (_, { slots }) => () => Vue.h('a', slots.default?.()) }, useRoute: () => ({}) },
    '../../utils/sessionRoute': { sessionRouteLocation: session => ({ name: 'session', params: { sessionId: session.id } }) },
    '../ui/ProviderIcon.vue': { props: ['provider'], setup: props => () => Vue.h('i', props.provider) },
})
const WordingPart = compileComponent(new URL('../../timeline/TimelineWordingPart.vue', import.meta.url), { './TimelineSessionMention.vue': Mention })
const AppTooltip = { props: ['for', 'trigger'], setup: (_, { slots }) => () => Vue.h('span', { class: 'app-tooltip' }, slots.default?.()) }
const settings = Vue.reactive({ areMessageTimestampsShown: true, isTouchDevice: false })
const Line = compileComponent(new URL('../../timeline/TimelineEventLine.vue', import.meta.url), {
    '../../stores/settings': { useSettingsStore: () => settings }, '../ui/AppTooltip.vue': AppTooltip, './TimelineSessionMention.vue': Mention,
    '../../utils/timeline/parts': parts, './TimelineWordingPart.vue': WordingPart,
})
const Notice = compileComponent(new URL('./SessionEventNotice.vue', import.meta.url), {
    '../../../stores/settings': { useSettingsStore: () => settings },
    '../../../utils/timeline/describeEvent.js': description, '../../timeline/TimelineEventLine.vue': Line,
})
const event = (kind, extra = {}) => ({ id: 1, session_id: 's', kind, occurred_at: '2026-10-27T14:32:00.000000Z', precision: 'exact', actor: 'user', data: {}, ...extra })
const text = n => n.type === '#comment' ? '' : `${n.text ?? ''}${n.children.map(text).join('')}`
async function mount(t, data, context = Vue.shallowReactive({ sessions: {}, time: { locale: 'en-GB', timeZone: 'UTC' } }), inScroller = false) {
    const { renderer, root } = makeRenderer(), Scroller = inScroller ? virtualScroller() : null
    const rows = [{ lineNum: 'event:1', event: data }]
    const app = renderer.createApp({ setup: () => () => inScroller
        ? Vue.h(Scroller, { items: rows, itemKey: row => row.lineNum, itemMemo: () => [] }, { default: ({ item }) => Vue.h(Notice, { event: item.event, context }) })
        : Vue.h(Notice, { event: data, context }) })
    app.mount(root); t.after(() => app.unmount()); await flush(); return root
}
test('all selected families and unknown payloads render individual text notices with time', async t => {
    for (const kind of ['session.created', 'title.changed', 'settings.changed', 'process.started', 'process.ended', 'awaiting_input.started', 'awaiting_input.ended',
        'archived.started', 'archived.ended', 'hidden.started', 'hidden.ended', 'muted.started', 'muted.ended', 'pinned.started', 'pinned.ended', 'future.changed']) {
        const root = await mount(t, event(kind, { data: null }))
        assert.equal(descendants(root, n => String(n.props.class).includes('timeline-time'))[0].text, '14:32')
        assert.equal(descendants(root, n => n.type === 'details' || n.type === 'summary').length, 0)
        assert.equal(descendants(root, n => String(n.props.class).includes('timeline-event-line')).length > 0, true)
        assert.equal(descendants(root, n => String(n.props.class).includes('timeline-row-card')).length, 0)
        if (kind === 'future.changed') assert.match(text(root), /future.changed/)
    }
})
test('estimated time, full rename values and multiline error remain accessible as plain text', async t => {
    const value = `<script>alert('x')</script>${'long title '.repeat(30)}`
    const root = await mount(t, event('title.changed', { precision: 'estimated', data: { old: value, new: 'next' } }))
    assert.equal(descendants(root, n => String(n.props.class).includes('timeline-time'))[0].text, '~14:32')
    assert.ok(descendants(root, n => String(n.props.title ?? '').includes(value)).length > 0, 'the full title is in the tooltip'); assert.equal(descendants(root, n => n.type === 'script').length, 0)
    const error = await mount(t, event('process.ended', { reason: 'error', data: { error: 'first\nsecond' } }))
    assert.ok(text(error).includes('first')); assert.equal(descendants(error, n => String(n.props.class).includes('timeline-row__expand')).length, 1)
})
test('stable reactive context updates actor links behind scroller memo', async t => {
    const context = Vue.shallowReactive({ sessions: { actor: { title: 'Before', project_id: 'p', provider: 'codex' } } })
    const root = await mount(t, event('settings.changed', { actor: 'agent', via: 'mcp_internal', actor_ref: 'actor' }), context, true)
    assert.ok(text(root).includes('Before')); assert.equal(descendants(root, n => n.type === 'a').length, 1)
    context.sessions = { actor: { title: 'After', project_id: 'p', provider: 'claude_code', hidden: true } }; await flush()
    assert.ok(text(root).includes('After')); assert.equal(descendants(root, n => n.type === 'a').length, 0)
})
test('the time follows the message timestamps setting; the wording is not semibold', async t => {
    settings.areMessageTimestampsShown = false
    t.after(() => { settings.areMessageTimestampsShown = true })
    const root = await mount(t, event('turn.started'))
    assert.equal(descendants(root, n => String(n.props.class).includes('timeline-time')).length, 0)
    assert.equal(descendants(root, n => String(n.props.class).includes('timeline-event-line--instance')).length, 0)
    settings.areMessageTimestampsShown = true
    const shown = await mount(t, event('turn.started'))
    assert.equal(descendants(shown, n => String(n.props.class).includes('timeline-time')).length, 1)
    assert.equal(descendants(shown, n => n.props?.class === 'app-tooltip').length, 1)
})

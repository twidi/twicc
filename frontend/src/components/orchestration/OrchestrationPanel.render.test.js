import test from 'node:test'
import assert from 'node:assert/strict'
import * as Vue from 'vue'
import { compileComponent, makeRenderer, flush, row, descendants, deferred } from '../../../tests/helpers/scrollerComponentHarness.js'
import * as view from '../../utils/orchestrationView.js'
import * as links from '../../utils/agentLinkIndex.js'
import * as metrics from '../../utils/agentTreeMetrics.js'
import * as dates from '../../utils/date.js'
import * as background from '../../utils/backgroundWork.js'
import * as labels from '../../utils/agentLabel.js'
import * as keys from './orchestrationKeys.js'
import * as visibility from './useVisibleComputed.js'

const START = '2026-10-07T10:00:00Z'
const END = '2026-10-07T10:01:00Z'
const NOW = Date.parse('2026-10-07T10:02:00Z')
const Cost = compileComponent(new URL('../ui/CostDisplay.vue', import.meta.url))
const Bar = compileComponent(new URL('./OrchestrationTimeBar.vue', import.meta.url))
const AnnotationLevel = compileComponent(new URL('./AnnotationTreeLevel.vue', import.meta.url))
const Annotations = compileComponent(new URL('./OrchestrationAnnotations.vue', import.meta.url), {
    '@vueuse/core': { useResizeObserver: () => {} }, './AnnotationTreeLevel.vue': AnnotationLevel,
    '../../directives/vPopoverFocusFix': { vPopoverFocusFix: {} }, '../../utils/orchestrationView': view,
})
const inert = { render: () => null }
const route = { name: 'project', params: { projectId: 'p' }, query: {} }
const helpers = { getEffectiveContextMax: () => 1000, getChoiceLabel: () => '1K', getSummaryParts: () => [] }

function fixture(t, { agents = false, active = true, both = false } = {}) {
    t.mock.timers.enable({ apis: ['setInterval', 'Date'], now: NOW })
    const local = Vue.reactive(links.agentLinkState())
    const state = Vue.reactive({ sessions: { root: { provider: 'claude', created_at: START, last_new_content_at: END } }, processStates: {} })
    let treeReads = 0, sessionReads = 0, geometryReads = 0, panelUpdates = 0
    const store = {
        getSession: id => { sessionReads++; return state.sessions[id] }, getSessionProvider: () => 'claude',
        getProcessState: id => state.processStates[id], getEffectiveContextMax: () => 1000,
        getAgentLinkInfo: id => local.agentLinkIndex[id],
        hasSubagents: () => links.hasTreeAgents(local, 'root'), getAgentTree: (root, previous) => { treeReads++; return links.buildAgentTree(local, root, previous) },
        getOrchestrationActivity: () => null, areSubagentsLoaded: () => true,
        fetchSubagentsState: async () => {},
    }
    if (agents) {
        for (const [id, owner] of [['live', 'root'], ['branch', 'root'], ['finished', 'branch']]) {
            links.setAgentLink(local, owner, `spawn-${id}`, { agentId: id, rootSessionId: 'root',
                startedAt: START, stoppedAt: END, metrics: { totalCost: 1, userMessageCount: 2 }, displayName: id })
        }
        state.processStates.live = { state: 'assistant_turn', synthetic: true }
    }
    const node = id => ({ id, session: { project_id: 'p', provider: 'claude', title: id, created_at: START,
        last_new_content_at: END, total_cost: 1, user_message_count: 2 }, process: { state: id === 'live' ? 'assistant_turn' : 'dead' }, subtree_total_cost: 1 })
    let payload = { nodes: ['root', 'live', 'branch', 'finished'].map(node),
        tree: { id: 'root', children: [{ id: 'live', children: [] }, { id: 'branch', children: [{ id: 'finished', children: [] }] }] } }
    let reads = 0
    t.mock.method(globalThis, 'fetch', async () => { reads++; return { ok: true, json: async () => structuredClone(payload) } })
    const settings = Vue.reactive({ areCostsShown: true })
    const frames = new Map()
    let frameId = 0
    const oldRequest = globalThis.requestAnimationFrame, oldCancel = globalThis.cancelAnimationFrame
    globalThis.requestAnimationFrame = callback => { frames.set(++frameId, callback); return frameId }
    globalThis.cancelAnimationFrame = id => frames.delete(id)
    t.after(() => { globalThis.requestAnimationFrame = oldRequest; globalThis.cancelAnimationFrame = oldCancel })
    const Switch = compileComponent(new URL('../ui/SegmentedControl.vue', import.meta.url), {
        '../../composables/useGlideInk': { useGlideInk: () => {} },
    })
    const common = {
        'vue-router': { useRoute: () => route }, '../../stores/settings': { useSettingsStore: () => settings },
        '../ui/CostDisplay.vue': Cost, '../ui/AppTooltip.vue': inert, './OrchestrationTimeBar.vue': Bar,
        '../../providers': { getProviderHelpers: () => helpers, getProviderStore: () => ({}) },
        '../../utils/date': dates, '../../utils/sessionRoute': { sessionRouteLocation: session => ({ name: 'project', params: { sessionId: session.id } }) },
        '../../utils/orchestrationView': { ...view, computeTreeGeometry: (...args) => { geometryReads++; return view.computeTreeGeometry(...args) } }, './orchestrationKeys.js': keys, './useVisibleComputed.js': visibility,
    }
    const SessionNode = compileComponent(new URL('./OrchestrationNode.vue', import.meta.url), {
        ...common, '../message/AgentSettingsSummaryView.vue': inert, '../project/ProjectBadge.vue': inert,
        './OrchestrationAnnotations.vue': Annotations, '../../utils/backgroundWork': background,
    })
    const AgentNode = compileComponent(new URL('./AgentTreeNode.vue', import.meta.url), {
        ...common, '../../stores/data': { useDataStore: () => store }, '../ui/ProviderIcon.vue': inert,
        '../../utils/agentLabel': labels, '../../utils/agentTreeMetrics': metrics,
    })
    const Summary = compileComponent(new URL('./OrchestrationSummary.vue', import.meta.url), common)
    const Panel = compileComponent(new URL('./OrchestrationPanel.vue', import.meta.url), {
        ...common, '@vueuse/core': { useResizeObserver: () => {} },
        '../../stores/data': { useDataStore: () => store }, './OrchestrationNode.vue': SessionNode,
        './AgentTreeNode.vue': AgentNode, './OrchestrationSummary.vue': Summary,
        './OrchestrationTabActivity.vue': inert, '../ui/SegmentedControl.vue': Switch,
        '../../utils/agentTreeMetrics': metrics,
    })
    const { renderer, root } = makeRenderer(140, element => {
        element.style = {}; element.offsetTop = 0; element.clientWidth = 400
        element.querySelectorAll = selector => descendants(element, n =>
            String(n.props.class ?? '').split(' ').includes(selector.slice(1).split(':')[0])
            && (!selector.includes(':not') || !String(n.props.class).includes('is-hidden')))
        element.querySelector = selector => element.querySelectorAll(selector)[0] ?? null
    })
    const instances = new Map(), updates = new Map()
    const props = Vue.reactive({ sessionId: 'root', projectId: 'p', hasSpawnTree: both || !agents, active })
    const app = renderer.createApp({ render: () => Vue.h(Panel, props) })
    app.component('RouterLink', row)
    app.component('OrchestrationNode', SessionNode)
    app.component('AgentTreeNode', AgentNode)
    app.component('AnnotationTreeLevel', AnnotationLevel)
    app.mixin({
        mounted() { if (this.$props.node) instances.set(this.$props.node.id, this) },
        beforeUpdate() { if (this.$options.__name === 'OrchestrationPanel') panelUpdates++; if (this.$props.node) { const id = this.$props.node.id; updates.set(id, (updates.get(id) ?? 0) + 1) } },
    })
    app.mount(root)
    let disposed = false
    const unmount = () => { if (!disposed) { app.unmount(); disposed = true } }
    t.after(unmount)
    return { state, local, root, props, store, settings, updates, instances, unmount, get reads() { return reads },
        get treeReads() { return treeReads }, get sessionReads() { return sessionReads },
        get geometryReads() { return geometryReads }, get panelUpdates() { return panelUpdates },
        async switchView(value) {
            const group = descendants(root, n => n.type === 'wa-radio-group')[0]
            group.value = value; group.props.onChange({ target: group, currentTarget: group })
            await flush()
            for (let i = 0; i < 2; i++) {
                const callbacks = [...frames.values()]; frames.clear()
                for (const callback of callbacks) callback(Date.now())
                await flush()
            }
        },
        change(id, fields) { Object.assign(payload.nodes.find(n => n.id === id).session, fields) },
        process(id, value) { payload.nodes.find(n => n.id === id).process = value },
        remove(id) { payload.nodes = payload.nodes.filter(n => n.id !== id); payload.tree.children = payload.tree.children.filter(n => n.id !== id) },
        async refresh() { descendants(root, n => n.type === 'wa-button' && n.props.title === 'Refresh')[0].props.onClick(); await flush() },
        text(id) { return text(instances.get(id).$el.children[0]) },
        bar(id) { return descendants(instances.get(id).$el.children[0], n => String(n.props.class).includes('otime-fill'))[0]?.props.style },
        toggle(id) { descendants(instances.get(id).$el.children[0], n => n.type === 'button' && n.props.class === 'ocard-toggle')[0].props.onClick() },
    }
}
function text(node) { return [node.text, ...node.children.map(text)].join(' ') }

test('a session cost update does not render unchanged sibling cards', async t => {
    const f = fixture(t); await flush(); f.updates.clear()
    f.change('live', { total_cost: 3 }); await f.refresh()
    assert.ok(f.text('live').includes('3.00'))
    assert.equal(f.updates.get('finished') ?? 0, 0)
    assert.equal(f.updates.get('branch') ?? 0, 0)
})

test('session clock ticks leave fixed stopped cards untouched while working durations advance', async t => {
    const f = fixture(t); await flush(); const before = f.text('live'); f.updates.clear()
    t.mock.timers.tick(30_000); await flush()
    assert.notEqual(f.text('live'), before)
    assert.equal(f.updates.get('finished') ?? 0, 0)
})

test('subagent clock ticks leave fixed stopped cards untouched while working durations advance', async t => {
    const f = fixture(t, { agents: true }); await flush(); const before = f.text('live'); f.updates.clear()
    t.mock.timers.tick(30_000); await flush()
    assert.notEqual(f.text('live'), before)
    assert.equal(f.updates.get('finished') ?? 0, 0)
})

test('subagent link changes retain other cards and update the changed metrics', async t => {
    const f = fixture(t, { agents: true }); await flush(); f.updates.clear()
    links.setAgentLink(f.local, 'root', 'spawn-live', { ...f.local.agentLinkIndex.live, metrics: { totalCost: 4, userMessageCount: 2 } })
    await flush()
    assert.ok(f.text('live').includes('4.00'))
    assert.equal(f.updates.get('branch') ?? 0, 0)
    assert.equal(f.updates.get('finished') ?? 0, 0)
})

test('stopped session bars adjust at the next topology snapshot', async t => {
    const f = fixture(t); await flush(); const before = f.bar('branch')
    t.mock.timers.tick(30_000); await flush()
    assert.notDeepEqual(f.bar('branch'), before)
    assert.equal(f.bar('finished').width, 'max(100%, 10px)')
})

test('session stop and restart preserve collapsed branches and restart the clock', async t => {
    const f = fixture(t); await flush(); f.toggle('branch'); await flush()
    f.process('live', { state: 'dead' }); await f.refresh()
    const stopped = f.text('live'), reads = f.reads; f.updates.clear()
    t.mock.timers.tick(60_000); await flush()
    assert.equal(f.reads, reads)
    assert.equal(f.text('live'), stopped)
    assert.equal(f.updates.get('live') ?? 0, 0)
    f.process('live', { state: 'assistant_turn' }); await f.refresh()
    assert.ok(f.text('live').includes('now'))
    assert.equal(descendants(f.instances.get('branch').$el, n => n.props.class === 'onode-kids').length, 0)
    const restarted = f.text('live')
    t.mock.timers.tick(30_000); await flush()
    assert.notEqual(f.text('live'), restarted)
})

test('subagent stop restart and live session metrics remain reactive', async t => {
    const f = fixture(t, { agents: true }); await flush()
    delete f.state.processStates.live
    await flush(); const stopped = f.text('live'); f.updates.clear()
    t.mock.timers.tick(60_000); await flush()
    assert.equal(f.text('live'), stopped)
    assert.equal(f.updates.get('live') ?? 0, 0)
    f.state.sessions.live = { total_cost: 7, user_message_count: 9, context_usage: 350, model: { family: 'opus', version: '4.7' } }
    await flush()
    assert.ok(f.text('live').includes('7.00'))
    assert.ok(f.text('live').includes('35%'))
    assert.ok(f.text('live').includes('opus 4.7'))
    f.state.processStates.live = { state: 'assistant_turn', synthetic: true }
    await flush(); const restarted = f.text('live')
    t.mock.timers.tick(30_000); await flush()
    assert.notEqual(f.text('live'), restarted)
})

test('session annotations update and topology removal removes the card', async t => {
    const f = fixture(t); await flush()
    f.change('branch', { annotations: { team: 'alpha' }, hidden: true }); await f.refresh()
    assert.ok(f.text('branch').includes('alpha'))
    assert.ok(String(f.instances.get('branch').$el.children[0].props.class).includes('is-hidden'))
    f.change('branch', { annotations: { team: 'beta' }, hidden: false }); await f.refresh()
    assert.ok(f.text('branch').includes('beta'))
    assert.ok(!f.text('branch').includes('alpha'))
    f.remove('live'); await f.refresh()
    assert.equal(f.instances.get('live').$.isUnmounted, true)
})

test('inactive orchestration stops polling and updates its snapshot on reactivation', async t => {
    const f = fixture(t); await flush(); f.props.active = false; await flush()
    const reads = f.reads
    f.change('live', { title: 'Updated while inactive' })
    t.mock.timers.tick(60_000); await flush()
    assert.equal(f.reads, reads)
    f.props.active = true; await flush()
    assert.ok(f.text('live').includes('Updated while inactive'))
    assert.equal(f.reads, reads + 1)
})


test('an initially hidden panel does not build cards or read presentation data', async t => {
    const f = fixture(t, { agents: true, active: false }); await flush()
    assert.equal(f.treeReads, 0)
    assert.equal(f.sessionReads, 0)
    assert.equal(f.instances.size, 0)
    f.props.active = true; await flush()
    assert.ok(f.text('live').includes('live'))
})

test('hidden subagent cards suspend store calculations and resume with current data', async t => {
    const f = fixture(t, { agents: true }); await flush()
    f.toggle('branch'); await flush()
    f.props.active = false; await flush(); f.updates.clear()
    const before = f.text('live'), treeReads = f.treeReads, sessionReads = f.sessionReads, panelUpdates = f.panelUpdates
    f.state.sessions.live = { total_cost: 8, context_usage: 450 }
    links.setAgentLink(f.local, 'root', 'spawn-live', { ...f.local.agentLinkIndex.live, displayName: 'Updated agent', metrics: { totalCost: 6 } })
    links.setAgentLink(f.local, 'root', 'spawn-new', { agentId: 'new', rootSessionId: 'root', startedAt: START })
    delete f.state.processStates.live
    await flush(); t.mock.timers.tick(60_000); await flush()
    assert.equal(f.text('live'), before)
    assert.equal(f.updates.size, 0)
    assert.equal(f.treeReads, treeReads)
    assert.equal(f.sessionReads, sessionReads)
    assert.equal(f.panelUpdates, panelUpdates)
    // A second event verifies that hidden computeds drop their previous subscriptions.
    f.state.sessions.live.total_cost = 10
    await flush()
    assert.equal(f.updates.size, 0)
    assert.equal(f.sessionReads, sessionReads)
    f.state.sessions.live.total_cost = 8
    f.props.active = true; await flush()
    assert.ok(f.text('live').includes('8.00'))
    assert.ok(f.text('live').includes('45%'))
    assert.ok(f.text('live').includes('Updated agent'))
    assert.ok(!f.text('live').includes('now'))
    assert.ok(f.instances.has('new'))
    assert.equal(descendants(f.instances.get('branch').$el, n => n.props.class === 'onode-kids').length, 0)
})

test('subagent duration ticks do not change the snapshot scale of stopped bars', async t => {
    const f = fixture(t, { agents: true }); f.state.processStates.root = { state: 'assistant_turn' }; await flush()
    const before = f.bar('branch'), duration = f.text('live'); f.updates.clear()
    t.mock.timers.tick(30_000); await flush()
    assert.deepEqual(f.bar('branch'), before)
    assert.notEqual(f.text('live'), duration)
    assert.equal(f.updates.get('branch') ?? 0, 0)
    await f.refresh()
    assert.notDeepEqual(f.bar('branch'), before)
})

test('switching to sessions suspends the mounted subagent view and retains scroll', async t => {
    const f = fixture(t, { agents: true, both: true }); await flush(); await f.switchView('agents')
    f.toggle('branch'); await flush()
    const content = descendants(f.root, n => n.props.class === 'orch-content')[0]
    content.scrollTop = 120
    await f.switchView('sessions'); f.updates.clear()
    const agent = f.instances.get('live'), before = f.text('live'), treeReads = f.treeReads
    f.state.sessions.live = { total_cost: 9 }
    await flush()
    assert.equal(f.text('live'), before)
    assert.equal(f.updates.size, 0)
    assert.equal(f.treeReads, treeReads)
    await f.switchView('agents')
    assert.equal(f.instances.get('live'), agent)
    assert.ok(f.text('live').includes('9.00'))
    assert.equal(content.scrollTop, 120)
    assert.equal(descendants(f.instances.get('branch').$el, n => n.props.class === 'onode-kids').length, 0)
})

test('the sessions poll stops while the subagent view is selected', async t => {
    const f = fixture(t, { agents: true, both: true }); await flush(); await f.switchView('agents')
    const reads = f.reads
    t.mock.timers.tick(60_000); await flush()
    assert.equal(f.reads, reads)
    await f.switchView('sessions')
    assert.equal(f.reads, reads + 1)
})

test('a topology response arriving after hiding the panel cannot replace its snapshot', async t => {
    const f = fixture(t); await flush()
    const pending = deferred()
    t.mock.method(globalThis, 'fetch', () => pending.promise)
    await f.refresh()
    const before = f.text('live')
    f.props.active = false; await flush(); f.updates.clear()
    pending.resolve({ ok: true, json: async () => ({ nodes: [], tree: null }) })
    await flush()
    assert.equal(f.text('live'), before)
    assert.equal(f.instances.get('live').$.isUnmounted, false)
    assert.equal(f.updates.size, 0)
})


test('root streaming does not recompute subagent bar geometry while the root is working', async t => {
    const f = fixture(t, { agents: true }); f.state.processStates.root = { state: 'assistant_turn' }; await flush()
    const reads = f.geometryReads, before = f.bar('branch')
    f.state.sessions.root.last_new_content_at = '2026-10-07T10:02:01Z'
    await flush()
    assert.equal(f.geometryReads, reads)
    assert.deepEqual(f.bar('branch'), before)
})

test('a newer topology snapshot wins when an earlier response arrives last', async t => {
    const f = fixture(t); await flush()
    const pending = deferred()
    t.mock.method(globalThis, 'fetch', () => pending.promise)
    await f.refresh()
    f.props.active = false; await flush()
    t.mock.method(globalThis, 'fetch', async () => ({ ok: true, json: async () => ({ nodes: [], tree: null }) }))
    f.props.active = true; await flush()
    pending.resolve({ ok: true, json: async () => ({ nodes: [{ id: 'stale' }], tree: { id: 'root', children: [] } }) })
    await flush()
    assert.ok(text(f.root).includes('No orchestration data.'))
    assert.equal(f.instances.get('live').$.isUnmounted, true)
})


test('a live link discovered during the first refresh receives historical metrics', async t => {
    const f = fixture(t, { agents: true, active: false }); await flush()
    links.clearAgentLinks(f.local, 'root'); links.clearAgentLinks(f.local, 'branch')
    const pending = deferred()
    const snapshot = [{ agent_id: 'late', owner_session_id: 'root', tool_use_id: 'spawn-late', started_at: START,
        stopped_at: END, display_name: 'Late agent', total_cost: 12, user_message_count: 2, context_usage: 300 }]
    let reads = 0
    f.store.fetchSubagentsState = async () => {
        const token = links.beginAgentFetch(f.local, 'root')
        const payload = ++reads === 1 ? await pending.promise : snapshot
        links.applyAgentSnapshot(f.local, 'root', payload, token)
    }
    f.props.active = true; await flush()
    links.setAgentLink(f.local, 'root', 'spawn-late', { agentId: 'late', rootSessionId: 'root', startedAt: START })
    await flush()
    pending.resolve(snapshot); await flush()
    assert.ok(f.text('late').includes('12.00'))
    assert.ok(f.text('late').includes('30%'))
    assert.ok(f.text('late').includes('Late agent'))
})


test('unmount cancels a queued metrics refresh after a late link', async t => {
    const f = fixture(t, { agents: true, active: false }); await flush()
    links.clearAgentLinks(f.local, 'root'); links.clearAgentLinks(f.local, 'branch')
    const pending = deferred()
    let reads = 0
    f.store.fetchSubagentsState = async () => { reads++; await pending.promise }
    f.props.active = true; await flush()
    links.setAgentLink(f.local, 'root', 'spawn-late', { agentId: 'late', rootSessionId: 'root', startedAt: START })
    await flush(); f.unmount()
    pending.resolve(); await flush()
    assert.equal(reads, 1)
})

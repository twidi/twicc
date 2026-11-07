import test from 'node:test'
import { createAppI18n } from '../i18n/index.js'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { createRenderer, ref, computed, watch, onMounted, onUnmounted, nextTick } from 'vue'
import { createStatisticsRefresh, refreshStatisticsHome } from './statisticsRefresh.js'

const tick = () => new Promise(resolve => setImmediate(resolve))

test('real home actions coalesce requests and publish the trailing response', async () => {
    const source = readFileSync(new URL('../stores/data.js', import.meta.url), 'utf8')
    const start = source.indexOf('        loadHomeData() {')
    const end = source.indexOf('        /**', source.indexOf('        async _fetchHomeData()', start))
    const gates = []
    const apiFetch = url => {
        assert.equal(url, '/api/home/')
        return new Promise(resolve => gates.push(resolve))
    }
    const actions = new Function('refreshStatisticsHome', 'apiFetch', `return {${source.slice(start, end)}}`)(
        refreshStatisticsHome, apiFetch)
    const store = { ...actions, projects: {}, weeklyActivity: {}, localState: { projectsList: {} } }
    const done = store.loadHomeData()
    store.loadHomeData()
    await tick()
    assert.equal(gates.length, 1)
    store.loadHomeData()
    gates.shift()({ ok: true, json: async () => ({ projects: [{ id: 'p', total_cost: 1 }] }) })
    await tick()
    assert.equal(gates.length, 1)
    gates.shift()({ ok: true, json: async () => ({ projects: [{ id: 'p', total_cost: 7,
        weekly_activity: [{ cost: 7 }] }], global_weekly_activity: [{ cost: 7 }] }) })
    await done
    assert.equal(store.projects.p.total_cost, 7)
    assert.deepEqual(store.weeklyActivity.p, [{ cost: 7 }])
    assert.deepEqual(store.weeklyActivity._global, [{ cost: 7 }])
})

test('mounted real graph script refreshes on activity events and removes its listener on unmount', async () => {
    const file = readFileSync(new URL('../components/activity/ContributionGraphs.vue', import.meta.url), 'utf8')
    const script = file.split('<script setup>')[1].split('</script>')[0].replace(/^import .*$/gm, '')
    const window = new EventTarget()
    const gates = []
    const apiFetch = url => {
        assert.equal(url, '/api/projects/p/daily-activity/')
        return new Promise(resolve => gates.push(resolve))
    }
    const names = ['ref', 'computed', 'watch', 'onMounted', 'onUnmounted', 'createStatisticsRefresh',
        'apiFetch', 'ALL_PROJECTS_ID', 'useSettingsStore', 'useStartupPolling', 'getProviderOptions', 'defineProps', 'window', 'useI18n']
    const setup = new Function(...names, `${script}\nreturn {dailyActivity, activityTotals}`)
    let exposed
    const renderer = createRenderer({
        createComment: () => ({}), insert() {}, remove() {}, parentNode() {}, nextSibling() {},
    })
    const app = renderer.createApp({ setup() {
        exposed = setup(ref, computed, watch, onMounted, onUnmounted, createStatisticsRefresh, apiFetch,
            '_all', () => ({ areCostsShown: true }), () => {}, () => [], () => ({ projectId: 'p' }), window, () => createAppI18n().global)
        return () => null
    } })
    app.mount({})
    await nextTick(); await tick()
    assert.equal(gates.length, 1)
    window.dispatchEvent(new Event('twicc:activity-updated'))
    window.dispatchEvent(new Event('twicc:activity-updated'))
    gates.shift()({ ok: true, json: async () => ({ daily_activity: [{ cost: 1 }] }) })
    await tick()
    assert.equal(gates.length, 1)
    gates.shift()({ ok: true, json: async () => ({ daily_activity: [{ cost: 7 }], totals: { cost: 7 } }) })
    await tick()
    assert.deepEqual(exposed.dailyActivity.value, [{ cost: 7 }])
    app.unmount()
    window.dispatchEvent(new Event('twicc:activity-updated'))
    await tick()
    assert.equal(gates.length, 0)
})

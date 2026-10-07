import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { computed, reactive, watch, nextTick } from 'vue'
import { isSessionUnread } from '../../utils/sessions.js'
import { summarizeProcessActivity } from '../../utils/processActivity.js'
import { getProjectActivityIndex } from '../../utils/projectProcessActivity.js'

// Run the real aggregate computations without browser-only component imports.
const source = readFileSync(new URL('./AggregatedProcessIndicator.vue', import.meta.url), 'utf8')
const start = source.indexOf('const projectIdSet =')
const create = new Function('computed', 'dataStore', 'props', 'isSessionUnread', 'summarizeProcessActivity',
    source.slice(start, source.indexOf('</script>', start)) + '; return summary')

test('equal request counts preserve project summaries and unrelated project subscribers', async () => {
    const store = reactive({ isStartupInProgress: false,
        sessions: { a: { id: 'a', project_id: 'p' }, b: { id: 'b', project_id: 'q' } },
        processStates: { a: { project_id: 'p', state: 'assistant_turn', pending_requests: [{ tool_input: 'before' }] } },
        getProjectIndicatorScopeIds: id => [id],
    })
    store.getProjectActivitySummary = (ids, previous) => getProjectActivityIndex(store).summary(ids, previous)
    const p = create(computed, store, { projectIds: ['p'] }, isSessionUnread, summarizeProcessActivity)
    const q = create(computed, store, { projectIds: ['q'] }, isSessionUnread, summarizeProcessActivity)
    const beforeP = p.value, beforeQ = q.value
    let updates = 0; const stop = watch([p, q], () => updates++)
    store.processStates.a.pending_requests = [{ tool_input: 'after' }]
    await nextTick()
    assert.strictEqual(p.value, beforeP); assert.strictEqual(q.value, beforeQ); assert.equal(updates, 0)
    store.processStates.a.state = 'user_turn'; await nextTick()
    assert.equal(p.value.hasAssistantTurn, false); assert.strictEqual(q.value, beforeQ)
    stop()
})

// Run with: node --test src/composables/useChatEntrance.test.js (from the frontend dir)
// Live chat entrances (visual refresh step 5a, docs/plans/2026-09-28-chat-entrances-skeleton-design.md §5):
// the composable's state, driven in an effect scope with fake timers and a fake environment.
import test from 'node:test'
import assert from 'node:assert/strict'
import { effectScope, nextTick, ref } from 'vue'

import { setParsedContent } from '../utils/parsedContent.js'
import { useChatEntrance } from './useChatEntrance.js'

/** Fake timers on a manual clock, and an optional document carrying --motion-dur-3. */
function makeEnv({ dur3 = '380ms', withDocument = true } = {}) {
    const env = { now: 0, timers: new Map(), nextId: 1, styleReads: 0 }
    env.setTimeout = (fn, ms) => {
        const id = env.nextId++
        env.timers.set(id, { fn, at: env.now + ms })
        return id
    }
    env.clearTimeout = (id) => { env.timers.delete(id) }
    env.advance = (ms) => {
        const target = env.now + ms
        for (;;) {
            const due = [...env.timers.entries()].filter(([, t]) => t.at <= target).sort((a, b) => a[1].at - b[1].at)[0]
            if (!due) break
            env.timers.delete(due[0])
            env.now = due[1].at
            due[1].fn()
        }
        env.now = target
    }
    if (withDocument) {
        env.document = { documentElement: { name: 'html' } }
        env.getComputedStyle = (element) => {
            assert.equal(element, env.document.documentElement)
            env.styleReads++
            return { getPropertyValue: (name) => (name === '--motion-dur-3' ? dur3 : '') }
        }
    }
    return env
}

const row = (lineNum, extra = {}) => ({ lineNum, kind: 'tool_use', isBlockStart: true, isBlockEnd: true, ...extra })

function setup(initial = [row(1)], { revealed = true, env = makeEnv() } = {}) {
    const items = ref(initial)
    const isRevealed = ref(revealed)
    const scope = effectScope()
    const entrance = scope.run(() => useChatEntrance({
        items,
        getKey: (item) => item.lineNum,
        isRevealed: () => isRevealed.value,
        env,
    }))
    return { items, isRevealed, scope, entrance, env }
}

test('the setup call never produces a class, even when revealed', async () => {
    const { entrance, scope, env } = setup([row(1), row(-500)])
    assert.equal(entrance.itemClass(row(1)), null)
    assert.equal(entrance.itemClass(row(-500)), null)
    assert.equal(entrance.itemStyle(row(-500)), null)
    assert.equal(env.timers.size, 0)
    scope.stop()
})

test('noteLive then an update adding the line: class and style, removed after the timer', async () => {
    const { items, entrance, scope, env } = setup()
    entrance.noteLive([2, 3])
    items.value = [row(1), row(2), row(3)]
    await nextTick()
    assert.deepEqual(entrance.itemClass(row(2)), ['chat-entering', 'is-card'])
    assert.deepEqual(entrance.itemStyle(row(2)), { '--chat-enter-index': 0 })
    assert.deepEqual(entrance.itemStyle(row(3)), { '--chat-enter-index': 1 })
    assert.equal(env.styleReads, 1, 'the duration is read once per batch')
    // Row 2: 0 × 70 + 380 + 100.
    env.advance(479)
    assert.notEqual(entrance.itemClass(row(2)), null)
    env.advance(1)
    assert.equal(entrance.itemClass(row(2)), null)
    assert.notEqual(entrance.itemClass(row(3)), null, 'row 3 is staggered by 70ms')
    env.advance(70)
    assert.equal(entrance.itemClass(row(3)), null)
    scope.stop()
})

test('the duration comes from the env, 380 without a document or with an empty token', async () => {
    for (const [env, expected] of [
        [makeEnv({ dur3: '0.5s' }), 600],
        [makeEnv({ withDocument: false }), 480],
        [makeEnv({ dur3: '' }), 480],
    ]) {
        const { items, entrance, scope } = setup([row(1)], { env })
        entrance.noteLive([2])
        items.value = [row(1), row(2)]
        await nextTick()
        env.advance(expected - 1)
        assert.notEqual(entrance.itemClass(row(2)), null, `still entering at ${expected - 1}`)
        env.advance(1)
        assert.equal(entrance.itemClass(row(2)), null, `done at ${expected}`)
        scope.stop()
    }
})

test('tool arrivals follow the group reveal: a curtain row, or an opened group\'s head; indexed in the batch', async () => {
    const { items, entrance, scope } = setup()
    entrance.noteLive([2, 3, 4])
    items.value = [
        row(1),
        row(2, { externallyGrouped: true }),
        row(3, { externallyGrouped: true }),
        row(4, { externallyGrouped: true, isGroupHead: true, isExpanded: true }),
    ]
    await nextTick()
    assert.deepEqual(entrance.itemClass(row(2)), ['group-row-growing'])
    assert.deepEqual(entrance.itemStyle(row(2)), { '--group-index': 0, '--group-count': 3 })
    assert.deepEqual(entrance.itemStyle(row(3)), { '--group-index': 1, '--group-count': 3 })
    assert.deepEqual(entrance.itemClass(row(4)), ['group-head-revealing'])
    assert.deepEqual(entrance.itemStyle(row(4)), { '--group-index': 2, '--group-count': 3 })
    scope.stop()
})

test('the variants reach the class', async () => {
    const { items, entrance, scope } = setup()
    entrance.noteLive([2, 3])
    items.value = [row(1), row(2, { kind: 'user_message' }), row(3, { isBlockEnd: false })]
    await nextTick()
    assert.deepEqual(entrance.itemClass(row(2)), ['chat-entering', 'is-user'])
    assert.deepEqual(entrance.itemClass(row(3)), ['chat-entering', 'is-slice'])
    scope.stop()
})

test('noteRetired or noteViewChange then an update: no class; the next update does not reuse the state', async () => {
    const { items, entrance, scope } = setup()
    entrance.noteLive([2])
    entrance.noteRetired([{ streamingLineNum: -1000, realLineNum: 2 }])
    items.value = [row(1), row(2)]
    await nextTick()
    assert.equal(entrance.itemClass(row(2)), null)
    entrance.noteLive([3])
    items.value = [row(1), row(2), row(3)]
    await nextTick()
    assert.notEqual(entrance.itemClass(row(3)), null, 'the retired set was emptied')

    entrance.noteViewChange()
    items.value = [row(1), row(2), row(3), row(-500)]
    await nextTick()
    assert.equal(entrance.itemClass(row(-500)), null)
    items.value = [row(1), row(2), row(3), row(-500), row(-400)]
    await nextTick()
    assert.notEqual(entrance.itemClass(row(-400)), null, 'the view-change flag was reset')
    scope.stop()
})

test('a key re-entering keeps its class for the full new duration', async () => {
    const { items, entrance, scope, env } = setup()
    items.value = [row(1), row(-500)]
    await nextTick()
    assert.notEqual(entrance.itemClass(row(-500)), null)
    env.advance(300)
    items.value = [row(1)]
    await nextTick()
    items.value = [row(1), row(-500)]
    await nextTick()
    env.advance(300)
    assert.notEqual(entrance.itemClass(row(-500)), null, 'the old timer does not cut the new entrance')
    env.advance(180)
    assert.equal(entrance.itemClass(row(-500)), null)
    scope.stop()
})

test('a key removed while entering loses its entry and timer; back without being live, no class', async () => {
    const { items, entrance, scope, env } = setup()
    entrance.noteLive([2])
    items.value = [row(1), row(2)]
    await nextTick()
    assert.equal(env.timers.size, 1)
    items.value = [row(1)]
    await nextTick()
    assert.equal(entrance.itemClass(row(2)), null)
    assert.equal(env.timers.size, 0)
    items.value = [row(1), row(2)]
    await nextTick()
    assert.equal(entrance.itemClass(row(2)), null)
    scope.stop()
})

test('hasToolBlock reads the parsed content', async () => {
    const withContent = (item, parsed) => { setParsedContent(item, parsed); return item }
    const streamed = { lineNum: -1000, syntheticKind: 'streaming-block' }
    const { items, entrance, scope } = setup([row(1), streamed])
    const toolUse = withContent(row(2, { kind: 'content_items' }), { message: { content: [{ type: 'tool_use' }] } })
    const toolResult = withContent(row(3, { kind: 'content_items' }), { message: { content: [{ type: 'tool_result' }] } })
    const thinking = withContent(row(4, { kind: 'content_items' }), { message: { content: [{ type: 'thinking' }] } })
    const codex = withContent(row(5, { kind: 'assistant_message' }), { payload: { type: 'agent_message' } })
    entrance.noteLive([2, 3, 4, 5])
    items.value = [row(1), toolUse, toolResult, thinking, codex, streamed]
    await nextTick()
    assert.notEqual(entrance.itemClass(toolUse), null)
    assert.notEqual(entrance.itemClass(toolResult), null)
    assert.equal(entrance.itemClass(thinking), null)
    assert.equal(entrance.itemClass(codex), null)
    scope.stop()
})

test('clear() removes every class and cancels the timers; so does disposing the scope', async () => {
    const first = setup()
    first.entrance.noteLive([2])
    first.items.value = [row(1), row(2)]
    await nextTick()
    first.entrance.clear()
    assert.equal(first.entrance.itemClass(row(2)), null)
    assert.equal(first.env.timers.size, 0)
    first.scope.stop()

    const second = setup()
    second.entrance.noteLive([2])
    second.items.value = [row(1), row(2)]
    await nextTick()
    second.scope.stop()
    assert.equal(second.entrance.itemClass(row(2)), null)
    assert.equal(second.env.timers.size, 0)
})

test('noteLive / noteRetired with a non-array change nothing', async () => {
    const { items, entrance, scope } = setup()
    entrance.noteLive(2)
    entrance.noteLive(null)
    entrance.noteRetired({ realLineNum: 2 })
    items.value = [row(1), row(2)]
    await nextTick()
    assert.equal(entrance.itemClass(row(2)), null, 'line 2 was never live')
    entrance.noteLive([3])
    entrance.noteRetired(undefined)
    items.value = [row(1), row(2), row(3)]
    await nextTick()
    assert.notEqual(entrance.itemClass(row(3)), null)
    scope.stop()
})

test('not revealed: a live line gets no class, and does not enter once revealed', async () => {
    const { items, isRevealed, entrance, scope } = setup([row(1)], { revealed: false })
    entrance.noteLive([2])
    items.value = [row(1), row(2)]
    await nextTick()
    assert.equal(entrance.itemClass(row(2)), null)
    isRevealed.value = true
    items.value = [row(1), row(2), row(3)]
    await nextTick()
    assert.equal(entrance.itemClass(row(2)), null)
    scope.stop()
})

import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { computed, reactive, ref } from 'vue'

for (const [name, path] of [['composer', '../message/MessageInput.vue'], ['terminal', '../terminal/TerminalPanel.vue']]) {
    test(`${name} group context stays stable until a context field changes`, () => {
        const source = readFileSync(new URL(path, import.meta.url), 'utf8')
        const start = source.indexOf('const snippetGroupContext = computed(')
        const end = source.indexOf('\n])', start) + 3
        assert.ok(start >= 0 && end > start)
        const deps = { computed, props: reactive({ sessionId: 'main', projectId: 'project', contextKey: 's:main', active: true }),
            session: ref({ provider: 'claude_code' }), route: reactive({ query: {} }), collapsed: ref(false),
            resolvedProjectId: ref('project'), activeAttachedKey: ref(null), activeIndex: ref(0) }
        const context = new Function(...Object.keys(deps), `${source.slice(start, end)}\nreturn snippetGroupContext`)(...Object.values(deps))
        const before = context.value
        deps.props.unrelated = 1
        assert.strictEqual(context.value, before)
        deps.props.sessionId = 'other'
        assert.notStrictEqual(context.value, before)
        assert.ok(context.value.includes('other'))
    })
}

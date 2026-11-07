import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { runInNewContext } from 'node:vm'
import { computed, effectScope, nextTick, ref, watch } from 'vue'

test('project action labels remeasure in both locale directions and retain resize behavior', async () => {
    const source = readFileSync(new URL('../../composables/useActionsRowLabels.js', import.meta.url), 'utf8')
    const fn = source.match(/function useActionsRowLabels\([^]*?\n}/)?.[0]
    assert.ok(fn)
    const header = readFileSync(new URL('./ProjectDetailHeader.vue', import.meta.url), 'utf8')
    const key = header.match(/const actionsLabels = useActionsRowLabels\(headerRef, actionsRef, (\(\) => \[[^]*?\])\)/)?.[1]
    assert.ok(key, 'use the actual project-header reset key')
    const locale = ref('en')
    const width = ref(400)
    const resetKey = runInNewContext(key, {
        locale, props: { projectId: 'project' }, isArchived: ref(false),
        compactHeight: ref(false), rootFontSizePx: ref(16),
    })
    const actions = ref({ querySelectorAll: () => [
        { offsetTop: 0 },
        { get offsetTop() { return width.value < (locale.value === 'pt-BR' ? 450 : 300) ? 20 : 0 } },
    ] })
    const useLabels = runInNewContext(`(${fn})`, { computed, ref, watch, useElementSize: () => ({ width }) })
    const scope = effectScope()
    try {
        const labels = scope.run(() => useLabels(ref(null), actions, resetKey))
        assert.equal(labels.value, true)
        locale.value = 'pt-BR'
        await nextTick()
        assert.equal(labels.value, false, 'longer labels hide when locale makes them wrap at the same width')
        locale.value = 'en'
        await nextTick()
        assert.equal(labels.value, true, 'shorter labels return at the same width')
        width.value = 290
        await nextTick()
        assert.equal(labels.value, false, 'resize still hides wrapping labels')
        width.value = 280
        await nextTick()
        assert.equal(labels.value, false)
        width.value = 350
        await nextTick()
        assert.equal(labels.value, true, 'growth retries labels above the recorded wrapping width')
    } finally {
        scope.stop()
    }
})

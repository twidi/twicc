import test from 'node:test'
import assert from 'node:assert/strict'
import { effectScope, nextTick, ref } from 'vue'
import { useLocalizedContentBody } from './useLocalizedContentBody.js'

function deferred() {
    let resolve, reject
    const promise = new Promise((yes, no) => { resolve = yes; reject = no })
    return { promise, resolve, reject }
}

async function settled() {
    await nextTick()
    await Promise.resolve()
    await nextTick()
}

test('updates an open body on locale changes and ignores late content from the previous locale', async () => {
    const key = ref('projects'), locale = ref('en')
    const english = deferred(), portuguese = deferred()
    const scope = effectScope()
    const state = scope.run(() => useLocalizedContentBody(key, locale, (id, lang) => {
        assert.equal(id, 'projects')
        return lang === 'en' ? english.promise : portuguese.promise
    }))
    try {
        assert.equal(state.loading.value, true)
        locale.value = 'pt-BR'
        await nextTick()
        portuguese.resolve({ html: '<p>Projetos</p>', locale: 'pt-BR' })
        await settled()
        assert.equal(state.bodyHtml.value, '<p>Projetos</p>')
        assert.equal(state.contentLocale.value, 'pt-BR')
        assert.equal(state.loading.value, false)
        english.resolve({ html: '<p>Projects</p>', locale: 'en' })
        await settled()
        assert.equal(state.bodyHtml.value, '<p>Projetos</p>')
    } finally { scope.stop() }
})

test('old failures cannot replace a newer page and closing clears pending content', async () => {
    const key = ref('projects'), locale = ref('en')
    const old = deferred(), current = deferred()
    const scope = effectScope()
    const state = scope.run(() => useLocalizedContentBody(key, locale, id => id === 'projects' ? old.promise : current.promise))
    try {
        key.value = 'workspaces'
        await nextTick()
        current.resolve({ html: '<p>Workspaces</p>', locale: 'en' })
        await settled()
        old.reject(new Error('old network failure'))
        await settled()
        assert.equal(state.bodyHtml.value, '<p>Workspaces</p>')
        assert.equal(state.errored.value, false)
        key.value = null
        await settled()
        assert.equal(state.bodyHtml.value, '')
        assert.equal(state.loading.value, false)
    } finally { scope.stop() }
})

test('current failures show the existing error state and retry succeeds when reopened', async () => {
    const key = ref('projects'), locale = ref('pt-BR')
    let fails = true
    const scope = effectScope()
    const state = scope.run(() => useLocalizedContentBody(key, locale, async () => {
        if (fails) throw new Error('network failure')
        return { html: '<p>English fallback</p>', locale: 'en' }
    }))
    try {
        await settled()
        assert.equal(state.errored.value, true)
        assert.equal(state.loading.value, false)
        key.value = null
        await nextTick()
        fails = false
        key.value = 'projects'
        await settled()
        assert.equal(state.errored.value, false)
        assert.equal(state.bodyHtml.value, '<p>English fallback</p>')
        assert.equal(state.contentLocale.value, 'en')
    } finally { scope.stop() }
})

test('a response after unmount cannot publish content', async () => {
    const pending = deferred()
    const scope = effectScope()
    const state = scope.run(() => useLocalizedContentBody(ref('projects'), ref('en'), () => pending.promise))
    scope.stop()
    pending.resolve({ html: '<p>Late result</p>', locale: 'en' })
    await settled()
    assert.equal(state.bodyHtml.value, '')
})

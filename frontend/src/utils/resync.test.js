// frontend/src/utils/resync.test.js
//
// Run with:  node --test src/utils/resync.test.js   (from the frontend dir)
//
// Policy tests need no DOM. The render test exercises the real toast component
// with real messages, without loading the WebSocket and store dependency graph.

import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { h } from 'vue'
import { renderToString } from '@vue/server-renderer'
import { i18n, loadLocaleMessages } from '../i18n/index.js'
import { decideResyncAction, RELOAD_COOLDOWN_MS } from './resync.js'

// These rendering tests exercise both catalogs; production loads them on demand.
await loadLocaleMessages('pt-BR')

const NOW = 1_700_000_000_000

test('a visible tab reloads immediately', () => {
    assert.equal(
        decideResyncAction({ visible: true, lastReload: null, now: NOW }),
        'reload',
    )
})

test('a hidden tab waits instead of reloading for nobody', () => {
    // It is also the tab most likely to have fallen behind, so reloading it
    // where no one is looking would burn the exact resource it is short of.
    assert.equal(
        decideResyncAction({ visible: false, lastReload: null, now: NOW }),
        'defer',
    )
})

test('a second loss inside the cooldown asks instead of reloading', () => {
    // The guard that matters: the loss happens because the client cannot keep
    // up, and a reload gives it more work. Without this, a chronically slow
    // client reloads forever.
    assert.equal(
        decideResyncAction({ visible: true, lastReload: NOW - 1000, now: NOW }),
        'ask',
    )
})

test('the cooldown outranks visibility', () => {
    // Otherwise a hidden tab would queue a deferred reload that fires the
    // moment the user returns, right after the one that just happened.
    assert.equal(
        decideResyncAction({ visible: false, lastReload: NOW - 1000, now: NOW }),
        'ask',
    )
})

test('reloading is allowed again once the cooldown has elapsed', () => {
    assert.equal(
        decideResyncAction({ visible: true, lastReload: NOW - RELOAD_COOLDOWN_MS, now: NOW }),
        'reload',
    )
    assert.equal(
        decideResyncAction({ visible: true, lastReload: NOW - RELOAD_COOLDOWN_MS - 1, now: NOW }),
        'reload',
    )
})

test('the boundary is exclusive — one millisecond short still asks', () => {
    assert.equal(
        decideResyncAction({ visible: true, lastReload: NOW - RELOAD_COOLDOWN_MS + 1, now: NOW }),
        'ask',
    )
})

test('a corrupted or absent stamp is treated as "never reloaded"', () => {
    // readLastReload() returns null for anything unparseable, so a wiped or
    // hand-edited sessionStorage must not wedge the tab into 'ask' forever.
    assert.equal(
        decideResyncAction({ visible: true, lastReload: null, now: NOW }),
        'reload',
    )
})


test('the persistent resync toast renders translated text and keeps its reload action', async context => {
    const previousLocale = i18n.global.locale.value
    context.after(() => { i18n.global.locale.value = previousLocale })
    const source = readFileSync(new URL('./resync.js', import.meta.url), 'utf8')
    const handler = source.slice(source.indexOf('function showResyncToast()'), source.indexOf('/** Test seam:'))
        .replace("import('../composables/useToast')", 'Promise.resolve({ toast })')
    let notification, reloads = 0, prevented = false
    const showResyncToast = new Function('h', 'i18n', 'toast', 'window', `${handler}; return showResyncToast`)(
        h, i18n, { custom(content, options) { notification = { content, options } } },
        { location: { reload() { reloads++ } } },
    )
    showResyncToast()
    await Promise.resolve()
    assert.equal(notification.options.duration, Infinity)
    assert.equal(notification.options.type, 'warning')
    assert.equal(notification.options.title, i18n.global.t('session.resync.title'))
    assert.ok((await renderToString(h(notification.content))).includes(i18n.global.t('session.resync.description')))
    i18n.global.locale.value = 'pt-BR'
    const body = notification.content.setup()()
    assert.equal(body.children[0].children, i18n.global.t('session.resync.description'))
    const link = body.children[1]
    assert.equal(link.type, 'a')
    assert.equal(link.children, i18n.global.t('session.resync.reload'))
    assert.notEqual(link.children, 'Reload now')
    link.props.onClick({ preventDefault() { prevented = true } })
    assert.equal(prevented, true)
    assert.equal(reloads, 1)
})

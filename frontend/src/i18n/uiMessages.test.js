import test from 'node:test'
import assert from 'node:assert/strict'
import { createAppI18n, loadLocaleMessages } from './index.js'
import { localUiMessage, resolveUiMessage } from './uiMessages.js'

// These rendering tests exercise both catalogs; production loads them on demand.
await loadLocaleMessages('pt-BR')

test('stored local messages follow locale changes and retain plural parameters', () => {
    const composer = createAppI18n().global
    composer.mergeLocaleMessage('en', { auditCount: 'One item | {count} items' })
    composer.mergeLocaleMessage('pt-BR', { auditCount: 'Um item | {count} itens' })
    const message = localUiMessage('auditCount', { count: 2 }, 2)
    assert.equal(resolveUiMessage(message, composer), '2 items')
    composer.locale.value = 'pt-BR'
    assert.equal(resolveUiMessage(message, composer), '2 itens')
})

test('external text and errors keep their content', () => {
    const external = 'session.rename.errors.empty'
    assert.equal(resolveUiMessage(external), external)
    const error = { error: 'Provider says no' }
    assert.equal(resolveUiMessage(error), error)
    assert.equal(resolveUiMessage(''), '')
})

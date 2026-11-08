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

await loadLocaleMessages('fr')

test('formatsOnlyDeclaredNumbers and preserves exact decimal strings', async () => {
    const { formatMessageParams } = await import('./messageDescriptors.js')
    const params = { used: '12345678901234567890.6700', status: 12345, user: '12345.6700' }
    assert.deepEqual(formatMessageParams('session.notifications.creditsUsed', params, 'fr'), {
        used: '12\u202f345\u202f678\u202f901\u202f234\u202f567\u202f890,6700', status: 12345, user: '12345.6700',
    })
    assert.equal(formatMessageParams('session.notifications.creditsUsed', { used: '-0.00100' }, 'fr').used, '-0,00100')
    assert.deepEqual(formatMessageParams('session.summary.httpFailed', { status: 12345 }, 'fr'), { status: 12345 })
    const composer = createAppI18n('fr').global
    assert.match(resolveUiMessage(localUiMessage('session.notifications.creditsUsed', { used: '12345.6700' }), composer), /12\u202f345,6700/)
})

test('formatted count parameters retain plural selection without an explicit count', () => {
    const composer = createAppI18n().global
    assert.equal(resolveUiMessage(localUiMessage('common.sessionCount', { count: 2 }), composer), '2 sessions')
})


test('matches every real generated Vue oracle case through the shared web resolver', async () => {
    const { readFile } = await import('node:fs/promises')
    const { toLocalMessageDescriptor } = await import('./messageDescriptors.js')
    const fixture = JSON.parse(await readFile(new URL('../../../android/app/src/test/resources/i18n/render-cases.json', import.meta.url)))
    for (const locale of ['de', 'es']) await loadLocaleMessages(locale)
    const composer = createAppI18n().global
    for (const entry of fixture.cases) {
        if (entry.message.key.startsWith('fixture.')) continue
        composer.locale.value = entry.locale
        const descriptor = toLocalMessageDescriptor(entry.message, composer)
        assert.ok(descriptor, `${entry.locale} ${entry.message.key}`)
        assert.equal(resolveUiMessage(descriptor, composer), entry.expected, `${entry.locale} ${entry.message.key}`)
    }
})

test('numeric amounts keep their shortest decimal value below twenty fraction digits', async () => {
    const { formatMessageParams } = await import('./messageDescriptors.js')
    assert.equal(formatMessageParams('session.notifications.creditsUsed', { used: 1e-25 }, 'fr').used,
        '0,0000000000000000000000001')
    assert.equal(formatMessageParams('session.notifications.creditsUsed', { used: 1.234e22 }, 'en').used,
        '12,340,000,000,000,000,000,000')
})

test('recursive descriptor validation uses the selected French plural form parameters', async () => {
    const { toLocalMessageDescriptor } = await import('./messageDescriptors.js')
    const composer = createAppI18n('fr').global
    composer.mergeLocaleMessage('en', { parameterSubset: 'One {single} | Many {multiple}' })
    composer.mergeLocaleMessage('fr', { parameterSubset: 'Un {single} | Plusieurs {multiple}' })
    const descriptor = toLocalMessageDescriptor({ key: 'parameterSubset', params: { single: 'item' }, count: 0 }, composer)
    assert.ok(descriptor)
    assert.equal(resolveUiMessage(descriptor, composer), 'Un item')
})

test('declared numeric count retains Vue named-count priority over the plural argument', () => {
    const composer = createAppI18n().global
    assert.equal(resolveUiMessage(localUiMessage('common.sessionCount', { count: 2 }, 1), composer), '2 sessions')
})

test('implicit plural count receives declared number formatting', () => {
    const composer = createAppI18n().global
    assert.equal(resolveUiMessage(localUiMessage('common.sessionCount', {}, 12345), composer), '12,345 sessions')
})

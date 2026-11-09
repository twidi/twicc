import { resolveUiMessageWithComposer } from './uiMessageRenderer.js'
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
    assert.equal(resolveUiMessageWithComposer(message, composer), '2 items')
    composer.locale.value = 'pt-BR'
    assert.equal(resolveUiMessageWithComposer(message, composer), '2 itens')
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
    assert.match(resolveUiMessageWithComposer(localUiMessage('session.notifications.creditsUsed', { used: '12345.6700' }), composer), /12\u202f345,6700/)
})

test('formatted count parameters retain plural selection without an explicit count', () => {
    const composer = createAppI18n().global
    assert.equal(resolveUiMessageWithComposer(localUiMessage('common.sessionCount', { count: 2 }), composer), '2 sessions')
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
        assert.equal(resolveUiMessageWithComposer(descriptor, composer), entry.expected, `${entry.locale} ${entry.message.key}`)
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
    assert.equal(resolveUiMessageWithComposer(descriptor, composer), 'Un item')
})

test('declared numeric count retains Vue named-count priority over the plural argument', () => {
    const composer = createAppI18n().global
    assert.equal(resolveUiMessageWithComposer(localUiMessage('common.sessionCount', { count: 2 }, 1), composer), '2 sessions')
})

test('implicit plural count receives declared number formatting', () => {
    const composer = createAppI18n().global
    assert.equal(resolveUiMessageWithComposer(localUiMessage('common.sessionCount', {}, 12345), composer), '12,345 sessions')
})

test('formatted plural aliases preserve the original numeric count priority', () => {
    const composer = createAppI18n().global
    const cases = [
        [{ count: 2, n: 1 }, undefined, '2 sessions'],
        [{ count: 1, n: 2 }, undefined, '1 session'],
        [{ count: 2, n: 1 }, 1, '2 sessions'],
        [{ count: 0, n: 1 }, 2, '0 sessions'],
        [{ count: '2.00', n: 1 }, 2, '2.00 session'],
        [{ n: 2 }, 1, '1 sessions'],
    ]
    for (const [params, count, expected] of cases) {
        assert.equal(resolveUiMessageWithComposer(localUiMessage('common.sessionCount', params, count), composer), expected,
            JSON.stringify({ params, count }))
    }
    composer.mergeLocaleMessage('en', { conflictingAliases: '{count} / {n} item | {count} / {n} items' })
    assert.equal(resolveUiMessageWithComposer(localUiMessage('conflictingAliases', { count: 2, n: 1 }), composer), '2 / 1 items')
})

test('Vue numeric interpolation defines JavaScript exponent and shortest-decimal boundaries', () => {
    const composer = createAppI18n().global
    composer.mergeLocaleMessage('en', { numericText: '{value}' })
    for (const [value, expected] of [
        [1e-6, '0.000001'], [1e-7, '1e-7'], [1e20, '100000000000000000000'],
        [1e21, '1e+21'], [1e23, '1e+23'], [-0, '0'],
        [-1e-7, '-1e-7'], [-1e21, '-1e+21'], [Number.MIN_VALUE, '5e-324'],
        [1000000000000000128, '1000000000000000100'], [0.1, '0.1'],
        [2 ** -24, '5.960464477539063e-8'], [-(2 ** -24), '-5.960464477539063e-8'],
    ]) {
        assert.equal(composer.t('numericText', { value }), expected)
        assert.equal(resolveUiMessageWithComposer(localUiMessage('numericText', { value }), composer), expected)
    }
})

test('public resolver always follows the application locale', async (context) => {
    const { i18n } = await import('./index.js')
    const previousLocale = i18n.global.locale.value
    context.after(() => { i18n.global.locale.value = previousLocale })
    const message = localUiMessage('files.actions.download')
    const translations = new Set()
    for (const locale of ['en', 'fr', 'de', 'es', 'pt-BR']) {
        await loadLocaleMessages(locale)
        i18n.global.locale.value = locale
        translations.add(resolveUiMessage(message))
        assert.equal(resolveUiMessage(message), i18n.global.t(message.key))
        // A stale caller cannot replace the global context with an incomplete object.
        assert.equal(resolveUiMessage(message, { t() { throw Error('unexpected context') } }),
            i18n.global.t(message.key))
    }
    assert.ok(translations.size > 1)
    assert.equal(resolveUiMessage.length, 1)
})

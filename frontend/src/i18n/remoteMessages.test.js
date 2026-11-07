import test from 'node:test'
import assert from 'node:assert/strict'
import { i18n } from './index.js'
import { localUiMessage, resolveUiMessage } from './uiMessages.js'
import { remoteUiMessage, remoteUiError, remoteUiErrors } from './remoteMessages.js'

test('remote messages retain their identity while the selected locale changes', () => {
    const previous = i18n.global.locale.value
    try {
        const message = remoteUiMessage({ error: 'Cancel', ui_message: { key: 'common.actions.cancel', params: {} } })
        i18n.global.locale.value = 'en'
        assert.equal(resolveUiMessage(message), 'Cancel')
        i18n.global.locale.value = 'pt-BR'
        assert.equal(resolveUiMessage(message), 'Cancelar')
    } finally { i18n.global.locale.value = previous }
})

test('unknown and malformed metadata preserves the English fallback without showing keys', () => {
    for (const ui_message of [null, 'backend.invalid', { key: 'backend.unknown' }, { key: 'common.actions.cancel', params: [] }]) {
        assert.equal(remoteUiMessage({ error: 'Original diagnostic', ui_message }), 'Original diagnostic')
    }
    assert.equal(remoteUiMessage({ error: 'Original diagnostic' }), 'Original diagnostic')
})

test('named fields and plural counts keep structured metadata', () => {
    assert.deepEqual(remoteUiMessage({ label_ui_message: { key: 'common.actions.cancel', params: {}, count: 0 } }, 'Cancel', 'label_ui_message'), {
        type: 'local-ui-message', key: 'common.actions.cancel', params: {}, count: 0,
    })
})

test('remote Error retains reactive UI identity and machine code', () => {
    const error = remoteUiError({ error: 'Cancel', code: 'cancelled', status: 400, ui_message: { key: 'common.actions.cancel' } })
    assert.ok(error instanceof Error)
    assert.equal(error.code, 'cancelled')
    assert.equal(error.status, 400)
    assert.equal(error.uiMessage.key, 'common.actions.cancel')
    const previous = i18n.global.locale.value
    try {
        i18n.global.locale.value = 'pt-BR'
        assert.equal(error.message, 'Cancelar')
    } finally { i18n.global.locale.value = previous }
})

test('remote errors preserve external diagnostics before a local HTTP fallback', () => {
    const fallback = localUiMessage('session.summary.httpFailed', { status: 400 })
    assert.equal(remoteUiError({ error: 'Provider detail' }, fallback).message, 'Provider detail')
    const error = remoteUiError({}, fallback)
    assert.equal(error.uiMessage.key, 'session.summary.httpFailed')
    assert.match(error.message, /400/)
    assert.doesNotMatch(error.message, /object Object/)
})


test('mixed validation lists resolve each stored message after a locale change', () => {
    const previous = i18n.global.locale.value
    try {
        const message = remoteUiErrors({ errors: [
            { message: 'Cancel', ui_message: { key: 'common.actions.cancel' } },
            { message: 'External diagnostic' },
        ] })
        i18n.global.locale.value = 'en'
        assert.equal(resolveUiMessage(message), 'Cancel — External diagnostic')
        i18n.global.locale.value = 'pt-BR'
        assert.equal(resolveUiMessage(message), 'Cancelar — External diagnostic')
    } finally { i18n.global.locale.value = previous }
})

test('nested message parameters do not freeze retained toast text', () => {
    const previous = i18n.global.locale.value
    try {
        const message = localUiMessage('files.upload.retryFrom', {
            message: remoteUiMessage({ ui_message: { key: 'common.actions.cancel' } }), location: 'Files',
        })
        i18n.global.locale.value = 'en'
        assert.match(resolveUiMessage(message), /Cancel/)
        i18n.global.locale.value = 'pt-BR'
        assert.match(resolveUiMessage(message), /Cancelar/)
        assert.doesNotMatch(resolveUiMessage(message), /object Object|local-ui-message/)
    } finally { i18n.global.locale.value = previous }
})

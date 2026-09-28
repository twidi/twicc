import { test } from 'node:test'
import assert from 'node:assert/strict'
import {
    DISK_FULL_MESSAGE, classifyCreationAnswer, classifyTusError, tusShouldRetry,
} from './transport.js'

function tusError(status, { upload = false, body = null, request = true } = {}) {
    const err = new Error('tus')
    err.originalRequest = request ? {} : null
    if (status !== null) {
        err.originalResponse = {
            getStatus: () => status,
            getHeader: name => (upload && name === 'X-Twicc-Upload' ? '1' : null),
            getBody: () => body,
        }
    }
    return err
}

function answer(status, upload) {
    return { status, getHeader: name => (upload && name === 'X-Twicc-Upload' ? '1' : null) }
}

test('creation answers: created, unauthorized, refused only with the upload header', () => {
    assert.equal(classifyCreationAnswer(null), 'unanswered')
    assert.equal(classifyCreationAnswer(answer(201, true)), 'created')
    assert.equal(classifyCreationAnswer(answer(200, true)), 'created')
    assert.equal(classifyCreationAnswer(answer(401, false)), 'unauthorized')
    assert.equal(classifyCreationAnswer(answer(400, true)), 'refused')
    assert.equal(classifyCreationAnswer(answer(403, true)), 'refused')
    assert.equal(classifyCreationAnswer(answer(507, true)), 'refused')
    // A proxy 4xx, any 5xx except the upload code's 507: may exist on the server.
    for (const status of [408, 413, 429, 404]) assert.equal(classifyCreationAnswer(answer(status, false)), 'unanswered')
    for (const status of [500, 502, 503, 504, 520, 524, 530]) {
        assert.equal(classifyCreationAnswer(answer(status, false)), 'unanswered')
        assert.equal(classifyCreationAnswer(answer(status, true)), 'unanswered')
    }
    assert.equal(classifyCreationAnswer(answer(507, false)), 'unanswered')
})

test('onShouldRetry: no response, 409, 423, 5xx except 507, proxy 4xx', () => {
    assert.equal(tusShouldRetry(tusError(null)), true)
    assert.equal(tusShouldRetry(tusError(409, { upload: true })), true)
    assert.equal(tusShouldRetry(tusError(423)), true)
    assert.equal(tusShouldRetry(tusError(500, { upload: true })), true)
    assert.equal(tusShouldRetry(tusError(502)), true)
    assert.equal(tusShouldRetry(tusError(507, { upload: true })), false)
    for (const status of [408, 413, 429, 400]) assert.equal(tusShouldRetry(tusError(status)), true)
    for (const status of [400, 403, 412, 415, 410, 422]) assert.equal(tusShouldRetry(tusError(status, { upload: true })), false)
    assert.equal(tusShouldRetry(tusError(401)), false)
    assert.equal(tusShouldRetry(tusError(404)), false)
    assert.equal(tusShouldRetry(tusError(200)), false)
})

test('onError table', () => {
    const active = { state: 'active', error: null }
    assert.deepEqual(classifyTusError(tusError(null, { request: false }), active).reason, 'error')
    assert.deepEqual(classifyTusError(tusError(401), active), { action: 'unauthorized' })
    for (const status of [404, 410, 422]) {
        assert.deepEqual(classifyTusError(tusError(status, { upload: true }), active), { action: 'reconcile' })
    }
    assert.deepEqual(classifyTusError(tusError(404), active), { action: 'reconcile' })
    assert.deepEqual(classifyTusError(tusError(507, { upload: true }), active),
        { action: 'pause', reason: 'error', message: DISK_FULL_MESSAGE })
    // A retryable status while the record carries a finalization error.
    assert.deepEqual(classifyTusError(tusError(500, { upload: true }), { state: 'active', error: 'copy failed' }),
        { action: 'pause', reason: 'error', message: 'copy failed' })
    assert.deepEqual(classifyTusError(tusError(null), { state: 'active', error: 'copy failed' }).reason, 'error')
    // Persistent upload-code 500 vs. a 500 without the header.
    assert.equal(classifyTusError(tusError(500, { upload: true, body: '{"error":"boom"}' }), active).message, 'boom')
    assert.equal(classifyTusError(tusError(500, { upload: true }), active).reason, 'error')
    assert.equal(classifyTusError(tusError(500), active).reason, 'network')
    for (const status of [null, 409, 423, 408, 413, 429, 502, 524]) {
        assert.equal(classifyTusError(tusError(status), active).reason, 'network', `status ${status}`)
    }
    assert.equal(classifyTusError(tusError(400, { upload: true }), active).reason, 'error')
    assert.equal(classifyTusError(tusError(415, { upload: true }), active).reason, 'error')
})

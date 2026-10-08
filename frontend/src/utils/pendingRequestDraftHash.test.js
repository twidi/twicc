// `hashPendingRequest` must ignore the auto-deny deadline keys: the local
// deadline differs between two page loads. The module cannot load under plain
// node (extensionless Vite imports), so the function source is extracted and
// run with its two dependencies injected.

import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

import { withoutAutoDenyKeys } from './autoDeny.js'
import { hashString } from './hash.js'

const source = readFileSync(new URL('./pendingRequestDraftStorage.js', import.meta.url), 'utf8')
const start = source.indexOf('export function hashPendingRequest(')
const end = source.indexOf('\n}\n', start) + 2
const hashPendingRequest = new Function(
    'hashString', 'withoutAutoDenyKeys',
    `${source.slice(start, end).replace('export ', '')}\nreturn hashPendingRequest`,
)(hashString, withoutAutoDenyKeys)

test('the draft hash ignores the auto-deny deadline keys', () => {
    const base = { request_id: 'r-1', tool_name: 'Bash', created_at: 1, tool_input: { command: 'ls' } }
    const hash = hashPendingRequest(base)
    assert.equal(hashPendingRequest({ ...base, autoDenyDeadlineMs: 123 }), hash)
    assert.equal(hashPendingRequest({ ...base, autoDenyDeadlineMs: 456 }), hash)
    assert.equal(hashPendingRequest({ ...base, auto_deny_in_seconds: 7 }), hash)
})

test('the draft hash still sees a real change', () => {
    const base = { request_id: 'r-1', tool_name: 'Bash', created_at: 1, tool_input: { command: 'ls' } }
    assert.notEqual(hashPendingRequest({ ...base, tool_input: { command: 'pwd' } }), hashPendingRequest(base))
})

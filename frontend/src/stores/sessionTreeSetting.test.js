// settings.js uses extensionless imports, so Node tests check its registration
// points in the source. The sync key set is imported directly.
import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

import { SYNCED_SETTINGS_KEYS } from '../constants.js'

const source = readFileSync(new URL('./settings.js', import.meta.url), 'utf8')

test('showSessionsAsTree defaults to false and accepts booleans', () => {
    assert.match(source, /showSessionsAsTree: false,/)
    assert.match(source, /showSessionsAsTree: \(v\) => typeof v === 'boolean'/)
})

test('showSessionsAsTree has a getter and validated setter', () => {
    assert.match(source, /isShowSessionsAsTree: \(state\) => state\.showSessionsAsTree/)
    assert.match(source, /setShowSessionsAsTree\(enabled\)/)
    assert.match(source, /SETTINGS_VALIDATORS\.showSessionsAsTree\(enabled\)/)
    assert.match(source, /this\.showSessionsAsTree = enabled/)
})

test('showSessionsAsTree persists locally without server sync', () => {
    assert.match(source, /showSessionsAsTree: store\.showSessionsAsTree,/)
    assert.equal(SYNCED_SETTINGS_KEYS.has('showSessionsAsTree'), false)
})

// settings.js uses extensionless imports, so Node tests check its registration
// points in the source. The sync key set is imported directly.
import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

import { SYNCED_SETTINGS_KEYS } from '../constants.js'

const source = readFileSync(new URL('./settings.js', import.meta.url), 'utf8')

test('showProviderIcons defaults to false and accepts booleans', () => {
    assert.match(source, /showProviderIcons: false,/)
    assert.match(source, /showProviderIcons: \(v\) => typeof v === 'boolean'/)
})

test('showProviderIcons has a getter and validated setter', () => {
    assert.match(source, /isShowProviderIcons: \(state\) => state\.showProviderIcons/)
    assert.match(source, /setShowProviderIcons\(enabled\)/)
    assert.match(source, /SETTINGS_VALIDATORS\.showProviderIcons\(enabled\)/)
    assert.match(source, /this\.showProviderIcons = enabled/)
})

test('showProviderIcons persists locally without server sync', () => {
    assert.match(source, /showProviderIcons: store\.showProviderIcons,/)
    assert.equal(SYNCED_SETTINGS_KEYS.has('showProviderIcons'), false)
})

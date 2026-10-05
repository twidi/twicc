// settings.js uses extensionless imports, so Node tests check its registration
// points in the source. The sync key set is imported directly.
import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

import { SYNCED_SETTINGS_KEYS } from '../constants.js'

const source = readFileSync(new URL('./settings.js', import.meta.url), 'utf8')

test('icon bar visibility defaults to true and accepts booleans', () => {
    assert.match(source, /sidebarRailVisibleWhenClosed: true,/)
    assert.match(source, /sidebarRailVisibleWhenClosed: \(v\) => typeof v === 'boolean'/)
})

test('icon bar visibility has a getter and validated setter', () => {
    assert.match(source, /isSidebarRailVisibleWhenClosed: \(state\) => state\.sidebarRailVisibleWhenClosed/)
    assert.match(source, /setSidebarRailVisibleWhenClosed\(enabled\)/)
    assert.match(source, /SETTINGS_VALIDATORS\.sidebarRailVisibleWhenClosed\(enabled\)/)
    assert.match(source, /this\.sidebarRailVisibleWhenClosed = enabled/)
})

test('icon bar visibility persists locally without server sync', () => {
    assert.match(source, /sidebarRailVisibleWhenClosed: store\.sidebarRailVisibleWhenClosed,/)
    assert.equal(SYNCED_SETTINGS_KEYS.has('sidebarRailVisibleWhenClosed'), false)
})

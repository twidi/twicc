import test from 'node:test'
import assert from 'node:assert/strict'

import * as constants from '../constants.js'

test('syncs the title suggestion model across clients', () => {
    assert.ok(constants.SYNCED_SETTINGS_KEYS.has('titleSuggestionModel'))
})

test('uses provider routing when the setting is missing or invalid', () => {
    const resolve = constants.resolveTitleSuggestionModel

    assert.equal(resolve?.(undefined), 'provider')
    assert.equal(resolve?.(null), 'provider')
    assert.equal(resolve?.('unknown'), 'provider')
    assert.equal(resolve?.('haiku'), 'haiku')
    assert.equal(resolve?.('luna'), 'luna')
})

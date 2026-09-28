import { test } from 'node:test'
import assert from 'node:assert/strict'
import { isAppNavigation, markAppNavigation } from './appNavigation.js'

test('the app-navigation flag is false until marked', () => {
    assert.equal(isAppNavigation(), false)
    markAppNavigation()
    assert.equal(isAppNavigation(), true)
})

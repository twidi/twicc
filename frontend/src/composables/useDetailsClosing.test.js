// Run with: node --test src/composables/useDetailsClosing.test.js (from the frontend dir)
import test from 'node:test'
import assert from 'node:assert/strict'
import { computed } from 'vue'

import { useDetailsClosing } from './useDetailsClosing.js'

test('mark, clear and isClosing, per key', () => {
    const { isClosing, markClosing, clearClosing } = useDetailsClosing()
    assert.equal(isClosing(), false)
    markClosing()
    assert.equal(isClosing(), true)
    assert.equal(isClosing('default'), true)
    assert.equal(isClosing('phase:a'), false)

    markClosing('phase:a')
    markClosing('agent:b')
    clearClosing('phase:a')
    assert.equal(isClosing('phase:a'), false)
    assert.equal(isClosing('agent:b'), true)
    clearClosing()
    assert.equal(isClosing(), false)
    clearClosing('never-marked')
    assert.equal(isClosing('never-marked'), false)
})

test('each call has its own state', () => {
    const first = useDetailsClosing()
    const second = useDetailsClosing()
    first.markClosing('x')
    assert.equal(second.isClosing('x'), false)
})

test('isClosing is reactive', () => {
    const { isClosing, markClosing, clearClosing } = useDetailsClosing()
    const shown = computed(() => isClosing('result'))
    assert.equal(shown.value, false)
    markClosing('result')
    assert.equal(shown.value, true)
    clearClosing('result')
    assert.equal(shown.value, false)
})

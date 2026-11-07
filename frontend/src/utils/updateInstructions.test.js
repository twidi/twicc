import { test } from 'node:test'
import assert from 'node:assert/strict'
import { setUpdateInstructions, getUpdateInstructions, getUpdateInstructionsHtml } from './updateInstructions.js'
import { i18n } from '../i18n/index.js'

test('bootstrap instructions render the exact environment command and escape HTML', () => {
    setUpdateInstructions({
        mode: 'pip', before: 'Stop TwiCC & run:',
        command: "'/my <env>/bin/python' -m pip install --upgrade twicc",
        after: 'Then restart TwiCC.',
    })
    const html = getUpdateInstructionsHtml()
    assert.ok(html.includes('Stop TwiCC &amp; run:'))
    assert.ok(html.includes('&lt;env&gt;'))
    assert.ok(html.includes('-m pip install --upgrade twicc'))
    assert.ok(html.includes('Then restart TwiCC.'))
    assert.ok(!html.includes('<env>'))
    assert.ok(!html.includes('uv tool upgrade'))
})

test('source and unknown instructions do not invent an upgrade command', () => {
    for (const before of ['Update your source checkout, then restart TwiCC.', 'Use your package manager.']) {
        setUpdateInstructions({ before, command: null, after: '' })
        assert.equal(getUpdateInstructionsHtml(), before)
    }
    setUpdateInstructions(null)
    assert.ok(getUpdateInstructionsHtml().includes('package manager'))
    assert.ok(!getUpdateInstructionsHtml().includes('<code'))
})


test('the structured fallback follows the current locale and preserves explicit instructions', context => {
    const previousLocale = i18n.global.locale.value
    context.after(() => { i18n.global.locale.value = previousLocale; setUpdateInstructions(null) })
    setUpdateInstructions(null)
    const english = getUpdateInstructions()
    i18n.global.locale.value = 'pt-BR'
    assert.deepEqual(getUpdateInstructions(), {
        before: i18n.global.t('settings.updates.fallbackInstructions'), command: null, after: '',
    })
    assert.notEqual(getUpdateInstructions().before, english.before)
    const explicit = { before: 'Custom installer:', command: '/custom/bin/update', after: 'Custom restart.' }
    setUpdateInstructions(explicit)
    assert.deepEqual(getUpdateInstructions(), explicit)
})

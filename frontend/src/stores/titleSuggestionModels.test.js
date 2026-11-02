import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

const popoverSource = readFileSync(new URL('../components/app/SettingsPopover.vue', import.meta.url), 'utf8')
const dataSource = readFileSync(new URL('./data.js', import.meta.url), 'utf8')
const renameSource = readFileSync(
    new URL('../components/session/detail/SessionRenameDialog.vue', import.meta.url),
    'utf8',
)

/** The source with every whitespace run collapsed: template sentences wrap over lines. */
const flat = source => source.replace(/\s+/g, ' ')

/** The `<wa-radio …>…</wa-radio>` element whose value is `value`. */
function radioFor(value) {
    const start = popoverSource.indexOf(`:value="${value}"`)
    assert.ok(start !== -1, `a radio has the value ${value}`)
    const open = popoverSource.lastIndexOf('<wa-radio', start)
    const end = popoverSource.indexOf('</wa-radio>', start)
    return popoverSource.slice(open, end)
}

test('the Titles setting offers OpenRouter, disabled while the OpenRouter key is not set', () => {
    const radio = radioFor('TITLE_SUGGESTION_MODEL.OPENROUTER')
    assert.match(radio, /:disabled="!!disabledTitleSuggestionModels\[TITLE_SUGGESTION_MODEL\.OPENROUTER\]"/)
    assert.match(flat(radio), /\{\{ TITLE_SUGGESTION_MODEL_LABELS\[TITLE_SUGGESTION_MODEL\.OPENROUTER\] \}\} for every session/)
    // The disabled map holds OpenRouter exactly when the key is absent.
    assert.match(popoverSource, /if \(!openrouterKeySet\.value\) \{\s*disabled\[TITLE_SUGGESTION_MODEL\.OPENROUTER\] =/)
    // The note says what to do (the variable, where, and the restart), not only that the key is missing.
    assert.match(popoverSource, /Add TWICC_OPENROUTER_API_KEY to \$\{textToSpeechStore\.envPath\}, then restart TwiCC\./)
    assert.match(popoverSource, /Add TWICC_OPENROUTER_API_KEY to the \.env file of your TwiCC data directory, then restart TwiCC\./)
    assert.doesNotMatch(popoverSource, /'The OpenRouter key is not set\.'/)
    assert.match(popoverSource, /const openrouterKeySet = computed\(\(\) => textToSpeechStore\.keyPresent\)/)
})

test('the OpenRouter radio says where the text goes and who pays, unless the key is missing', () => {
    const radio = flat(radioFor('TITLE_SUGGESTION_MODEL.OPENROUTER'))
    // The disabled note comes first; the privacy and cost note replaces it while the key is set.
    assert.match(
        radio,
        /<span v-if="disabledTitleSuggestionModels\[TITLE_SUGGESTION_MODEL\.OPENROUTER\]" class="radio-note" >— \{\{ disabledTitleSuggestionModels\[TITLE_SUGGESTION_MODEL\.OPENROUTER\] \}\}<\/span>/,
    )
    assert.match(
        radio,
        /<span v-else class="radio-note" >— uses \{\{ OPENROUTER_MODEL_DESCRIPTION \}\}; sends the user messages to OpenRouter, paid on your OpenRouter account<\/span>/,
    )
})

test('the Titles hints hold for every model', () => {
    const source = flat(popoverSource)
    assert.match(source, /TwiCC generates the title with another model/)
    assert.doesNotMatch(source, /generates the title with the other provider/)
    // The radio note says where the text goes: the hint does not say it twice.
    assert.equal(source.match(/paid on your OpenRouter account/g)?.length, 1)
    // The model words come from the one constant, never written in the component.
    assert.doesNotMatch(source, /Qwen|Cerebras/)
})

test('a displaced OpenRouter choice says it comes back with the key, an agent choice with its provider', () => {
    const source = flat(popoverSource)
    assert.match(
        source,
        /<template v-if="displacedTitleModelIsOpenRouter"> Your choice is kept, and comes back once the OpenRouter key is set and TwiCC restarted\. <\/template>/,
    )
    assert.match(
        source,
        /<template v-else> Your choice is kept, and comes back as soon as you enable its provider again\. <\/template>/,
    )
    assert.match(
        popoverSource,
        /const displacedTitleModelIsOpenRouter = computed\(\s*\(\) => titleSuggestionModelIsDisplaced\.value && store\.getTitleSuggestionModel === TITLE_SUGGESTION_MODEL\.OPENROUTER,\s*\)/,
    )
})

test('the title_suggested handler keeps the asked and the real model', () => {
    const start = dataSource.indexOf('handleTitleSuggested(data) {')
    assert.ok(start !== -1, 'the handler exists')
    const body = dataSource.slice(start, dataSource.indexOf('clearTitleSuggestion(sessionId)', start))
    assert.match(body, /const \{[^}]*\brequestedModel\b[^}]*\btitleModel\b[^}]*\} = data/)
    assert.match(body, /requestedModel: requestedModel \|\| null,/)
    assert.match(body, /titleModel: titleModel \|\| null,/)
})

test('the rename dialog names the real model, marks a fallback by model, and offers OpenRouter with the key', () => {
    assert.match(renameSource, /suggestionModel\(suggestionEntry\.value\)/)
    assert.match(renameSource, /isFallbackEntry\(suggestionEntry\.value\)/)
    assert.match(
        renameSource,
        /titleModelAlternatives\(displayedTitleModel\.value, settingsStore\.enabledProviders, textToSpeechStore\.keyPresent\)/,
    )
    // The provider-only checks are gone: they cannot tell OpenRouter from an agent.
    assert.doesNotMatch(renameSource, /titleModelForProvider\(/)
    assert.doesNotMatch(renameSource, /entry\.titleProvider !== entry\.requestedProvider/)
})

import en from '../i18n/locales/en.json' with { type: 'json' }
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

test('the Titles setting offers OpenRouter, disabled while the OpenRouter key is not set', () => {
    // The segments: the session provider, then the three models; one that cannot run is disabled, its reason is the tooltip.
    assert.match(popoverSource, /const titleModelOptions = computed\(\(\) => \[\s*\{ value: TITLE_SUGGESTION_MODEL\.PROVIDER, label: t\('settings\.titles\.model\.followProvider'\) \},\s*\.\.\.\[TITLE_SUGGESTION_MODEL\.HAIKU, TITLE_SUGGESTION_MODEL\.LUNA, TITLE_SUGGESTION_MODEL\.OPENROUTER\]/)
    assert.match(popoverSource, /disabled: true, title: `\$\{TITLE_SUGGESTION_MODEL_LABELS\[model\]\}: \$\{disabledTitleSuggestionModels\.value\[model\]\}`/)
    assert.equal(en['settings.titles.model.followProvider'], 'Follow session provider')
    // The disabled map holds OpenRouter exactly when the key is absent.
    assert.match(popoverSource, /if \(!openrouterKeySet\.value\) \{\s*disabled\[TITLE_SUGGESTION_MODEL\.OPENROUTER\] =/)
    // The note only says the key is missing: the help page says how to set it.
    assert.match(popoverSource, /disabled\[TITLE_SUGGESTION_MODEL\.OPENROUTER\] = t\('settings\.titles\.model\.keyMissing'\)/)
    assert.equal(en['settings.titles.model.keyMissing'], 'OpenRouter key not set')
    assert.doesNotMatch(popoverSource, /'The OpenRouter key is not set\.'/)
    assert.match(popoverSource, /const openrouterKeySet = computed\(\(\) => textToSpeechStore\.keyPresent\)/)
})

test('the Titles model is a segmented control with the reasons and the OpenRouter help link under it', () => {
    const source = flat(popoverSource)
    assert.match(source, /<SegmentedControl fit :label="\$t\('common\.labels\.model'\)" :model-value="titleSuggestionModel" :options="titleModelOptions" @update:model-value="store\.setTitleSuggestionModel\(\$event\)" @click="onTitleSuggestionModelClick" @keydown="onTitleSuggestionModelKeydown" \/>/)
    assert.match(source, /v-for="\(reason, model\) in disabledTitleSuggestionModels"[^>]*class="setting-group-hint" >\{\{ \$t\('settings\.titles\.model\.unavailable', \{ model: TITLE_SUGGESTION_MODEL_LABELS\[model\], reason \}\) \}\}<\/span> <HelpFeatureLink help-key="openrouter" :label="\$t\('help\.openrouter'\)" \/>/)
    // No radio, no per-model detail text any more.
    assert.doesNotMatch(popoverSource, /name="title-suggestion-model"/)
    assert.equal(en['settings.titles.model.everySession'], undefined)
    assert.equal(en['settings.titles.model.matchProvider'], undefined)
    assert.equal(en['settings.titles.model.openRouterHint'], undefined)
})

test('the Titles hints hold for every model', () => {
    const source = flat(popoverSource)
    assert.ok(source.includes("$t('settings.titles.model.fallbackHint')"))
    assert.match(en['settings.titles.model.fallbackHint'], /TwiCC generates the title with another model/)
    assert.doesNotMatch(en['settings.titles.model.fallbackHint'], /generates the title with the other provider/)
    assert.doesNotMatch(en['settings.titles.model.fallbackHint'], /paid on your OpenRouter account/)
    // The model words come from the one constant, never written in the component.
    assert.doesNotMatch(source, /Qwen|Cerebras/)
})

test('a displaced OpenRouter choice says it comes back with the key, an agent choice with its provider', () => {
    const source = flat(popoverSource)
    assert.match(en['settings.titles.model.openRouterSetupHint'], /once the OpenRouter key is set and TwiCC restarted\./)
    assert.match(en['settings.titles.model.disabledProviderHint'], /as soon as you enable its provider again\./)
    assert.match(
        source,
        /<template v-if="displacedTitleModelIsOpenRouter"> \{\{ \$t\('settings\.titles\.model\.openRouterSetupHint'\) \}\} <\/template>/,
    )
    assert.match(
        source,
        /<template v-else> \{\{ \$t\('settings\.titles\.model\.disabledProviderHint'\) \}\} <\/template>/,
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

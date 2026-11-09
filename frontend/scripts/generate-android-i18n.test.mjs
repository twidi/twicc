import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { compileAndroidCatalog, generateAndroidI18n } from './generate-android-i18n.mjs'

const locales = ['en', 'de', 'es', 'fr', 'pt-BR']
const manifest = (messages = { 'sample.message': {} }) => ({ format: 1, locales, messages })
const catalogs = source => Object.fromEntries(locales.map(locale => [locale, { 'sample.message': source }]))

test('packagesEveryNotificationSoundLabelAndLocalizedTitle', () => {
    const selected = JSON.parse(readFileSync(new URL('../../android/i18n/messages.json', import.meta.url)))
    const sounds = JSON.parse(readFileSync(new URL('../../android/app/src/main/assets/notification-sounds/manifest.json', import.meta.url)))
    const shared = Object.fromEntries(locales.map(locale => [locale,
        JSON.parse(readFileSync(new URL(`../src/i18n/locales/${locale}.json`, import.meta.url), 'utf8'))]))
    for (const key of ['android.push.soundTitle', ...sounds.entries.map(({ id }) => `settings.notifications.sounds.${id}`)]) {
        assert.ok(Object.hasOwn(selected.messages, key), `Missing native sound message: ${key}`)
    }
    const compiled = compileAndroidCatalog(shared, selected)
    for (const locale of locales) {
        assert.deepEqual(compiled.locales[locale]['android.push.soundTitle'].parameters, ['sound'])
        assert.equal(shared[locale]['android.push.soundTitle'], 'TwiCC — {sound}')
    }
})

test('compilesLiteralPipesAndNamedValues', () => {
    const source = `"{provider}" isn't {'|'} {'@'} {'{'} {'}'}\ncafé 😀 {'a\\'b'}`
    const input = catalogs(source)
    const compiled = compileAndroidCatalog(input, manifest())
    const message = compiled.locales.en['sample.message']
    assert.equal(compiled.format, 1)
    assert.deepEqual(message.parameters, ['provider'])
    assert.deepEqual(message.forms[0].parameters, ['provider'])
    assert.equal(message.forms.length, 1)
    assert.equal(message.forms[0].tokens.map(token => token.type === 'named' ? 'Codex' : token.value).join(''),
        `"Codex" isn't | @ { }\ncafé 😀 a'b`)
    assert.equal(input.en['sample.message'], source)
})

test('keepsSharedMessagesExact', () => {
    const shared = Object.fromEntries(locales.map(locale => [locale,
        JSON.parse(readFileSync(new URL(`../src/i18n/locales/${locale}.json`, import.meta.url), 'utf8'))]))
    const before = structuredClone(shared)
    const selected = manifest({ 'composer.status.startingProvider': {}, 'common.sessionCount': {} })
    const result = compileAndroidCatalog(shared, selected)
    assert.deepEqual(shared, before)
    assert.deepEqual(result.locales.en['composer.status.startingProvider'].parameters, ['provider'])
    assert.equal(result.locales.fr['common.sessionCount'].forms.length, 2)
})

test('rejectsUnsupportedSubsetSyntax', () => {
    for (const source of ['@:other', '@.lower:other', '{0}', '<b>hello</b>', "{'<b>'}", "{'<'}b{'>'}", 'a | b | c | d', '{missing', '{"bad"}']) {
        assert.throws(() => compileAndroidCatalog(catalogs(source), manifest()), /sample.message/)
    }
})

test('detectsMissingLocaleOrParameter', () => {
    const missingLocale = catalogs('{provider}')
    delete missingLocale.fr
    assert.throws(() => compileAndroidCatalog(missingLocale, manifest()), /fr/)
    const missingKey = catalogs('{provider}')
    delete missingKey.de['sample.message']
    assert.throws(() => compileAndroidCatalog(missingKey, manifest()), /de.*sample.message/)
    const differentParameters = catalogs('{provider}')
    differentParameters.es['sample.message'] = '{model}'
    assert.throws(() => compileAndroidCatalog(differentParameters, manifest()), /parameter/i)
})

test('allowsDifferentParameterSubsetsPerPluralForm', () => {
    const input = catalogs('None | One {provider} | {count} with {provider}')
    input.fr['sample.message'] = 'Aucun {provider} | Un | {count} avec {provider}'
    const result = compileAndroidCatalog(input, manifest())
    assert.deepEqual(result.locales.en['sample.message'].parameters, ['count', 'provider'])
    assert.deepEqual(result.locales.en['sample.message'].forms.map(form => form.parameters),
        [[], ['provider'], ['count', 'provider']])
})

test('validatesDeclaredNumberFormats', () => {
    const selected = manifest({ 'sample.message': { numberFormats: { amount: 'decimal' } } })
    assert.deepEqual(compileAndroidCatalog(catalogs('{amount} / {status}'), selected)
        .locales.en['sample.message'].numberFormats, { amount: 'decimal' })
    assert.throws(() => compileAndroidCatalog(catalogs('{status}'), selected), /amount/)
    assert.throws(() => compileAndroidCatalog(catalogs('{amount}'), manifest({
        'sample.message': { numberFormats: { amount: 'unknown' } },
    })), /format/i)
})

test('generatedAssetsMatchSourcesAndVueOracle', async () => {
    const result = await generateAndroidI18n({ check: true })
    assert.ok(result.keys > 0)
    assert.equal(result.locales, 5)
    const fixtures = JSON.parse(readFileSync(new URL('../../android/app/src/test/resources/i18n/render-cases.json', import.meta.url)))
    assert.ok(fixtures.cases.some(value => value.locale === 'fr' && value.message.count === 1.5))
    assert.ok(fixtures.cases.some(value => value.message.count === 21))
    assert.ok(fixtures.cases.some(value => value.message.key === 'fixture.threeForms'))
    assert.ok(fixtures.cases.every(value => !value.expected.includes('NaN')), 'Numeric samples must be finite decimals')
    for (const locale of locales) {
        for (const key of Object.keys(fixtures.catalog.locales[locale])) {
            assert.ok(fixtures.cases.some(value => value.locale === locale && value.message.key === key), `${locale}.${key}`)
        }
    }
    assert.ok(fixtures.invalidSources.length > 0)
})

test('bootstrapResourcesPreserveEveryNativeResourceId', () => {
    const manifest = JSON.parse(readFileSync(new URL('../../android/i18n/messages.json', import.meta.url)))
    const xml = readFileSync(new URL('../../android/app/src/main/res/values/strings.xml', import.meta.url), 'utf8')
    const resources = [...xml.matchAll(/<string name="([^"]+)"/g)].map(match => match[1])
    assert.deepEqual(resources.sort(), ['app_name', ...Object.keys(manifest.bootstrapResources)].sort())
    assert.match(xml, /name="app_name" translatable="false">TwiCC<\/string>/)
    assert.equal(manifest.bootstrapResources.tts_notification_title.parameters.name, '%1$s')
    assert.deepEqual(manifest.bootstrapResources.instances_open_description.parameters,
        { name: '%1$s', address: '%2$s' })
    for (const mappings of Object.values(manifest.nativeSources)) {
        for (const key of Object.values(mappings)) assert.ok(Object.hasOwn(manifest.messages, key), key)
    }
})

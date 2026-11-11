import { mkdir, readFile, writeFile } from 'node:fs/promises'
import { dirname, resolve } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'
import { baseCompile, detectHtmlTag } from '@intlify/message-compiler'
import { createAppI18n } from '../src/i18n/index.js'
import { createAndroidFingerprints } from './android-i18n-fingerprints.mjs'
import { generateAndroidTestFixtures } from './generate-android-test-fixtures.mjs'

const root = fileURLToPath(new URL('../../', import.meta.url))
const supportedLocales = ['en', 'de', 'es', 'fr', 'pt-BR']
const manifestPath = 'android/i18n/messages.json'
const generatorPath = 'frontend/scripts/generate-android-i18n.mjs'
const catalogPath = 'android/app/src/main/assets/i18n/messages.json'
const fingerprintPath = 'android/app/src/main/assets/i18n/sources.sha256'
const fixturePath = 'android/app/src/test/resources/i18n/render-cases.json'
const xmlPath = 'android/app/src/main/res/values/strings.xml'
const sortedUnique = values => [...new Set(values)].sort()
const serialize = value => JSON.stringify(value, null, 2) + '\n'

/** Compile only the native subset. Catalog source strings are never changed. */
export function compileAndroidCatalog(catalogs, manifest) {
    if (manifest.format !== 1 || JSON.stringify(manifest.locales) !== JSON.stringify(supportedLocales)) {
        throw new Error('Manifest format or supported locales are incompatible')
    }
    if (!manifest.messages || !Object.keys(manifest.messages).length) throw new Error('Manifest has no messages')
    const result = { format: 1, locales: {} }
    for (const locale of manifest.locales) {
        if (!Object.hasOwn(catalogs, locale)) throw new Error(`Missing locale: ${locale}`)
        result.locales[locale] = {}
        for (const [key, metadata] of Object.entries(manifest.messages)) {
            const fail = reason => { throw new Error(`${locale}.${key}: ${reason}`) }
            if (!Object.hasOwn(catalogs[locale], key)) fail('Missing message')
            const source = catalogs[locale][key]
            if (typeof source !== 'string' || !source.trim()) fail('Message must be a nonempty string')
            if (detectHtmlTag(source)) fail('HTML is unsupported')
            const { ast } = baseCompile(source, { jit: true, optimize: false, onError: error => fail(error.message) })
            if (ast.type !== 0) fail('Unknown root node')
            const nodes = ast.body.type === 1 ? ast.body.cases : [ast.body]
            if (nodes.length < 1 || nodes.length > 3) fail('Only one, two, or three forms are supported')
            const forms = nodes.map(node => {
                if (node.type !== 2) fail(`Unsupported form node: ${node.type}`)
                const tokens = node.items.map(item => {
                    if (item.type === 3 || item.type === 9) {
                        if (typeof item.value !== 'string') fail('Invalid text node')
                        if (detectHtmlTag(item.value)) fail('HTML is unsupported')
                        return { type: 'text', value: item.value }
                    }
                    if (item.type === 4) return { type: 'named', name: item.key }
                    fail(`Unsupported node: ${item.type}`)
                })
                if (detectHtmlTag(tokens.map(token => token.type === 'text' ? token.value : ' parameter ').join(''))) {
                    fail('HTML is unsupported')
                }
                return { tokens, parameters: sortedUnique(tokens.filter(token => token.type === 'named').map(token => token.name)) }
            })
            const parameters = sortedUnique(forms.flatMap(form => form.parameters))
            const numberFormats = { ...metadata.numberFormats }
            for (const [name, format] of Object.entries(numberFormats)) {
                if (!parameters.includes(name)) fail(`Numeric parameter is unused: ${name}`)
                if (format !== 'decimal') fail(`Unsupported number format: ${format}`)
            }
            if (locale !== 'en' && JSON.stringify(parameters) !== JSON.stringify(result.locales.en[key].parameters)) {
                fail('Whole-message parameter sets differ from English')
            }
            result.locales[locale][key] = { forms, parameters, numberFormats }
        }
    }
    return result
}

const invalidSources = [
    { source: '@:other', reason: 'linked message' },
    { source: '@.lower:other', reason: 'linked modifier' },
    { source: '{0}', reason: 'list interpolation' },
    { source: '<b>hello</b>', reason: 'HTML' },
    { source: "{'<b>'}", reason: 'literal HTML' },
    { source: "{'<'}b{'>'}", reason: 'HTML assembled from literals' },
    { source: 'a | b | c | d', reason: 'more than three forms' },
    { source: '{missing', reason: 'invalid interpolation' },
]

function sampleValue(name) {
    if (name === 'count') return 2
    if (name === 'amount') return '12345.6700'
    if (name === 'provider') return 'Codex'
    if (name === 'status') return 503
    return `${name}: café 😀 "quote" {'|'} @`
}

// This adapter is used only by the Vue oracle, never by native rendering.
// Decimal strings preserve trailing zeroes; numbers use their shortest decimal form.
function oracleNumber(locale, value) {
    const fraction = String(value).split('.')[1]?.length ?? 0
    return new Intl.NumberFormat(locale, {
        minimumFractionDigits: typeof value === 'string' ? fraction : 0,
        maximumFractionDigits: Math.max(fraction, 20),
    }).format(value)
}

function renderOracle(composers, catalog, locale, message) {
    const definition = catalog.locales[locale][message.key]
    const params = Object.fromEntries(Object.entries(message.params ?? {}).map(([name, value]) => [name,
        value && typeof value === 'object' && typeof value.key === 'string'
            ? renderOracle(composers, catalog, locale, value)
            : definition.numberFormats[name] ? oracleNumber(locale, value) : value,
    ]))
    return message.count === undefined
        ? composers[locale].t(message.key, params)
        : composers[locale].t(message.key, params, message.count)
}

function createFixtures(catalogs, manifest) {
    const fixtureSources = {
        'fixture.literal': `"{provider}" isn't {'|'} {'@'} {'{'} {'}'}\ncafé 😀 {'a\\'b'}`,
        'fixture.oneForm': 'Always {count}',
        'fixture.twoForms': '{count} item | {count} items',
        'fixture.threeForms': 'None | One {provider} | {count} with {provider}',
        'fixture.nested': '{title}\n{body}',
        'fixture.decimal': '{amount} / HTTP {status} / {enabled}',
    }
    const fixtureManifest = { ...manifest, messages: {
        ...manifest.messages,
        ...Object.fromEntries(Object.keys(fixtureSources).map(key => [key,
            key === 'fixture.decimal' ? { numberFormats: { amount: 'decimal' } } : {}])),
    } }
    const allSources = Object.fromEntries(manifest.locales.map(locale => [locale, { ...catalogs[locale], ...fixtureSources }]))
    const catalog = compileAndroidCatalog(allSources, fixtureManifest)
    const composers = Object.fromEntries(manifest.locales.map(locale => {
        const composer = createAppI18n(locale).global
        composer.setLocaleMessage(locale, allSources[locale])
        return [locale, composer]
    }))
    const cases = []
    for (const locale of manifest.locales) {
        for (const [key, definition] of Object.entries(catalog.locales[locale])) {
            const params = Object.fromEntries(definition.parameters.map(name => [name,
                manifest.messages[key]?.sampleParams?.[name]
                    ?? (definition.numberFormats[name] ? '12345.6700' : sampleValue(name))]))
            if (key === 'fixture.nested') {
                params.title = { key: 'composer.status.startingProvider', params: { provider: 'Codex' } }
                params.body = { key: 'fixture.literal', params: { provider: 'Claude Code' } }
            }
            if (key === 'fixture.decimal') params.enabled = true
            const counts = definition.forms.length > 1 || definition.parameters.includes('count')
                ? [0, 1, 2, 21, ...(locale === 'fr' ? [0.5, 1.5, 2.5, -1.5] : [])] : [undefined]
            for (const count of counts) {
                const message = { key, params: { ...params }, ...(count === undefined ? {} : { count }) }
                if (count !== undefined && definition.parameters.includes('count')) message.params.count = count
                cases.push({ locale, message, expected: renderOracle(composers, catalog, locale, message) })
            }
        }
    }
    return { format: 1, catalog, cases, invalidSources }
}

function bootstrapXml(compiled, manifest) {
    const escapeXml = value => value.replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;')
        .replaceAll('"', '\\"').replaceAll("'", "\\'").replaceAll('\n', '\\n')
    const lines = ['<?xml version="1.0" encoding="utf-8"?>',
        '<!-- Generated by frontend/scripts/generate-android-i18n.mjs. Do not edit. -->',
        '<resources>', '    <!-- Product name: deliberately unchanged in every language. -->',
        '    <string name="app_name" translatable="false">TwiCC</string>']
    for (const [resource, mapping] of Object.entries(manifest.bootstrapResources ?? {})) {
        const definition = compiled.locales.en[mapping.key]
        if (!definition || definition.forms.length !== 1) throw new Error(`Invalid bootstrap resource: ${resource}`)
        const params = mapping.parameters ?? {}
        if (JSON.stringify(Object.keys(params).sort()) !== JSON.stringify(definition.parameters)) {
            throw new Error(`Bootstrap parameter mismatch: ${resource}`)
        }
        const text = definition.forms[0].tokens.map(token => token.type === 'text' ? token.value : params[token.name]).join('')
        lines.push(`    <string name="${resource}">${escapeXml(text)}</string>`)
    }
    return [...lines, '</resources>', ''].join('\n')
}

/** Regenerate all committed outputs, or fail without writing when check is true. */
export async function generateAndroidI18n({ check = false } = {}) {
    const sourcePaths = [manifestPath, generatorPath, 'frontend/scripts/android-i18n-fingerprints.mjs', 'frontend/scripts/generate-android-test-fixtures.mjs',
        'frontend/src/i18n/index.js', 'frontend/src/i18n/androidLocale.js', 'frontend/package-lock.json',
        'frontend/src/i18n/uiMessages.js', 'frontend/src/i18n/uiMessageRenderer.js',
        'frontend/src/i18n/remoteMessages.js', 'frontend/src/i18n/messageDescriptors.js',
        'frontend/src/utils/androidAppClient.js', 'frontend/src/utils/androidEnvironment.js', 'frontend/src/utils/nativeTtsClient.js',
        'frontend/src/utils/telemetryClient.js', 'frontend/src/utils/crypto.js',
        ...supportedLocales.filter(locale => locale !== 'en').map(locale => `frontend/src/i18n/locales/${locale}.js`),
        ...supportedLocales.map(locale => `frontend/src/i18n/locales/${locale}.json`)]
    const sources = Object.fromEntries(await Promise.all(sourcePaths.map(async path => [path, await readFile(resolve(root, path))])))
    const manifest = JSON.parse(sources[manifestPath])
    const catalogs = Object.fromEntries(supportedLocales.map(locale => [locale,
        JSON.parse(sources[`frontend/src/i18n/locales/${locale}.json`])]))
    const compiled = compileAndroidCatalog(catalogs, manifest)
    for (const mappings of Object.values(manifest.nativeSources ?? {})) {
        for (const key of Object.values(mappings)) {
            if (!Object.hasOwn(manifest.messages, key)) throw new Error(`Native mapping is not selected: ${key}`)
        }
    }
    const outputs = {
        [catalogPath]: serialize(compiled),
        [fixturePath]: serialize(createFixtures(catalogs, manifest)),
        [xmlPath]: bootstrapXml(compiled, manifest),
        ...await generateAndroidTestFixtures(manifest),
    }
    // Locale JSON contributes only selected native translations. Outputs remain fully checked.
    outputs[fingerprintPath] = createAndroidFingerprints(sources, outputs, manifest)
    for (const [path, expected] of Object.entries(outputs)) {
        if (check) {
            let actual
            try { actual = await readFile(resolve(root, path), 'utf8') } catch { /* Missing output is stale. */ }
            if (actual !== expected) throw new Error(`Stale generated output: ${path}. Run npm run i18n:android:generate`)
        } else {
            await mkdir(dirname(resolve(root, path)), { recursive: true })
            await writeFile(resolve(root, path), expected)
        }
    }
    return { keys: Object.keys(manifest.messages).length, locales: supportedLocales.length }
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
    try {
        const check = process.argv.includes('--check')
        const result = await generateAndroidI18n({ check })
        console.log(`Android i18n ${check ? 'checked' : 'generated'}: ${result.keys} keys, ${result.locales} locales`)
    } catch (error) {
        console.error(error.message)
        process.exitCode = 1
    }
}

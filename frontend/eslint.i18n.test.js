import test from 'node:test'
import assert from 'node:assert/strict'
import { fileURLToPath } from 'node:url'
import { ESLint } from 'eslint'

const eslint = new ESLint({ cwd: fileURLToPath(new URL('.', import.meta.url)), overrideConfigFile: 'eslint.i18n.config.js' })
const rawTextRule = '@intlify/vue-i18n/no-raw-text'
const lint = async (source, filePath = 'src/i18n-lint-probe.vue') => {
    const [result] = await eslint.lintText(source, { filePath })
    assert.equal(result.fatalErrorCount, 0, JSON.stringify(result.messages))
    return result.messages
}

test('official rules detect direct template text, conditional literals and visible attributes', async () => {
    const findings = await lint(`<template>
        <button title="Open details">Details</button>
        <wa-input label="Username" placeholder="Enter name" help-text="Required" />
        <HelpIconButton label="More information" />
        <wa-icon name="play" aria-label="Start playback" />
        <p>{{ busy ? 'Working now' : 'Ready now' }}</p>
    </template>`)
    for (const value of ['Open details', 'Details', 'Username', 'Enter name', 'Required',
        'More information', 'Start playback', 'Working now', 'Ready now']) {
        assert.ok(findings.some(item => item.ruleId === rawTextRule && item.message.includes(value)), value)
    }
    assert.ok(findings.every(item => item.ruleId.startsWith('@intlify/vue-i18n/')))
})

test('official config leaves technical script strings and translated messages alone', async () => {
    assert.deepEqual(await lint(`<script setup>
        const label = 'Indirect label'
        const style = { padding: '0 12px' }
        console.debug('Diagnostic message')
    </script><template>
        <wa-button variant="brand" @click="emit('close')">{{ $t('common.actions.save') }}</wa-button>
        <p>{{ label }}</p><span>123 …</span>
    </template>`), [])
    assert.deepEqual(await lint("const label: string = 'Indirect label'", 'src/i18n-lint-probe.ts'), [])
})

test('official catalog rules validate syntax, duplicate keys and absent message keys', async () => {
    const catalog = await lint('{ "probe": "First", "probe": "{unfinished" }', 'src/i18n/locales/en.json')
    assert.ok(catalog.some(item => item.ruleId === '@intlify/vue-i18n/no-duplicate-keys-in-locale'))
    assert.ok(catalog.some(item => item.ruleId === '@intlify/vue-i18n/valid-message-syntax'))
    assert.ok((await lint('<template>{{ $t("nonexistent.probe.key") }}</template>'))
        .some(item => item.ruleId === '@intlify/vue-i18n/no-missing-keys'))
})

test('an explained literal exception only suppresses the next template node', async () => {
    const findings = await lint(`<template>
        <!-- eslint-disable-next-line @intlify/vue-i18n/no-raw-text -- Product name. -->
        <strong>TwiCC</strong>
        <button>Untranslated action</button>
    </template>`)
    assert.equal(findings.length, 1)
    assert.equal(findings[0].ruleId, rawTextRule)
    assert.match(findings[0].message, /Untranslated action/)
})

test('tests and long help assets are excluded while help interface controls stay included', async () => {
    for (const path of ['src/example.test.js', 'public/help/example.md', 'public/tips/example.md']) {
        assert.equal(await eslint.isPathIgnored(path), true, path)
    }
    for (const file of ['src/components/help/HelpDialog.vue', 'src/components/tips/TipToast.vue']) {
        assert.equal(await eslint.isPathIgnored(file), false)
        assert.ok((await lint('<template><button>Close panel</button></template>', file))
            .some(item => item.ruleId === rawTextRule))
    }
})

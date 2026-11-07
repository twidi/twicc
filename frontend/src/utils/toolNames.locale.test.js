import test from 'node:test'
import assert from 'node:assert/strict'
import { computed } from 'vue'
import { i18n, loadLocaleMessages } from '../i18n/index.js'
import { formatToolNameForHeader, formatDurationMsCompact } from './toolNames.js'

// These rendering tests exercise both catalogs; production loads them on demand.
await loadLocaleMessages('pt-BR')

test('known first-party headings follow locale at the existing computed consumer boundary', () => {
    const before = i18n.global.locale.value
    try {
        i18n.global.locale.value = 'en'
        const goal = computed(() => formatToolNameForHeader('create_goal'))
        assert.equal(goal.value, 'Create goal')
        assert.equal(formatToolNameForHeader('request_user_input'), 'Request user input')
        i18n.global.locale.value = 'pt-BR'
        assert.equal(goal.value, 'Criar objetivo')
        assert.equal(formatToolNameForHeader('update_goal'), 'Atualizar objetivo')
        assert.equal(formatToolNameForHeader('request_user_input'), 'Solicitar entrada do usuário')
        assert.equal(formatToolNameForHeader('TaskCreate'), 'Criar tarefa')
        assert.equal(formatToolNameForHeader('collaboration__send_message'), 'Enviar mensagem')
    } finally {
        i18n.global.locale.value = before
    }
})

test('unknown/custom identities and explicit user labels retain their existing formatting', () => {
    const before = i18n.global.locale.value
    try {
        const names = ['customTool', 'mcp__custom__create_goal', 'my_namespace__request_user_input', 'toString']
        i18n.global.locale.value = 'en'
        const english = names.map(name => formatToolNameForHeader(name))
        i18n.global.locale.value = 'pt-BR'
        assert.deepEqual(names.map(name => formatToolNameForHeader(name)), english)
        assert.equal(formatToolNameForHeader('create_goal', 'My custom title'), 'My custom title')
        const minutes = new Intl.NumberFormat('pt-BR', { style: 'unit', unit: 'minute', unitDisplay: 'narrow', maximumFractionDigits: 0 }).format(2)
        assert.equal(formatDurationMsCompact(120000), minutes)
    } finally {
        i18n.global.locale.value = before
    }
})

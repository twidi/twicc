import test from 'node:test'
import assert from 'node:assert/strict'
import { computed } from 'vue'
import { createAppI18n, loadLocaleMessages } from './index.js'
import { getComposerPlaceholder } from './composerPlaceholder.js'

// These rendering tests exercise both catalogs; production loads them on demand.
await loadLocaleMessages('pt-BR')

function placeholder(locale, options = {}) {
    const instance = createAppI18n(locale)
    return getComposerPlaceholder(instance.global.t, locale, {
        provider: 'Claude Code', commandChars: ['/'], ...options,
    })
}

test('composer keyboard hints contain localized alternatives and complete shortcut messages', () => {
    assert.equal(placeholder('en'), 'Shortcuts: At start: / = commands, ! and Up/PageUp = message history; Anywhere: @ = file paths; Ctrl↵ or Meta↵ to send')
    assert.equal(placeholder('pt-BR'), 'Atalhos: No início: / = comandos e ! e ↑/PageUp = histórico de mensagens; Em qualquer lugar: @ = caminhos de arquivos; Ctrl↵ ou Meta↵ para enviar')
    assert.match(placeholder('pt-BR', { isMac: true }), /⌘↵ ou Ctrl↵ para enviar$/)
    assert.match(placeholder('pt-BR', { commandChars: ['/', '$', '@'] }), /\/, \$ ou @ = comandos/)
})

test('touch and draft hints omit unavailable shortcuts without leaving punctuation fragments', () => {
    assert.equal(placeholder('pt-BR', { isTouchDevice: true }), 'Atalhos: No início: / = comandos e ! = histórico de mensagens; Em qualquer lugar: @ = caminhos de arquivos')
    assert.equal(placeholder('en', { isDraft: true, commandChars: [], isTouchDevice: true }), 'Shortcuts: Anywhere: @ = file paths')
    assert.equal(placeholder('pt-BR', { isDraft: true, commandChars: [] }), 'Atalhos: Em qualquer lugar: @ = caminhos de arquivos; Ctrl↵ ou Meta↵ para enviar')
    assert.doesNotMatch(placeholder('en', { isDraft: true }), /message history/)
})

test('starting and working states use complete messages including the provider note', () => {
    assert.equal(placeholder('pt-BR', { state: 'starting', provider: 'Codex' }), 'Iniciando processo do Codex...')
    assert.equal(placeholder('pt-BR', { state: 'assistant_turn', provider: 'Codex' }), 'Você pode enviar uma mensagem agora. Codex a receberá assim que possível (durante ou após o trabalho).')
    assert.equal(placeholder('pt-BR', {
        state: 'assistant_turn', workingMessageKey: 'composer.shortcuts.sendWhileWorkingWithoutHistory',
    }), 'Você pode enviar uma mensagem agora. Claude Code a receberá assim que possível (durante ou após o trabalho). Observação: ela não aparecerá no histórico da conversa.')
})

test('the composer calculation follows the current locale', () => {
    const instance = createAppI18n('en')
    const value = computed(() => getComposerPlaceholder(instance.global.t, instance.global.locale.value, {
        provider: 'Codex', commandChars: ['/', '$'], isDraft: true,
    }))
    assert.match(value.value, /\/ or \$ = commands/)
    instance.global.locale.value = 'pt-BR'
    assert.match(value.value, /\/ ou \$ = comandos/)
    assert.doesNotMatch(value.value, / or /)
})

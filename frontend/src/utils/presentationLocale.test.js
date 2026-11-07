import assert from 'node:assert/strict'
import { afterEach, test } from 'node:test'
import { computed } from 'vue'
import { i18n, setLocale } from '../i18n/index.js'
import { ATTACHMENT_STATE, mapAttachmentStatus, attachmentBadge, formatAttachmentSize } from './composerAttachments.js'
import { PLACEHOLDERS, getUnavailablePlaceholders, resolveSnippetText } from './snippetPlaceholders.js'
import { describeEvent } from './timeline/describeEvent.js'
import { messageParts, valuePart } from './timeline/parts.js'
import { formatAvg } from './countUp.js'
import { formatBurnChip, formatQuotaMoment } from './usage.js'
import { speechEndResult } from './speechOutcome.js'

afterEach(() => setLocale('en'))

test('locale changes update display getters and preserve upload protocol states', () => {
    const label = computed(() => PLACEHOLDERS[0].label)
    const english = label.value
    setLocale('pt-BR')
    assert.notEqual(label.value, english)
    assert.equal(ATTACHMENT_STATE.UPLOADING, 'uploading')
    assert.deepEqual(mapAttachmentStatus({ state: 'uploading' }), { state: 'uploading' })
    assert.match(attachmentBadge({ count: 1, chipStates: ['uploading'] }).label, /upload|enviando/i)
    assert.equal(formatAttachmentSize(1536), '1,5 KB')
    assert.equal(formatAvg(1.5), '1,5')
    assert.equal(formatBurnChip({ utilization: 20, burnRate: 1.2 }).text, '×1,2')
})

test('snippet metadata adds translation keys and preserves terminal substitutions', () => {
    setLocale('pt-BR')
    const [unavailable] = getUnavailablePlaceholders(['project-dir'], {})
    assert.equal(unavailable.labelKey, PLACEHOLDERS[0].labelKey)
    assert.equal(unavailable.label, i18n.global.t(unavailable.labelKey))
    assert.equal(resolveSnippetText('cd {project-dir}', ['project-dir'], { project: { directory: '/raw/path' } }), 'cd /raw/path')
})

test('rich messages preserve literal provider data and translator slot order', () => {
    i18n.global.mergeLocaleMessage('pt-BR', { 'test.presentation.order': '{second} antes de {first}' })
    setLocale('pt-BR')
    const literal = valuePart('raw @data {value} | text')
    assert.deepEqual(messageParts('test.presentation.order', { first: literal, second: valuePart('B') }), [valuePart('B'), { type: 'text', text: ' antes de ' }, literal])
})

test('automatic timeline tags use stable author kind in both locales', () => {
    const event = { id: 1, kind: 'turn.started', actor: 'system', via: null, precision: 'exact', occurred_at: '2026-11-01T12:00:00Z', data: {} }
    const context = { sessions: {} }
    for (const locale of ['en', 'pt-BR']) {
        setLocale(locale)
        assert.ok(describeEvent(event, context).tags.includes(i18n.global.t('session.timeline.automatic')))
    }
})

test('quota day names use selected locale and speech refusal exposes a stable reason', () => {
    setLocale('pt-BR')
    const when = new Date(Date.now() + 48 * 3_600_000)
    assert.equal(formatQuotaMoment(when).day, new Intl.DateTimeFormat('pt-BR', { weekday: 'long' }).format(when))
    const result = speechEndResult({ kind: 'error', reason: 'refused' }, { state: 'complete' })
    assert.equal(result.reason, 'refused')
    assert.equal(result.error, i18n.global.t('session.speech.errors.refused'))
})

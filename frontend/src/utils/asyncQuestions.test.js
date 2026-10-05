import assert from 'node:assert/strict'
import { describe, it } from 'node:test'
import * as questions from './asyncQuestions.js'

export const batch = (item_id = 'q1', status = 'ready') => ({
    item_id, status, at: '2026-10-05T09:12:06Z', line: 5674,
    questions: [{ index: 2, title: 'Quelle action ?', options: ['Oui', 'Non'] },
        { index: 4, title: 'Details?', options: [] }],
})
const record = () => ({ choices: { q1: { 2: { kind: 'other', value: 'My own answer' } } },
    sourceBatches: { q1: batch() }, recoveredIds: [] })
const resolved = (status = 'sent', request_id = 'external') => ({ revision: 3, batches: [],
    resolutions: { q1: { status, request_id } }, widget_enabled: true })

describe('async question pure transforms', () => {
    it('matches Python formatting and preserves original question index and language', () => {
        assert.equal(questions.formatAsyncQuestionMessage([batch()], [
            { item_id: 'q1', index: 4, kind: 'other', value: '  Details\nnext  ' },
            { item_id: 'q1', index: 2, kind: 'option', value: 'Oui' },
        ], 'Keep text.\n'), 'Answers to your questions:\n\nQuestion: Quelle action ?\nAnswer: Oui\n\nQuestion: Details?\nAnswer:   Details\nnext  \n\nAdditional message:\nKeep text.\n')
        assert.equal(questions.formatAsyncQuestionMessage([batch()], [{ item_id: 'q1', index: 4, value: '  ' }], ' raw '), ' raw ')
    })
    it('orders canonical source lines before timestamps and anchors unlined batches', () => {
        const a = { ...batch('a'), line: 2, at: '2026-10-05T12:00:00Z' }
        const b = { ...batch('b'), line: 1, at: '2026-10-05T13:00:00Z' }
        const c = { ...batch('c'), line: null, at: '2026-10-05T12:30:00Z' }
        const output = questions.formatAsyncQuestionMessage([a, b, c], ['a', 'b', 'c'].map(item_id => ({ item_id, index: 2, value: item_id })), '')
        assert.ok(output.indexOf('Answer: c') < output.indexOf('Answer: b'))
        assert.ok(output.indexOf('Answer: b') < output.indexOf('Answer: a'))
    })
    it('matches Python whitespace and microsecond ordering', () => {
        assert.equal(questions.formatAsyncQuestionMessage([batch()], [{ item_id: 'q1', index: 2, value: '\u0085' }], 'text'), 'text')
        assert.equal(questions.formatAsyncQuestionMessage([batch()], [{ item_id: 'q1', index: 2, value: '\uFEFF' }], '\u0085'),
            'Answers to your questions:\n\nQuestion: Quelle action ?\nAnswer: \uFEFF')
        const a = { ...batch('a'), line: null, at: '2026-10-05T09:12:06.000002Z' }
        const b = { ...batch('b'), line: null, at: '2026-10-05T09:12:06.000001Z' }
        const output = questions.formatAsyncQuestionMessage([a, b], ['a', 'b'].map(item_id => ({ item_id, index: 2, value: item_id })), '')
        assert.ok(output.indexOf('Answer: b') < output.indexOf('Answer: a'))
    })
    it('recovers Other text once without changing existing draft text or attachments', () => {
        const draft = { message: 'Keep the original draft.', mediaIds: ['image'] }
        const first = questions.recoverResolvedAnswers({ draft, choices: record(), snapshot: resolved() })
        assert.ok(first.draft.message.includes('Answer: My own answer'))
        assert.ok(first.draft.message.endsWith(draft.message))
        assert.deepEqual(first.draft.mediaIds, ['image'])
        assert.deepEqual(first.recoveredIds, ['q1'])
        assert.equal(first.notice, 'These questions were handled elsewhere. Your answers are kept in your draft.')
        const second = questions.recoverResolvedAnswers({ draft: first.draft, choices: first.choices, snapshot: resolved() })
        assert.equal(second.draft.message, first.draft.message)
        assert.deepEqual(record().choices.q1[2], { kind: 'other', value: 'My own answer' })
    })
    it('recovers no-options free text from persisted source after server removal', () => {
        const choices = record()
        choices.choices.q1 = { 4: { kind: 'other', value: 'Retained answer' } }
        const result = questions.recoverResolvedAnswers({ draft: {}, choices, snapshot: resolved('dismissed') })
        assert.ok(result.draft.message.includes('Question: Details?\nAnswer: Retained answer'))
    })
    it('consumes matching local dismissals and sends without external text', () => {
        for (const status of ['dismissed', 'sent']) {
            const result = questions.recoverResolvedAnswers({ draft: { message: 'Keep' }, choices: record(), snapshot: resolved(status, 'local'),
                pendingDismissals: status === 'dismissed' ? { q1: 'local' } : {},
                pendingSends: status === 'sent' ? { local: { async_questions: { batch_ids: ['q1'] }, status: 'uncertain' } } : {} })
            assert.equal(result.draft.message, 'Keep')
            assert.deepEqual(result.choices.choices, {})
            assert.equal(result.notice, null)
        }
    })
    it('holds local answers while an unmatched local send remains uncertain', () => {
        const choices = record()
        const result = questions.recoverResolvedAnswers({ draft: { message: 'Keep' }, choices, snapshot: resolved(),
            pendingSends: { local: { async_questions: { batch_ids: ['q1'] }, status: 'uncertain' } } })
        assert.deepEqual(result.choices, choices)
        assert.equal(result.draft.message, 'Keep')
    })
    it('does not recover missing batches without resolution evidence', () => {
        const result = questions.recoverResolvedAnswers({ draft: {}, choices: record(), snapshot: { batches: [], resolutions: {} } })
        assert.deepEqual(result.choices, record())
    })
})

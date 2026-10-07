import assert from 'node:assert/strict'
import { it } from 'node:test'
import { readFileSync } from 'node:fs'
import { computed, nextTick, ref } from 'vue'
import * as questions from './asyncQuestions.js'

const classify = input => questions.classifyAsyncQuestionSend(input)
const selected = { item_id: 'batch-a', index: 4, kind: 'option', value: 'Yes' }

it('counts a selected answer as message content', () => {
    const result = classify({ text: '', answers: [selected], attachments: [], settingsOnly: true })
    assert.equal(result.settingsOnly, false)
    assert.equal(result.canSend, true)
    assert.equal(result.hasAnswers, true)
})

it('does not count whitespace-only Other as an answer', () => {
    const result = classify({ text: '', answers: [{ ...selected, kind: 'other', value: ' \n\t\u0085' }] })
    assert.equal(result.canSend, false)
    assert.equal(result.hasAnswers, false)
})

it('allows partial answers without requiring every question', () => {
    const result = classify({ text: 'Also keep this.', answers: [selected, { kind: 'other', value: '' }] })
    assert.equal(result.canSend, true)
    assert.equal(result.settingsOnly, false)
})

it('blocks a command with selected answers without changing inputs', () => {
    const input = { text: '/compact', answers: [selected], command: true }
    const previous = structuredClone(input)
    const result = classify(input)
    assert.equal(result.canSend, false)
    assert.equal(result.commandBlocked, true)
    assert.deepEqual(input, previous)
})

it('allows a command after clearing selections', () => {
    const result = classify({ text: '/compact', answers: [], command: true })
    assert.equal(result.commandBlocked, false)
    assert.equal(result.canSend, true)
})

it('disables an empty composer after clearing selections', () => {
    assert.equal(classify({ text: '', answers: [] }).canSend, false)
})

it('keeps settings-only actions separate from messages', () => {
    const result = classify({ text: ' ', answers: [], attachments: [], settingsOnly: true })
    assert.equal(result.settingsOnly, true)
    assert.equal(result.canSend, true)
})

it('counts attachments as message content before settings', () => {
    const result = classify({ text: '', answers: [], attachments: [{}], settingsOnly: true })
    assert.equal(result.settingsOnly, false)
    assert.equal(result.canSend, true)
})

it('counts a direct free-text answer as message content', () => {
    assert.equal(classify({ text: '', answers: [{ ...selected, kind: 'other', value: 'Custom answer' }] }).canSend, true)
})

// Execute production SFC setup functions. Rendering and layout remain in product QA.
function setupSource(path) {
    return readFileSync(new URL(path, import.meta.url), 'utf8')
        .split('<script setup>')[1].split('</script>')[0].replace(/^import .*\n/gm, '')
}

function mountFields(overrides = {}) {
    const props = { questions: [], modelValue: {}, disabled: false, autoFocus: false, ...overrides }
    let mounted
    const setup = new Function('defineProps', 'defineEmits', 'defineExpose', 'nextTick', 'onMounted', 'ref', 'useId', 'canStealFocus', `
        ${setupSource('../components/message/QuestionFields.vue')}
        return { selectOption, clearAnswer, toggleOther, handleOptionKeydown, setPrimaryRef, setTextInputRef }
    `)
    const fields = setup(() => props, () => (event, value) => {
        assert.equal(event, 'update:modelValue')
        props.modelValue = value
    }, () => {}, nextTick, callback => { mounted = callback }, ref, () => 'question-id', () => true)
    return { props, fields, mounted }
}

it('Clear answer restores an unanswered question without clearing another batch index', () => {
    const view = mountFields()
    view.fields.selectOption(4, 'Yes')
    view.fields.selectOption(9, 'No')
    view.fields.clearAnswer(4)
    assert.equal(view.props.modelValue[4], undefined)
    assert.deepEqual(view.props.modelValue[9], { kind: 'option', value: 'No' })
})

it('Enter and Space select a card without reaching a submit shortcut', () => {
    const view = mountFields()
    for (const key of ['Enter', ' ']) {
        let prevented = false, stopped = false
        view.fields.handleOptionKeydown({ key, ctrlKey: true,
            preventDefault() { prevented = true }, stopPropagation() { stopped = true },
        }, 4, { label: 'Yes' })
        assert.equal(prevented, true)
        assert.equal(stopped, true)
        assert.deepEqual(view.props.modelValue[4], { kind: 'option', value: 'Yes' })
    }
})

it('async fields do not steal focus on arrival but explicit Other focuses its input', async () => {
    const view = mountFields()
    let primaryFocus = 0, otherFocus = 0
    view.fields.setPrimaryRef({ focus() { primaryFocus++ } }, true)
    view.mounted()
    await nextTick()
    assert.equal(primaryFocus, 0)
    view.fields.selectOption(4, 'Yes')
    view.fields.setTextInputRef(4, { focus() { otherFocus++ } })
    view.fields.toggleOther(4)
    await nextTick()
    assert.equal(otherFocus, 1)
    assert.deepEqual(view.props.modelValue[4], { kind: 'other', value: '' })
    assert.equal(classify({ answers: Object.values(view.props.modelValue) }).canSend, false)
})

it('disabled shared fields preserve choices', () => {
    const original = { 4: { kind: 'other', value: 'Keep this' } }
    const view = mountFields({ disabled: true, modelValue: original })
    view.fields.selectOption(4, 'Yes')
    view.fields.clearAnswer(4)
    view.fields.toggleOther(4)
    assert.deepEqual(view.props.modelValue, original)
})

function mountBlocking() {
    const props = { sessionId: 'session-a', isResponding: false, pendingRequest: { request_id: 'request-a', tool_input: {
        questions: [
            { id: 'first', question: 'Pick one', isOther: true, options: [{ label: 'Yes' }] },
            { id: 'secret', question: 'Secret answer', isSecret: true, options: [] },
        ],
    } } }
    let draftContract, submitted
    const setup = new Function('defineProps', 'defineEmits', 'computed', 'ref', 'useId', 'watch',
        'usePendingRequestSubmitShortcut', 'usePendingRequestDraft', `
        ${setupSource('../components/session/detail/items/codex/RequestUserInputBody.vue')}
        return { fieldAnswers, allAnswered, submit, dismiss }
    `)
    const host = setup(() => props, () => (event, value) => { submitted = value }, computed, ref,
        () => 'control-id', () => {}, () => {}, contract => { draftContract = contract })
    return { props, host, draftContract, submitted: () => submitted }
}

it('blocking host requires every answer and keeps its native submit envelope', () => {
    const view = mountBlocking()
    view.host.fieldAnswers.value = { 0: { kind: 'option', value: 'Yes' } }
    assert.equal(view.host.allAnswered.value, false)
    view.host.submit()
    assert.equal(view.submitted(), undefined)
    view.host.fieldAnswers.value = { 0: { kind: 'option', value: 'Yes' }, 1: { kind: 'other', value: ' secret ' } }
    assert.equal(view.host.allAnswered.value, true)
    view.host.submit()
    assert.deepEqual(view.submitted(), { tool_name: 'toolRequestUserInput',
        answers: { first: { answers: ['Yes'] }, secret: { answers: ['secret'] } } })
    view.host.dismiss()
    assert.deepEqual(view.submitted(), { tool_name: 'toolRequestUserInput', answers: {} })
})

it('blocking host preserves pending draft schema and excludes secret text from persistence', () => {
    const view = mountBlocking()
    view.draftContract.apply({ selections: {}, otherTexts: { 0: 'Custom', 1: 'Secret' }, otherActive: { 0: true, 1: true } })
    assert.deepEqual(view.host.fieldAnswers.value[0], { kind: 'other', value: 'Custom' })
    assert.deepEqual(view.draftContract.collect(), { selections: {}, otherTexts: { 0: 'Custom' }, otherActive: { 0: true, 1: true } })
})

function mountAsync(widgetEnabled = true) {
    const batch = { item_id: 'batch-a', status: 'ready', questions: [{ index: 9, title: 'Pick one', options: ['Yes'] }] }
    const props = { sessionId: 'session-a', snapshot: { widget_enabled: widgetEnabled, batches: [batch] } }
    let record = { choices: { 'batch-b': { 2: { kind: 'other', value: 'Keep this' } } },
        sourceBatches: { 'batch-b': { item_id: 'batch-b' } }, recoveredIds: ['older'],
        acceptedSendIds: ['accepted-id'], pendingDismissals: { 'batch-c': 'dismiss-id' } }
    const store = {
        getPendingAsyncQuestionIds: () => [],
        getAsyncQuestionDraft: () => record,
        setAsyncQuestionDraft(sessionId, next) {
            assert.equal(sessionId, 'session-a')
            record = next
            return Promise.resolve()
        },
    }
    const setup = new Function('defineProps', 'defineEmits', 'computed', 'useDataStore', 'toast', `
        ${setupSource('../components/message/AsyncQuestions.vue')}
        return { batches, fields, updateChoices }
    `)
    const host = setup(() => props, () => () => {}, computed, () => store, { error() { assert.fail('unexpected storage error') } })
    return { host, batch, record: () => record }
}

it('async host persists original question indexes and the complete source draft record', () => {
    const view = mountAsync()
    assert.equal(view.host.fields(view.batch)[0].index, 9)
    view.host.updateChoices(view.batch, { 9: { kind: 'option', value: 'Yes' } })
    assert.deepEqual(view.record().choices['batch-a'], { 9: { kind: 'option', value: 'Yes' } })
    assert.equal(view.record().choices['batch-b'][2].value, 'Keep this')
    assert.equal(view.record().sourceBatches['batch-a'], view.batch)
    assert.deepEqual(view.record().acceptedSendIds, ['accepted-id'])
    assert.deepEqual(view.record().pendingDismissals, { 'batch-c': 'dismiss-id' })
    assert.deepEqual(view.record().recoveredIds, ['older'])
})

it('widget-disabled async host hides batches and keeps stored answers', () => {
    const view = mountAsync(false)
    assert.deepEqual(view.host.batches.value, [])
    assert.equal(view.record().choices['batch-b'][2].value, 'Keep this')
})

it('failed answer persistence reports the error type without exposing the answer or error message', async () => {
    const warnings = [], errors = []
    const store = {
        getAsyncQuestionDraft: () => ({}),
        getPendingAsyncQuestionIds: () => [],
        setAsyncQuestionDraft: async () => { throw new DOMException('Private diagnostic content', 'NotFoundError') },
    }
    const setup = new Function('defineProps', 'defineEmits', 'computed', 'useDataStore', 'toast', 'console', `
        ${setupSource('../components/message/AsyncQuestions.vue')}
        return updateChoices
    `)
    const updateChoices = setup(() => ({ sessionId: 's', snapshot: { batches: [] } }), () => () => {},
        computed, () => store, { error: message => errors.push(message) }, { warn: (...args) => warnings.push(args) })
    updateChoices({ item_id: 'q1' }, { 0: { kind: 'other', value: 'Private answer' } })
    await new Promise(resolve => setImmediate(resolve))
    assert.deepEqual(warnings, [['Failed to save question answers:', 'NotFoundError']])
    assert.equal(errors.length, 1)
})

it('composer blocks actual slash commands with answers and permits ordinary slash-prefixed text', () => {
    const source = readFileSync(new URL('../components/message/MessageInput.vue', import.meta.url), 'utf8')
    const start = source.indexOf('const isComposerCommand = computed(')
    const end = source.indexOf('const isSettingsOnlyButton =', start)
    const setup = new Function('computed', 'classifyAsyncQuestionSend', 'getProviderHelpers', 'session',
        'messageText', 'asyncQuestionAnswers', 'canSendAttachmentsOnly', 'attachments', 'hasUnappliedChanges', `
        ${source.slice(start, end)}
        return asyncQuestionSendClassification
    `)
    const messageText = ref('/compact')
    const state = setup(computed, questions.classifyAsyncQuestionSend,
        () => ({ getBuiltInCommands: () => [{ name: 'compact' }, { name: 'plan' }, { name: 'goal' }] }),
        ref({ provider: 'codex' }), messageText, ref([selected]), ref(false), ref([]), ref(false))
    assert.equal(state.value.commandBlocked, true)
    messageText.value = '/some/path'
    assert.equal(state.value.commandBlocked, false)
    assert.equal(state.value.canSend, true)
    messageText.value = '/compactify'
    assert.equal(state.value.commandBlocked, false)
    messageText.value = '/goal\nKeep working'
    assert.equal(state.value.commandBlocked, true)
})


it('collapsed composer shows arriving ready questions during active turns without pending readiness', () => {
    const source = readFileSync(new URL('../components/message/MessageInput.vue', import.meta.url), 'utf8')
    const start = source.indexOf('const collapsedLabel = computed(')
    const end = source.indexOf('const collapsedMessageLabel =', start)
    const setup = new Function('computed', 'readyAsyncQuestionCount', 'collapsedMessageLabel', `
        ${source.slice(start, end)}
        return collapsedLabel
    `)
    const count = ref(0)
    const label = setup(computed, count, ref('Your message is waiting'))
    assert.equal(label.value, 'Your message is waiting')
    count.value = 1
    assert.equal(label.value, '1 question ready · Your message is waiting')
    count.value = 2
    assert.equal(label.value, '2 questions ready · Your message is waiting')
    count.value = 0
    assert.equal(label.value, 'Your message is waiting')
})

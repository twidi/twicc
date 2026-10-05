export const ASYNC_QUESTION_RECOVERY_NOTICE = 'These questions were handled elsewhere. Your answers are kept in your draft.'

/** Native question resolution must settle these sends before generic audit or expiry. */
export function retainsAsyncQuestionSend(entry) {
    return !!entry?.acceptancePending || !!(entry?.async_questions ?? entry?.asyncQuestions)?.batch_ids?.length
}

const clone = value => JSON.parse(JSON.stringify(value))
const time = entry => Date.parse(entry.at) * 1000 + Number((entry.at.match(/\.(\d+)/)?.[1] || '').padEnd(6, '0').slice(3, 6))
// Python str.strip includes NEL and excludes BOM. JS trim has different rules.
const hasText = text => /[^\u0009-\u000D\u001C-\u0020\u0085\u00A0\u1680\u2000-\u200A\u2028\u2029\u202F\u205F\u3000]/u.test(text)
/** Classify composer content without changing choices or formatting raw text. */
export function classifyAsyncQuestionSend({ text = '', answers = [], attachments = [], settingsOnly = false, command = false }) {
    const hasAnswers = answers.some(answer => typeof answer?.value === 'string' && hasText(answer.value))
    const hasMessage = !!text.trim() || attachments.length > 0 || hasAnswers
    const commandBlocked = !!command && hasAnswers
    return { hasAnswers, commandBlocked, settingsOnly: !!settingsOnly && !hasMessage,
        canSend: !commandBlocked && (hasMessage || !!settingsOnly) }
}
const compareText = (a, b) => a < b ? -1 : a > b ? 1 : 0
const compareTime = (a, b) => time(a) - time(b) || compareText(a.key ?? a.item_id ?? '', b.key ?? b.item_id ?? '')

// Match Python's source-line anchors. A pairwise line/time comparator can cycle.
function orderedBatches(batches) {
    if (!batches.every(batch => batch.at)) return batches
    const rollouts = new Map(), unlined = []
    for (const batch of batches) {
        if (batch.line == null) unlined.push(batch)
        else {
            const key = batch.data?.rollout_id ?? null
            if (!rollouts.has(key)) rollouts.set(key, [])
            rollouts.get(key).push(batch)
        }
    }
    unlined.sort(compareTime)
    const groups = [...rollouts.values()]
    const first = group => [...group].sort(compareTime)[0]
    groups.sort((a, b) => compareTime(first(a), first(b)))
    const result = []
    let index = 0
    for (const group of groups) {
        for (const anchor of group.sort((a, b) => a.line - b.line || compareTime(a, b))) {
            while (index < unlined.length && time(unlined[index]) <= time(anchor)) result.push(unlined[index++])
            result.push(anchor)
        }
    }
    return [...result, ...unlined.slice(index)]
}

/** Build exactly the backend message. Answers use immutable source indexes. */
export function formatAsyncQuestionMessage(batches, answers, text) {
    const selected = new Map()
    for (const answer of answers) {
        if (typeof answer.value === 'string' && hasText(answer.value)) {
            selected.set(JSON.stringify([answer.item_id, answer.index]), answer.value)
        }
    }
    const sections = []
    for (const batch of orderedBatches(batches)) {
        for (const question of [...batch.questions].sort((a, b) => a.index - b.index)) {
            const value = selected.get(JSON.stringify([batch.item_id, question.index]))
            if (value !== undefined) sections.push(`Question: ${question.title}\nAnswer: ${value}`)
        }
    }
    if (!sections.length) return text
    const message = `Answers to your questions:\n\n${sections.join('\n\n')}`
    return hasText(text) ? `${message}\n\nAdditional message:\n${text}` : message
}

/** `choices` is the complete persisted question-draft record, not its choices map. */
export function recoverResolvedAnswers({ draft = {}, choices = {}, snapshot, pendingSends = {}, pendingDismissals = {} }) {
    const next = clone(choices)
    next.choices ||= {}
    next.sourceBatches ||= {}
    next.recoveredIds ||= []
    const recovered = new Set(next.recoveredIds)
    const batches = [], answers = []
    const sends = pendingSends instanceof Map ? [...pendingSends.entries()] : Object.entries(pendingSends)
    for (const [itemId, selected] of Object.entries(next.choices)) {
        const resolution = snapshot?.resolutions?.[itemId]
        if (!resolution || !['sent', 'dismissed'].includes(resolution.status)) continue
        const requestId = resolution.request_id
        const ownDismiss = resolution.status === 'dismissed' && requestId
            && requestId === (pendingDismissals[itemId] ?? next.pendingDismissals?.[itemId])
        const ownSend = resolution.status === 'sent' && requestId && (next.acceptedSendIds?.includes(requestId)
            || sends.some(([id, send]) => id === requestId && send.status !== 'rejected'))
        const referencing = sends.filter(([, send]) => (send.async_questions ?? send.asyncQuestions)?.batch_ids?.includes(itemId))
        if (!ownDismiss && !ownSend && referencing.some(([, send]) => !['accepted', 'rejected'].includes(send.status))) continue
        const captured = next.acceptedSendAnswers?.[requestId]
            ?? (sends.find(([id]) => id === requestId)?.[1]?.asyncQuestions
                ?? sends.find(([id]) => id === requestId)?.[1]?.async_questions)?.answers
        // Acceptance consumes only exact values carried by that request. Retry can
        // leave newer selections in the composer, including previously empty answers.
        const unsent = ownSend ? Object.entries(selected).filter(([index, answer]) =>
            !captured?.some(value => value.item_id === itemId && value.index === Number(index)
                && value.kind === answer.kind && value.value === answer.value)) : Object.entries(selected)
        if (!ownDismiss && unsent.length && !recovered.has(itemId)) {
            const source = next.sourceBatches[itemId] ?? snapshot.batches?.find(batch => batch.item_id === itemId)
            // Keep answers if their source is unavailable. Never silently lose text.
            if (!source) continue
            batches.push(source)
            for (const [index, answer] of unsent) answers.push({ ...answer, item_id: itemId, index: Number(index) })
        }
        recovered.add(itemId)
        delete next.choices[itemId]
        delete next.sourceBatches[itemId]
        if (next.pendingDismissals) delete next.pendingDismissals[itemId]
    }
    next.recoveredIds = [...recovered]
    const recoveredText = formatAsyncQuestionMessage(batches, answers, '')
    const resultDraft = { ...draft }
    if (recoveredText) resultDraft.message = draft.message ? `${recoveredText}\n\n${draft.message}` : recoveredText
    return { draft: resultDraft, choices: next, recoveredIds: next.recoveredIds,
        notice: recoveredText ? ASYNC_QUESTION_RECOVERY_NOTICE : null }
}

/** Capture the submission boundary. New revisions alone do not change its identity. */
export function prepareAsyncQuestionSend({ snapshot, questionDraft = {}, rawText = '', pendingQuestionIds = [] }) {
    const batches = snapshot?.widget_enabled === false ? [] : (snapshot?.batches || [])
        .filter(batch => batch.status === 'ready' && !pendingQuestionIds.includes(batch.item_id))
    const sourceBatches = clone(batches)
    const answers = batches.flatMap(batch => batch.questions.flatMap(question => {
        const answer = questionDraft.choices?.[batch.item_id]?.[question.index]
        return answer && classifyAsyncQuestionSend({ answers: [answer] }).hasAnswers
            ? [{ ...answer, item_id: batch.item_id, index: question.index }] : []
    }))
    const selected = {}
    for (const answer of answers) {
        selected[answer.item_id] ||= {}
        selected[answer.item_id][answer.index] = { kind: answer.kind, value: answer.value }
    }
    return { rawText, text: formatAsyncQuestionMessage(sourceBatches, answers, rawText), sourceBatches,
        questionDraft: { choices: selected, sourceBatches: Object.fromEntries(sourceBatches.map(b => [b.item_id, b])), recoveredIds: [] },
        ...(batches.length ? { asyncQuestions: { revision: snapshot.revision, batch_ids: batches.map(b => b.item_id), answers } } : {}),
    }
}

/** Report native acceptance before considering another client's recovery. */
export function reconcileSend(input) {
    const entries = input.pendingSends instanceof Map ? [...input.pendingSends] : Object.entries(input.pendingSends || {})
    const acceptedRequestIds = entries.filter(([id, send]) => send.status === 'accepted'
        || Object.values(input.snapshot?.resolutions || {}).some(r => r.status === 'sent' && r.request_id === id))
        .map(([id]) => id)
    const pendingQuestionIds = [...new Set(entries.filter(([id, send]) => !acceptedRequestIds.includes(id) && send.status !== 'rejected')
        .flatMap(([, send]) => (send.asyncQuestions ?? send.async_questions)?.batch_ids || []))]
    const result = recoverResolvedAnswers({ ...input, pendingSends: Object.fromEntries(entries.map(([id, send]) =>
        [id, acceptedRequestIds.includes(id) ? { ...send, status: 'accepted' } : send])) })
    const oldText = input.draft?.message || ''
    const newText = result.draft.message || ''
    const recoveredText = newText === oldText ? '' : oldText ? newText.slice(0, -oldText.length - 2) : newText
    return { ...result, recoveredText, acceptedRequestId: acceptedRequestIds[0] || null, acceptedRequestIds, pendingQuestionIds }
}

export function asyncQuestionRetryState(entry, snapshot) {
    const payload = entry.asyncQuestions ?? entry.async_questions
    const uncertain = entry.code === 'send_uncertain' || entry.status === 'uncertain' || entry.acceptancePending
    const sources = entry.sourceBatches || []
    const ready = !payload || (snapshot?.widget_enabled !== false && payload.batch_ids.every(id => {
        const current = snapshot?.batches?.find(batch => batch.item_id === id && batch.status === 'ready')
        const source = sources.find(batch => batch.item_id === id)
        return current && (!source || JSON.stringify(current.questions) === JSON.stringify(source.questions))
    }))
    return { canRetry: !uncertain && entry.code !== 'async_questions_stale' && !!ready, text: payload ? (entry.rawText ?? '') : entry.text, asyncQuestions: payload }
}

/** Remove only unchanged captured values. Typing during a commit stays in the draft. */
export function consumeAsyncQuestionSend(send, draft = {}, questionDraft = {}) {
    const next = clone(questionDraft); next.choices ||= {}; next.sourceBatches ||= {}
    for (const [id, answers] of Object.entries(send.questionDraft?.choices || {})) {
        for (const [index, answer] of Object.entries(answers)) {
            if (JSON.stringify(next.choices[id]?.[index]) === JSON.stringify(answer)) delete next.choices[id][index]
        }
        if (next.choices[id] && !Object.keys(next.choices[id]).length) { delete next.choices[id]; delete next.sourceBatches[id] }
    }
    const raw = send.rawText || ''
    const current = draft.message || ''
    return { draft: { ...draft, message: raw && current.startsWith(raw) ? current.slice(raw.length) : current }, questionDraft: next }
}

/** Ready failures recover editable choices. Stale failures recover ordinary formatted text. */
export function restoreAsyncQuestionSend(send, draft = {}, questionDraft = {}, snapshot, { forceChoices = false } = {}) {
    const editable = forceChoices || asyncQuestionRetryState(send, snapshot).canRetry
    const next = clone(questionDraft); next.choices ||= {}; next.sourceBatches ||= {}
    const text = editable ? (send.rawText ?? send.text) : send.text
    if (editable) {
        for (const [id, answers] of Object.entries(send.questionDraft?.choices || {})) {
            next.choices[id] = { ...answers, ...next.choices[id] }
            next.sourceBatches[id] ||= clone(send.questionDraft.sourceBatches[id])
        }
    }
    return { draft: { ...draft, message: text ? (draft.message ? `${text}\n\n${draft.message}` : text) : draft.message || '' }, questionDraft: next }
}

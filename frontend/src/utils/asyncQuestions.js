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
        if (!ownDismiss && !ownSend && !recovered.has(itemId)) {
            const source = next.sourceBatches[itemId] ?? snapshot.batches?.find(batch => batch.item_id === itemId)
            // Keep answers if their source is unavailable. Never silently lose text.
            if (!source) continue
            batches.push(source)
            for (const [index, answer] of Object.entries(selected)) answers.push({ ...answer, item_id: itemId, index: Number(index) })
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

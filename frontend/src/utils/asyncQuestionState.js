import { recoverResolvedAnswers, retainsAsyncQuestionSend } from './asyncQuestions.js'

const clone = value => JSON.parse(JSON.stringify(value))
const emptyDraft = () => ({ choices: {}, sourceBatches: {}, recoveredIds: [], pendingDismissals: {} })

/** Production Pinia actions with injectable storage and network boundaries. */
export function createAsyncQuestionActions({ saveMessage, getAll, getAllMessages, recover, fetch, send, uuid, pendingSends, cancelDraftSave, remove }) {
    const writes = new Map()
    const touched = new Set()
    const editedAnswers = new Map()
    const acknowledgements = new Map()
    const committedAcknowledgements = new Set()
    function enqueue(sessionId, write) {
        const previous = writes.get(sessionId) || Promise.resolve()
        const next = previous.catch(() => {}).then(write)
        writes.set(sessionId, next)
        next.finally(() => { if (writes.get(sessionId) === next) writes.delete(sessionId) }).catch(() => {})
        return next
    }
    const persist = (owner, sessionId) => {
        return enqueue(sessionId, async () => {
            let stored
            do {
                const draft = clone(owner.localState.draftMessages[sessionId] || {})
                const record = clone(owner.localState.asyncQuestionDrafts[sessionId] || emptyDraft())
                stored = JSON.stringify([draft, record])
                await recover(sessionId, draft, record)
            } while (stored !== JSON.stringify([owner.localState.draftMessages[sessionId] || {},
                owner.localState.asyncQuestionDrafts[sessionId] || emptyDraft()]))
        })
    }
    return {
        async loadAsyncQuestions(projectId, sessionId) {
            const session = this.sessions[sessionId]
            if (session && (session.provider !== 'codex' || session.type === 'subagent' || session.draft)) return null
            const response = await fetch(`/api/projects/${projectId}/sessions/${sessionId}/async-questions/`)
            if (!response.ok) throw new Error(`Failed to load async questions: ${response.status}`)
            const snapshot = await response.json()
            await this.applyAsyncQuestionSnapshot(sessionId, snapshot)
            return this.localState.asyncQuestionSnapshots[sessionId]
        },

        applyAsyncQuestionSnapshot(sessionId, snapshot) {
            const current = this.localState.asyncQuestionSnapshots[sessionId]
            if (current && snapshot.revision < current.revision) return Promise.resolve(false)
            this.localState.asyncQuestionSnapshots[sessionId] = clone(snapshot)
            const draft = this.localState.asyncQuestionDrafts[sessionId]
            if (draft) {
                for (const batch of snapshot.batches) {
                    if (draft.choices?.[batch.item_id]) draft.sourceBatches[batch.item_id] = clone(batch)
                }
            }
            const reconciled = this.reconcileAsyncQuestionDraft(sessionId)
            if (!this.confirmInflightSend) return reconciled
            return reconciled.then(async result => {
                const sends = pendingSends(sessionId, this)
                const entries = sends instanceof Map ? sends : new Map(Object.entries(sends))
                for (const resolution of Object.values(snapshot.resolutions || {})) {
                    if (resolution.status === 'sent' && entries.has(resolution.request_id)) {
                        await this.acknowledgeInflightSend(sessionId, resolution.request_id)
                    }
                }
                return result
            })
        },

        setAsyncQuestionDraft(sessionId, draft) {
            touched.add(sessionId)
            const record = { ...emptyDraft(), ...clone(draft) }
            // A controls edit cannot accidentally discard durable dismissal/send identities.
            const previous = this.localState.asyncQuestionDrafts[sessionId]
            const identities = editedAnswers.get(sessionId) || new Set()
            for (const choices of [previous?.choices || {}, record.choices]) {
                for (const [itemId, answers] of Object.entries(choices)) {
                    for (const index of Object.keys(answers)) identities.add(JSON.stringify([itemId, index]))
                }
            }
            editedAnswers.set(sessionId, identities)
            record.sourceBatches = { ...previous?.sourceBatches, ...record.sourceBatches }
            record.pendingDismissals = { ...previous?.pendingDismissals, ...record.pendingDismissals }
            record.acceptedSendIds = [...new Set([...(previous?.acceptedSendIds || []), ...(record.acceptedSendIds || [])])]
            record.recoveredIds = [...new Set([...(previous?.recoveredIds || []), ...record.recoveredIds])]
            for (const batch of this.localState.asyncQuestionSnapshots[sessionId]?.batches || []) {
                if (record.choices[batch.item_id]) record.sourceBatches[batch.item_id] = clone(batch)
            }
            this.localState.asyncQuestionDrafts[sessionId] = record
            const saved = persist(this, sessionId)
            // A selection made while the network waits uses the current resolution.
            const reconciled = this.reconcileAsyncQuestionDraft(sessionId)
            return Promise.all([saved, reconciled])
        },

        reconcileAsyncQuestionDraft(sessionId) {
            const record = this.localState.asyncQuestionDrafts[sessionId]
            const snapshot = this.localState.asyncQuestionSnapshots[sessionId]
            if (!record || !snapshot) return Promise.resolve(false)
            const previousDraft = this.localState.draftMessages[sessionId] || {}
            const result = recoverResolvedAnswers({ draft: previousDraft, choices: record, snapshot,
                pendingSends: pendingSends(sessionId, this), pendingDismissals: record.pendingDismissals || {} })
            if (JSON.stringify(result.choices) === JSON.stringify(record)) return Promise.resolve(false)
            touched.add(sessionId)
            this.localState.asyncQuestionDrafts[sessionId] = result.choices
            const changedText = result.draft.message !== previousDraft.message
            if (changedText) {
                cancelDraftSave(sessionId)
                this.localState.draftMessages[sessionId] = result.draft
                this.localState.asyncQuestionNotices[sessionId] = result.notice
                this.localState.draftAppendSignals[sessionId] = (this.localState.draftAppendSignals[sessionId] || 0) + 1
            }
            return persist(this, sessionId).then(() => true)
        },

        persistComposerDraft(sessionId) {
            if (this.localState.asyncQuestionDrafts[sessionId]) return persist(this, sessionId)
            return saveMessage(sessionId, this.localState.draftMessages[sessionId] || {})
        },

        async hydrateAsyncQuestionDrafts() {
            let drafts
            try { drafts = await getAll() }
            catch (error) { console.warn('Failed to load async question drafts:', error); return }
            for (const [sessionId, draft] of Object.entries(drafts)) {
                if (!touched.has(sessionId)) this.localState.asyncQuestionDrafts[sessionId] = { ...emptyDraft(), ...draft }
                else {
                    const current = this.localState.asyncQuestionDrafts[sessionId] || emptyDraft()
                    const merged = { ...emptyDraft(), ...clone(draft), ...current,
                        choices: clone(draft.choices || {}), sourceBatches: { ...draft.sourceBatches, ...current.sourceBatches },
                        recoveredIds: [...new Set([...(draft.recoveredIds || []), ...current.recoveredIds])],
                        pendingDismissals: { ...draft.pendingDismissals, ...current.pendingDismissals },
                        acceptedSendIds: [...new Set([...(draft.acceptedSendIds || []), ...(current.acceptedSendIds || [])])],
                    }
                    for (const identity of editedAnswers.get(sessionId) || []) {
                        const [itemId, index] = JSON.parse(identity)
                        const answer = current.choices[itemId]?.[index]
                        if (answer) { merged.choices[itemId] ||= {}; merged.choices[itemId][index] = answer }
                        else if (merged.choices[itemId]) delete merged.choices[itemId][index]
                    }
                    for (const itemId of merged.recoveredIds) { delete merged.choices[itemId]; delete merged.sourceBatches[itemId] }
                    this.localState.asyncQuestionDrafts[sessionId] = merged
                    await persist(this, sessionId)
                }
                await this.reconcileAsyncQuestionDraft(sessionId)
            }
        },

        async hydrateDraftMessages() {
            let drafts
            try { drafts = await getAllMessages() }
            catch (error) { console.warn('Failed to load draft messages from IndexedDB:', error); return }
            for (const [sessionId, draft] of Object.entries(drafts)) {
                if (!this.localState.draftMessageEdits?.[sessionId]) this.localState.draftMessages[sessionId] = draft
            }
        },

        async dismissAsyncQuestion(projectId, sessionId, itemId) {
            const record = this.localState.asyncQuestionDrafts[sessionId] ||= emptyDraft()
            record.pendingDismissals ||= {}
            const requestId = uuid()
            record.pendingDismissals[itemId] = requestId
            touched.add(sessionId)
            try {
                await persist(this, sessionId)
                if (!await send({ type: 'codex_dismiss_async_question', session_id: sessionId, item_id: itemId, request_id: requestId })) {
                    throw new Error('WebSocket is disconnected')
                }
            } catch (error) {
                // A failed transaction retains source answers and releases only this request.
                await this.failAsyncQuestionDismissal(requestId).catch(() => {})
                throw error
            }
            return requestId
        },

        async handleAsyncQuestionDismissed(message) {
            // The ack request_id correlates transport only. Its snapshot retains the original resolution ID.
            await this.applyAsyncQuestionSnapshot(message.session_id, message.snapshot)
            await this.failAsyncQuestionDismissal(message.request_id)
        },

        async failAsyncQuestionDismissal(requestId) {
            for (const [sessionId, record] of Object.entries(this.localState.asyncQuestionDrafts)) {
                const itemId = Object.keys(record.pendingDismissals || {}).find(id => record.pendingDismissals[id] === requestId)
                if (!itemId) continue
                delete record.pendingDismissals[itemId]
                await persist(this, sessionId)
                await this.reconcileAsyncQuestionDraft(sessionId)
                return true
            }
            return false
        },

        async settleAsyncQuestionSend(sessionId, requestId, status) {
            const record = this.localState.asyncQuestionDrafts[sessionId]
            if (!record) return
            if (status === 'accepted') {
                record.acceptedSendIds = [...new Set([...(record.acceptedSendIds || []), requestId])]
                await persist(this, sessionId)
            }
            await this.reconcileAsyncQuestionDraft(sessionId)
        },

        acknowledgeInflightSend(sessionId, requestId) {
            if (committedAcknowledgements.has(requestId)) {
                this.confirmInflightSend(sessionId, requestId, { acceptancePersisted: true })
                return Promise.resolve()
            }
            const sends = pendingSends(sessionId, this)
            const entry = sends instanceof Map ? sends.get(requestId) : sends[requestId]
            if (!this.localState.asyncQuestionDrafts[sessionId] && !retainsAsyncQuestionSend(entry)) {
                this.confirmInflightSend(sessionId, requestId)
                return Promise.resolve()
            }
            this.localState.asyncQuestionDrafts[sessionId] ||= emptyDraft()
            if (acknowledgements.has(requestId)) return acknowledgements.get(requestId)
            // Acceptance is known now. A storage failure must not become a provider failure.
            this.markInflightSendAccepted(sessionId, requestId)
            const acknowledgement = this.settleAsyncQuestionSend(sessionId, requestId, 'accepted').then(() => {
                committedAcknowledgements.add(requestId)
                this.confirmInflightSend(sessionId, requestId, { acceptancePersisted: true })
            })
            acknowledgements.set(requestId, acknowledgement)
            acknowledgement.finally(() => {
                if (acknowledgements.get(requestId) === acknowledgement) acknowledgements.delete(requestId)
            }).catch(() => {})
            return acknowledgement
        },

        refreshActiveAsyncQuestions() {
            const ids = new Set([...Object.keys(this.localState.asyncQuestionSnapshots), ...Object.keys(this.localState.sessions || {})])
            return Promise.allSettled([...ids].map(sessionId => {
                const session = this.sessions[sessionId]
                return session?.project_id ? this.loadAsyncQuestions(session.project_id, sessionId) : null
            }))
        },

        async deleteAsyncQuestionState(sessionId) {
            delete this.localState.asyncQuestionSnapshots[sessionId]
            delete this.localState.asyncQuestionDrafts[sessionId]
            delete this.localState.asyncQuestionNotices[sessionId]
            if (remove) await enqueue(sessionId, () => remove(sessionId))
        },
    }
}

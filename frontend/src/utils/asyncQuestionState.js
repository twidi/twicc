import { reconcileSend, retainsAsyncQuestionSend, consumeAsyncQuestionSend, restoreAsyncQuestionSend } from './asyncQuestions.js'

const clone = value => JSON.parse(JSON.stringify(value))
const emptyDraft = () => ({ choices: {}, sourceBatches: {}, recoveredIds: [], pendingDismissals: {} })

/** Production Pinia actions with injectable storage and network boundaries. */
export function createAsyncQuestionActions({ saveMessage, getAll, getAllMessages, recover, fetch, send, uuid, pendingSends, cancelDraftSave, remove, stageSend, markDispatched, restoreStaged }) {
    const preparingSends = new Map()
    const getSends = (sessionId, owner) => {
        const existing = pendingSends(sessionId, owner)
        return new Map([...(existing instanceof Map ? existing : Object.entries(existing)),
            ...[...preparingSends].filter(([, entry]) => entry.sessionId === sessionId)])
    }
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
        getPendingAsyncQuestionIds(sessionId) {
            return [...new Set(Object.values(this.localState.asyncQuestionSendLocks?.[sessionId] || {}).flat())]
        },

        lockAsyncQuestionSend(sessionId, requestId, payload) {
            if (!payload?.batch_ids?.length) return
            this.localState.asyncQuestionSendLocks ||= {}
            this.localState.asyncQuestionSendLocks[sessionId] ||= {}
            this.localState.asyncQuestionSendLocks[sessionId][requestId] = [...payload.batch_ids]
        },

        releaseAsyncQuestionSendLock(sessionId, requestId) {
            if (this.localState.asyncQuestionSendLocks?.[sessionId]) delete this.localState.asyncQuestionSendLocks[sessionId][requestId]
        },

        async sendAsyncQuestionMessage(sessionId, projectId, requestId, payload, outgoing, { retryRequestId = null } = {}) {
            const ids = outgoing.asyncQuestions.batch_ids
            if (ids.some(id => this.getPendingAsyncQuestionIds(sessionId).includes(id))) return false
            // Reserve before the first await. Only these batches stop accepting edits.
            this.lockAsyncQuestionSend(sessionId, requestId, outgoing.asyncQuestions)
            const snapshot = clone({ ...outgoing, sessionId, sentAt: Date.now(), status: 'staged', retryRequestId })
            preparingSends.set(requestId, snapshot)
            let staged = false
            try {
                await enqueue(sessionId, async () => {
                    cancelDraftSave(sessionId)
                    const next = retryRequestId
                        ? { draft: this.localState.draftMessages[sessionId] || {}, questionDraft: this.localState.asyncQuestionDrafts[sessionId] || emptyDraft() }
                        : consumeAsyncQuestionSend(snapshot, this.localState.draftMessages[sessionId], this.localState.asyncQuestionDrafts[sessionId])
                    await stageSend(requestId, snapshot, next.draft, next.questionDraft)
                    staged = true
                    if (!retryRequestId) {
                        const current = consumeAsyncQuestionSend(snapshot, this.localState.draftMessages[sessionId], this.localState.asyncQuestionDrafts[sessionId])
                        this.localState.draftMessages[sessionId] = current.draft
                        this.localState.asyncQuestionDrafts[sessionId] = current.questionDraft
                        this.localState.draftAppendSignals[sessionId] = (this.localState.draftAppendSignals[sessionId] || 0) + 1
                    }
                    this.registerOutgoingSend(sessionId, projectId, requestId, { ...snapshot, prePersisted: true })
                    preparingSends.delete(requestId)
                    if (retryRequestId) this.removeFailedSend(sessionId, retryRequestId, { preserveSnapshot: true })
                })
            } catch (error) {
                if (!staged) {
                    preparingSends.delete(requestId)
                    this.releaseAsyncQuestionSendLock(sessionId, requestId)
                    this.reconcileAsyncQuestionDraft(sessionId).catch(() => {})
                }
                throw error
            }
            // Save edits made during staging through the same per-session queue.
            persist(this, sessionId).catch(error => console.warn('Failed to persist edits after question staging:', error))
            let dispatched = false
            try { dispatched = await send(payload) } catch { /* A socket throw means the frame did not leave. */ }
            if (!dispatched) {
                await this.restoreAsyncQuestionSnapshot(sessionId, requestId, snapshot, { forceChoices: true })
                this.cancelStagedOutgoingSend?.(sessionId, requestId)
                this.releaseAsyncQuestionSendLock(sessionId, requestId)
                await this.reconcileAsyncQuestionDraft(sessionId)
                return false
            }
            // Failure here retains a staged (uncertain) snapshot. Never resend automatically.
            try { await markDispatched(requestId) }
            catch (error) { console.warn('Failed to mark question send dispatched:', error) }
            return true
        },

        async restoreAsyncQuestionSnapshot(sessionId, requestId, snapshot, options) {
            if (snapshot.medias?.length) await this.restoreDraftAttachments?.(sessionId, snapshot.medias, { strict: true })
            await enqueue(sessionId, async () => {
                cancelDraftSave(sessionId)
                const restore = () => restoreAsyncQuestionSend(snapshot, this.localState.draftMessages[sessionId],
                    this.localState.asyncQuestionDrafts[sessionId], this.localState.asyncQuestionSnapshots[sessionId], options)
                const next = restore()
                await restoreStaged(requestId, sessionId, next.draft, next.questionDraft)
                // A user can keep typing while IndexedDB commits.
                const current = restore()
                this.localState.draftMessages[sessionId] = current.draft
                this.localState.asyncQuestionDrafts[sessionId] = current.questionDraft
                this.localState.draftAppendSignals[sessionId] = (this.localState.draftAppendSignals[sessionId] || 0) + 1
            })
            persist(this, sessionId).catch(error => console.warn('Failed to save edits after message restoration:', error))
        },

        async editAsyncQuestionFailure(sessionId, requestId) {
            const entry = this.getFailedSend(sessionId, requestId)
            if (!entry || entry.code === 'send_uncertain') return false
            await this.restoreAsyncQuestionSnapshot(sessionId, requestId, entry)
            this.removeFailedSend(sessionId, requestId, { preserveSnapshot: true })
            return true
        },

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
            // Native acceptance settles before any external-resolution recovery.
            const result = reconcileSend({ choices: draft || emptyDraft(), snapshot, pendingSends: getSends(sessionId, this) })
            const accepted = this.confirmInflightSend
                ? Promise.all(result.acceptedRequestIds.map(id => this.acknowledgeInflightSend(sessionId, id)))
                : Promise.resolve()
            return accepted.then(() => this.reconcileAsyncQuestionDraft(sessionId))
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
            const result = reconcileSend({ draft: previousDraft, choices: record, snapshot,
                pendingSends: getSends(sessionId, this), pendingDismissals: record.pendingDismissals || {} })
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
            if (this.getPendingAsyncQuestionIds(sessionId).includes(itemId)) return null
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
            const sends = getSends(sessionId, this)
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
                this.releaseAsyncQuestionSendLock(sessionId, requestId)
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

/** Browser-owned one-shot entries. This module never reads server session rows. */
export const isLaunchedEphemeral = session => session?.ephemeral === true && !session.draft

export function ephemeralFields(session) {
    const fields = { draft: session.draft !== false && !session.ephemeralPhase }
    for (const key of ['ephemeral', 'ephemeralPhase', 'ephemeralStartedAt', 'ephemeralDraftId', 'ephemeralBound', 'ephemeralPrompt', 'ephemeralResult']) {
        if (session[key] !== undefined) fields[key] = JSON.parse(JSON.stringify(session[key]))
    }
    return fields
}

/** Shared whole-record writer for both normal drafts and launched ephemeral entries. */
export function serializeDraftSession(session) {
    const record = {
        ...ephemeralFields(session), projectId: session.project_id, title: session.title,
        provider: session.provider, hybrid: session.hybrid, layout: session.layout,
        peerMessageId: session.peerMessageId,
    }
    for (const key of ['selected_model', 'permission_mode', 'effort', 'thinking_enabled', 'claude_in_chrome', 'fast_mode', 'context_max']) record[key] = session[key] ?? null
    return JSON.parse(JSON.stringify(record))
}

export function summarizeEphemeralAttachments(medias = []) {
    return medias.map(media => ({
        name: media.name || media.filename || 'Attachment',
        media_type: media.mimeType || media.media_type || '',
        kind: media.type === 'image' ? 'image' : 'document',
    }))
}

/** Send failure consumption is shared with Pinia, including persistent snapshot disposal. */
export function createSendFailureActions(inflightSends) {
    return {
        dropDiscardedSendFailure(requestId, entry) {
            if (!this.isEphemeralDiscarded(entry.sessionId)
                && !(entry.ephemeral && !this.sessions[this.localState.draftAliases[entry.sessionId] || entry.sessionId])) return false
            this._dropInflightSend(requestId)
            return true
        },
        failInflightSend(requestId, info) {
            const entry = inflightSends.get(requestId)
            if (!entry) return false
            inflightSends.delete(requestId)
            if (!this.dropDiscardedSendFailure(requestId, entry)) this._applySendFailure(requestId, entry, info)
            return true
        },
    }
}

/** Dependencies are storage/transport boundaries; actions run unchanged in Pinia and tests. */
export function createEphemeralActions({ saveControl, deleteControl, deleteSession, clearContent, stop, navigate, buildPrompt, rekeySession, uuid }) {
    const persist = promise => Promise.resolve(promise).catch(error => console.warn('Ephemeral storage operation failed:', error))
    return {
        isEphemeralDiscarded(id) {
            const session = this.sessions[id]
            const originalId = session?.ephemeralDraftId || id
            const alias = this.localState.draftAliases[id]
            return Object.entries(this.localState.ephemeralControls).some(([key, control]) =>
                control.discard && (key === id || key === originalId || control.canonicalId === id || (alias && control.canonicalId === alias)))
        },
        isEphemeralSessionId(id) {
            return isLaunchedEphemeral(this.sessions[id]) || !!this.localState.ephemeralIds?.[id]
                || !!this.localState.ephemeralControls[id]
        },
        setDraftEphemeral(id, value) {
            const session = this.sessions[id]
            if (!session?.draft) return
            session.ephemeral = !!value
            if (value) session.hybrid = false
            this._saveDraftToIndexedDB(id)
        },
        applyCreationSendMode(payload) {
            const session = this.sessions[payload.session_id]
            if (!session?.draft) return payload
            payload.hybrid = session.hybrid === true
            payload.ephemeral = session.ephemeral === true
            if (payload.ephemeral) delete payload.layout
            else payload.layout = session.layout || {}
            if (session.title) payload.title = session.title
            return payload
        },
        promoteEphemeralSession(id, { text, medias = [] }) {
            const session = this.sessions[id]
            if (!session?.draft || !session.ephemeral) return false
            const known = this.localState.ephemeralIds ||= {}
            known[id] = true
            Object.assign(session, {
                draft: false, ephemeralPhase: 'running', ephemeralStartedAt: new Date().toISOString(),
                ephemeralDraftId: id, ephemeralBound: false,
                ephemeralPrompt: { text, attachments: summarizeEphemeralAttachments(medias) },
            })
            this.localState.optimisticMessages[id] = buildPrompt(session, session.ephemeralPrompt)
            this._saveDraftToIndexedDB(id)
            this.recomputeVisualItems(id)
            return true
        },
        async bindEphemeralSession(draftId, id) {
            const controls = this.localState.ephemeralControls
            const control = controls[draftId] || Object.values(controls).find(value => value.canonicalId === draftId)
            const session = this.sessions[draftId]
            if (!isLaunchedEphemeral(session) && !control) return false
            const known = this.localState.ephemeralIds ||= {}
            known[draftId] = true
            known[id] = true
            if (control) {
                control.canonicalId = id
                persist(saveControl(control.draftId, control))
                persist(stop(id))
            }
            if (!session || control?.discard) return true
            session.ephemeralBound = true
            this.localState.draftAliases[draftId] = id
            if (draftId !== id) {
                this.sessions[id] = { ...session, id }
                delete this.sessions[draftId]
                for (const map of [this.processStates, this.sessionItems, ...Object.values(this.localState)]) {
                    if (map && typeof map === 'object' && !Array.isArray(map) && Object.hasOwn(map, draftId)
                        && map !== controls && map !== this.localState.ephemeralIds && map !== this.localState.draftAliases) {
                        map[id] = map[draftId]
                        delete map[draftId]
                    }
                }
                this._rekeyEphemeralInflight(draftId, id)
                this.rekeyMruSession(draftId, id)
                if (rekeySession) persist(rekeySession(draftId, id, serializeDraftSession(this.sessions[id])))
                else persist(deleteSession(draftId))
            }
            this._saveDraftToIndexedDB(id)
            this.recomputeVisualItems(id)
            await navigate(draftId, id, session.project_id)
            return true
        },
        recoverEphemeralDraft(id) {
            const session = this.sessions[id]
            if (!isLaunchedEphemeral(session)) return id
            const newId = uuid()
            const draft = { ...session, id: newId, draft: true }
            for (const key of ['ephemeralPhase', 'ephemeralStartedAt', 'ephemeralDraftId', 'ephemeralBound', 'ephemeralPrompt', 'ephemeralResult']) delete draft[key]
            this.sessions[newId] = draft
            delete this.sessions[id]
            for (const map of Object.values(this.localState)) {
                if (map && typeof map === 'object' && !Array.isArray(map)
                    && map !== this.localState.draftAliases && map !== this.localState.ephemeralControls
                    && map !== this.localState.ephemeralIds && Object.hasOwn(map, id)) {
                    map[newId] = map[id]
                    delete map[id]
                }
            }
            delete this.localState.optimisticMessages[newId]
            delete this.processStates[id]
            delete this.sessionItems[id]
            this.localState.draftAliases[id] = newId
            this._rekeyEphemeralInflight(id, newId)
            this.rekeyMruSession(id, newId)
            if (rekeySession) persist(rekeySession(id, newId, serializeDraftSession(draft)))
            else persist(deleteSession(id))
            this._saveDraftToIndexedDB(newId)
            persist(navigate(id, newId, session.project_id))
            return newId
        },
        receiveEphemeralResult(frame) {
            const session = this.sessions[frame.session_id]
            if (!isLaunchedEphemeral(session) || session.ephemeralResult) return
            if (!['done', 'error', 'stopped'].includes(frame.status)) return
            session.ephemeralPhase = frame.status
            session.ephemeralResult = {
                text: frame.text || '', error: frame.error || null,
                cost_usd: frame.cost_usd ?? null, duration_ms: frame.duration_ms ?? null,
                finished_at: frame.finished_at || new Date().toISOString(),
            }
            this.clearEphemeralControl(frame.session_id)
            this._saveDraftToIndexedDB(frame.session_id)
            this.recomputeVisualItems(frame.session_id)
        },
        failEphemeralAdmission(frame) {
            const id = this.localState.draftAliases[frame.draft_session_id] || frame.draft_session_id
            this.clearEphemeralControl(frame.draft_session_id)
            const session = this.sessions[id]
            if (!isLaunchedEphemeral(session) || session.ephemeralPhase !== 'running') return
            this.receiveEphemeralResult({ session_id: id, status: 'error', error: frame.error || 'The ephemeral run could not start.' })
        },
        clearEphemeralControl(id) {
            for (const [key, control] of Object.entries(this.localState.ephemeralControls)) {
                if (key === id || control.canonicalId === id) {
                    delete this.localState.ephemeralControls[key]
                    persist(deleteControl(key))
                }
            }
        },
        async stopEphemeralSession(id) {
            const session = this.sessions[id]
            if (!isLaunchedEphemeral(session) || session.ephemeralPhase !== 'running') return
            const draftId = session.ephemeralDraftId || id
            const control = this.localState.ephemeralControls[draftId] ||= { draftId, discard: false }
            if (session.ephemeralBound) control.canonicalId = id
            await persist(saveControl(draftId, control))
            if (control.canonicalId) await stop(control.canonicalId)
        },
        purgeEphemeralContent(ids) {
            const writes = []
            for (const id of new Set(ids.filter(Boolean))) {
                this._clearEphemeralInflight(id)
                delete this.sessions[id]
                delete this.sessionItems[id]
                delete this.processStates[id]
                for (const [key, map] of Object.entries(this.localState)) {
                    if (key !== 'ephemeralControls' && key !== 'ephemeralIds' && map && typeof map === 'object' && !Array.isArray(map)) delete map[id]
                }
                this.removeMruSession(id)
                writes.push(persist(deleteSession(id)), persist(clearContent(id)))
            }
            return Promise.all(writes)
        },
        async discardEphemeralSession(id) {
            const session = this.sessions[id]
            if (!session || (!session.draft && !isLaunchedEphemeral(session))) return
            const draftId = session.ephemeralDraftId || id
            let canonicalId = null
            if (session.ephemeralPhase === 'running') {
                const control = { draftId, discard: true, ...(session.ephemeralBound ? { canonicalId: id } : {}) }
                this.localState.ephemeralControls[draftId] = control
                persist(saveControl(draftId, control))
                canonicalId = control.canonicalId
            }
            // Retire content synchronously. A late error during Stop cannot restore it.
            const cleanup = this.purgeEphemeralContent([id, draftId])
            const stopping = canonicalId ? persist(stop(canonicalId)) : Promise.resolve()
            await cleanup
            await navigate(id, null, session.project_id)
            await stopping
        },
        async reconcileEphemeralProcesses(processes, admissions = []) {
            const live = new Set(processes.map(process => process.session_id))
            const pending = new Set(admissions.map(entry => entry.draft_session_id || entry.session_id))
            for (const process of processes) {
                if (process.extra?.ephemeral) {
                    const known = this.localState.ephemeralIds ||= {}
                    known[process.session_id] = true
                    this.bindEphemeralSession(process.extra.ephemeral_draft_id || process.session_id, process.session_id)
                }
            }
            for (const [id, session] of Object.entries(this.sessions)) {
                if (isLaunchedEphemeral(session) && session.ephemeralPhase === 'running'
                    && !live.has(id) && !pending.has(session.ephemeralDraftId || id)) {
                    session.ephemeralPhase = 'lost'
                    this._saveDraftToIndexedDB(id)
                    this.recomputeVisualItems(id)
                }
            }
            for (const [key, control] of Object.entries(this.localState.ephemeralControls)) {
                if (!pending.has(key) && !live.has(control.canonicalId) && !live.has(key)) this.clearEphemeralControl(key)
            }
        },
    }
}

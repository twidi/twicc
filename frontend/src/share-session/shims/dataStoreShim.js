import { agentLinkState, setAgentLink, markAgentStopped, markAgentIdle, beginAgentFetch, applyAgentSnapshot, rootAgentToolLine } from '../../utils/agentLinkIndex'
// Read-only mirror of the SPA data store for the share bundle. Only the surface
// the reused transcript components actually touch is implemented; anything else
// throws in dev so drift is caught, and no-ops the write surface (failed sends,
// api-error recovery) that read-only mode never exercises.
import { defineStore } from 'pinia'
import { markRaw } from 'vue'
import { DISPLAY_LEVEL, SYNTHETIC_ITEM } from '../../constants'
import { computeVisualItems, insertDaySeparators, visualItemEqual } from '../../utils/visualItems'
import { getParsedContent, setParsedContent, clearParsedContent } from '../../utils/parsedContent'
import { shareApi } from './shareApi'
import { useSettingsStore } from '../../stores/settings' // aliased to settingsStoreShim

function rangesToQS(ranges) {
    const params = new URLSearchParams()
    for (const range of ranges) {
        if (typeof range === 'number' || typeof range === 'string') {
            params.append('range', String(range))
        } else if (Array.isArray(range)) {
            const [lo, hi] = range
            params.append('range', `${lo ?? ''}:${hi ?? ''}`)
        }
    }
    return params.toString()
}

export const useDataStore = defineStore('shareData', {
    state: () => ({
        // The single shared session (plus subagents keyed by id).
        sessions: {},            // id -> session-ish meta
        sessionItems: {},        // id -> [{ line_num, content, display_level, group_head, group_tail, kind, timestamp }]
        visualItems: {},         // id -> stabilized visual items
        expandedGroups: {},      // id -> [groupHeadLineNum]
        internalExpandedGroups: {},
        detailedBlocks: {},      // id -> [userMessageLineNum]
        openDetails: {},         // id -> { key: bool }
        ...agentLinkState(),          // id -> { toolId: { agentId, isBackground, toolUseLineNum, slug } }
        toolStates: {},          // id -> { toolId: {...} }
        liveTurns: {},           // id -> bool (live share: root session in assistant_turn)
        _cache: {},              // id -> Map for visual-item stabilization
    }),
    getters: {
        getSession: (s) => (id) => s.sessions[id] || null,
        getProject: () => () => null,
        getSessionItems: (s) => (id) => s.sessionItems[id] || [],
        getSessionItem: (s) => (id, lineNum) => {
            const items = s.sessionItems[id]
            if (!items || lineNum < 1) return null
            return items[lineNum - 1] || null
        },
        getSessionVisualItems: (s) => (id) => s.visualItems[id] || [],
        // No per-session debug override in a share (the dev-mode toggle has no
        // counterpart here), so the viewer's mode is always the effective one.
        getEffectiveDisplayMode: () => () => useSettingsStore().getDisplayMode,
        getExpandedGroups: (s) => (id) => s.expandedGroups[id] || [],
        getInternalExpandedGroups: (s) => (id, lineNum) => (s.internalExpandedGroups[id]?.[lineNum]) || [],
        isBlockDetailed: (s) => (id, u) => (s.detailedBlocks[id] || []).includes(u),
        isDetailOpen: (s) => (id, key) => !!s.openDetails[id]?.[key],
        getAgentLinkInfo: (s) => (id) => s.agentLinkIndex[id] || null,
        getRootAgentToolUseLineNum: (s) => (root, id) => rootAgentToolLine(s, root, id),
        getAgentLink: (s) => (id, toolId) => s.agentLinks[id]?.[toolId],
        getAgentToolUseLineNum: (s) => (parentId, subId) => {
            const links = s.agentLinks[parentId]
            if (!links) return null
            for (const l of Object.values(links)) if (l.agentId === subId) return l.toolUseLineNum ?? null
            return null
        },
        getWorkflowLink: () => () => undefined,          // no "View Workflow" in shares
        getToolState: (s) => (id, toolId) => s.toolStates[id]?.[toolId] || null,
        getProcessState: () => () => null,               // never live → no spinners/stop
        getPendingRequests: () => () => [],
        isItemLive: () => () => false,
        isStartupInProgress: () => () => false,
        getProjectIndicatorScopeIds: () => () => [],
        // failed-send / api-error read surface (unused in read-only)
        getFailedSend: () => () => null,
        apiErrorRecovery: () => () => null,
    },
    actions: {
        // ── Loading ──────────────────────────────────────────────────────
        async loadSessionMetadata(_projectId, sessionId, parentSessionId = null) {
            try { return await shareApi().fetchItemsMetadata(parentSessionId ? sessionId : null) }
            catch { return null }
        },
        initSessionItemsFromMetadata(sessionId, metadata) {
            // A server-filtered share delivers a SPARSE set of line_nums (the display
            // ceiling drops debug/over-ceiling lines), so place each item at line_num-1
            // to stay aligned with content-fill / getSessionItem (both line_num-1 keyed);
            // holes are compacted away in recomputeVisualItems. The SPA can .map() densely
            // only because its metadata covers every line — ours does not.
            //
            // MERGE, never replace: on a live share, WS items can land while the
            // initial metadata fetch is in flight — both inside the snapshot (keep
            // their content) and beyond its tail (keep the rows). A content-bearing
            // entry is always at least as fresh as the metadata stub.
            const arr = this.sessionItems[sessionId] || (this.sessionItems[sessionId] = [])
            for (const m of metadata) {
                const i = m.line_num - 1
                if (arr[i] && arr[i].content != null) continue
                arr[i] = {
                    line_num: m.line_num, display_level: m.display_level,
                    group_head: m.group_head, group_tail: m.group_tail,
                    kind: m.kind, timestamp: m.timestamp ?? null, content: null,
                }
            }
            this.recomputeVisualItems(sessionId)
        },
        updateSessionItemsContent(sessionId, items) {
            const arr = this.sessionItems[sessionId]
            if (!arr) return
            for (const it of items) {
                const i = it.line_num - 1
                if (!arr[i]) continue
                arr[i].content = it.content
                clearParsedContent(arr[i])
                if (it.display_level != null) arr[i].display_level = it.display_level
                if (it.group_head != null) arr[i].group_head = it.group_head
                if (it.group_tail != null) arr[i].group_tail = it.group_tail
                if (it.kind !== undefined) arr[i].kind = it.kind
                if (it.timestamp !== undefined) arr[i].timestamp = it.timestamp
            }
            this.recomputeVisualItems(sessionId)
        },
        addSessionItems(sessionId, items) {
            const arr = this.sessionItems[sessionId] || (this.sessionItems[sessionId] = [])
            for (const it of items) {
                const i = it.line_num - 1
                arr[i] = { ...it, content: it.content ?? null }
                clearParsedContent(arr[i])
            }
            this.recomputeVisualItems(sessionId)
        },
        async loadSessionItemsRanges(_projectId, sessionId, ranges, parentSessionId = null) {
            if (!ranges?.length) return true
            const qs = rangesToQS(ranges)
            if (!qs) return false
            try {
                const items = await shareApi().fetchItems(qs, parentSessionId ? sessionId : null)
                this.addSessionItems(sessionId, items)
                return true
            } catch { return false }
        },
        areSessionItemsFetched(sessionId) { return !!this.sessionItems[sessionId] },

        // ── Tool states (same shape as the SPA store: drives running spinners,
        // error rendering and the extra-based helpers like Monitor/exec chains) ──
        async fetchToolStates(_projectId, sessionId, parentSessionId = null) {
            try {
                const data = await shareApi().fetchToolStates(parentSessionId ? sessionId : null)
                const map = this.toolStates[sessionId] || (this.toolStates[sessionId] = {})
                for (const [toolUseId, st] of Object.entries(data.tools || {})) {
                    // A WS share_tool_state may have landed while this snapshot was in
                    // flight; never regress an entry to fewer results (a completed tool
                    // never re-broadcasts, so a regression would stick until reload).
                    const cur = map[toolUseId]
                    if (cur && (cur.resultCount ?? 0) > (st.result_count ?? 0)) continue
                    map[toolUseId] = {
                        resultCount: st.result_count,
                        completedAt: st.completed_at,
                        error: st.error ?? null,
                        extra: st.extra ?? null,
                        toolResultLineNums: Array.isArray(st.tool_result_line_nums) ? st.tool_result_line_nums : [],
                    }
                }
            } catch { /* keep as-is — frozen transcripts gate the spinners off anyway */ }
        },
        setToolState(sessionId, toolUseId, resultCount, completedAt, error = null, extra = null, toolResultLineNums = []) {
            const map = this.toolStates[sessionId] || (this.toolStates[sessionId] = {})
            map[toolUseId] = { resultCount, completedAt, error, extra, toolResultLineNums }
        },

        // ── Visual items (simplified: no streaming/optimistic/working/failed) ──
        recomputeVisualItems(sessionId) {
            // Compact the sparse (line_num-1 keyed) array before computeVisualItems,
            // which iterates with for..of and would choke on holes.
            const items = (this.sessionItems[sessionId] || []).filter(Boolean)
            const isAssistantTurn = !!this.liveTurns[sessionId]
            if (!items.length && !isAssistantTurn) {
                this.visualItems[sessionId] = []; this._cache[sessionId] = new Map(); return
            }
            const settings = useSettingsStore()
            const mode = settings.getDisplayMode
            const expanded = this.expandedGroups[sessionId] || []
            const detailed = new Set(this.detailedBlocks[sessionId] || [])

            // Live share: append the reused "<Provider> is thinking" synthetic
            // message during an assistant turn. Empty content (no label/tools) makes
            // WorkingAssistantMessage render its default "thinking" phrase. liveTurns
            // is set only by the WS process_state path, so snapshots never inject it.
            let allItems = items
            if (isAssistantTurn) {
                const { lineNum, kind: syntheticKind } = SYNTHETIC_ITEM.WORKING_ASSISTANT_MESSAGE
                const working = {
                    line_num: lineNum, content: null, kind: 'assistant_message', syntheticKind,
                    display_level: DISPLAY_LEVEL.ALWAYS, group_head: null, group_tail: null,
                }
                setParsedContent(working, {
                    type: 'assistant', syntheticKind,
                    label: null, tools: [], lastStartedToolId: null, lastToolVisible: true,
                    message: { role: 'assistant', content: [] },
                })
                allItems = [...items, working]
            }

            const vis = computeVisualItems(allItems, mode, expanded, isAssistantTurn, detailed)

            // Reorder the /compact command before its compact_summary (they land in
            // swapped JSONL order). Mirrors the SPA store; gated on the seeded flag.
            if (this.sessions[sessionId]?.compacted) {
                for (let i = 0; i < vis.length; i++) {
                    if (vis[i].kind !== 'compact_summary') continue
                    for (let j = i + 1; j < Math.min(i + 10, vis.length); j++) {
                        if (vis[j].kind !== 'user_message') continue
                        const text = getParsedContent(vis[j])?.message?.content
                        if (typeof text === 'string' && text.includes('<command-name>/compact</command-name>')) {
                            const [moved] = vis.splice(j, 1); vis.splice(i, 0, moved)
                        }
                        break  // only inspect the first user_message after compact_summary
                    }
                }
            }

            // computeVisualItems doesn't copy syntheticKind onto its output — re-tag
            // the working message so SessionItem dispatches to WorkingAssistantMessage.
            if (isAssistantTurn) {
                for (let i = vis.length - 1; i >= 0; i--) {
                    if (vis[i].lineNum === SYNTHETIC_ITEM.WORKING_ASSISTANT_MESSAGE.lineNum) {
                        vis[i].syntheticKind = SYNTHETIC_ITEM.WORKING_ASSISTANT_MESSAGE.kind; break
                    }
                    if (vis[i].lineNum >= 0) break  // synthetics sit at the end
                }
            }

            for (let i = 0; i < vis.length; i++) {
                const isUser = vis[i].kind === 'user_message'
                const prevUser = i > 0 ? vis[i - 1].kind === 'user_message' : null
                const nextUser = i < vis.length - 1 ? vis[i + 1].kind === 'user_message' : null
                vis[i].isBlockStart = i === 0 || isUser !== prevUser
                vis[i].isBlockEnd = i === vis.length - 1 || isUser !== nextUser
            }
            const render = settings.areMessageTimestampsShown ? insertDaySeparators(vis) : vis
            const cache = this._cache[sessionId] || new Map()
            const next = new Map()
            const stable = render.map((vi) => {
                const cached = cache.get(vi.lineNum)
                if (visualItemEqual(cached, vi)) {
                    const p = getParsedContent(vi); if (p !== null) setParsedContent(cached, p)
                    next.set(vi.lineNum, cached); return cached
                }
                const p = getParsedContent(vi); if (p !== null) setParsedContent(vi, p)
                next.set(vi.lineNum, vi); return vi
            })
            this._cache[sessionId] = next
            this.visualItems[sessionId] = stable
        },

        // ── Toggles / detail state (persist within the tab session) ──────
        toggleExpandedGroup(sessionId, head) {
            const arr = this.expandedGroups[sessionId] || (this.expandedGroups[sessionId] = [])
            const i = arr.indexOf(head)
            if (i >= 0) arr.splice(i, 1); else arr.push(head)
            this.recomputeVisualItems(sessionId)
        },
        toggleInternalExpandedGroup(sessionId, lineNum, startIndex) {
            const s = this.internalExpandedGroups[sessionId] || (this.internalExpandedGroups[sessionId] = {})
            const arr = s[lineNum] || (s[lineNum] = [])
            const i = arr.indexOf(startIndex)
            if (i >= 0) arr.splice(i, 1); else arr.push(startIndex)
        },
        toggleBlockDetailedMode(sessionId, u) {
            const arr = this.detailedBlocks[sessionId] || (this.detailedBlocks[sessionId] = [])
            const i = arr.indexOf(u)
            if (i >= 0) arr.splice(i, 1); else arr.push(u)
            this.recomputeVisualItems(sessionId)
        },
        setDetailOpen(sessionId, key, open) {
            const s = this.openDetails[sessionId] || (this.openDetails[sessionId] = {})
            if (open) s[key] = true; else delete s[key]
        },

        // ── Seed helpers used by ShareSessionApp ─────────────────────────
        setSession(session) { this.sessions[session.id] = markRaw(session) },
        beginAgentFetch(root) { return beginAgentFetch(this, root) },
        applyAgentSnapshot(root, links, token) { return applyAgentSnapshot(this, root, links, token) },
        setAgentLinks(sessionId, links) {
            this.applyAgentSnapshot(sessionId, links, this.beginAgentFetch(sessionId))
        },
        markAgentStopped(id, stoppedAt, root) { markAgentStopped(this, id, stoppedAt, root) },
        markAgentIdle(id, stoppedAt, root) {
            markAgentIdle(this, id, stoppedAt)
            this.setSession({ ...this.sessions[id], id, parent_session_id: root,
                provider: this.sessions[id]?.provider || this.sessions[root]?.provider,
                last_stopped_at: stoppedAt ?? null,
            })
        },
        addAgentLink(root, link) {
            setAgentLink(this, link.owner_session_id || root, link.tool_use_id, {
                agentId: link.agent_id, rootSessionId: root, isBackground: link.is_background,
                toolUseLineNum: link.tool_use_line_num, slug: link.agent_slug ?? null,
                startedAt: link.started_at ?? null, stoppedAt: link.stopped_at ?? null,
                agentStoppedAt: link.agent_stopped_at ?? null, running: link.running,
                displayName: link.display_name ?? null,
            })
        },
        // Live: root session entered/left an assistant turn — drives the reused
        // "<Provider> is thinking" synthetic message via recomputeVisualItems.
        setLiveAssistantTurn(sessionId, active) {
            if (!!this.liveTurns[sessionId] === !!active) return
            this.liveTurns[sessionId] = !!active
            this.recomputeVisualItems(sessionId)
        },

        // ── No-op write surface (statically imported by reused components) ──
        registerOutgoingSend() {}, removeFailedSend() {}, restoreDraftAttachments() {},
        setProcessState() {}, markItemsLive() {}, clearEndedStreamingBlocks() {},
        auditInflightSends() {}, ensureSessionItemsCoverage() { return Promise.resolve() },
    },
})

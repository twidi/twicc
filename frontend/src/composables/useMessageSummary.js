import { computed, inject, markRaw, nextTick, onScopeDispose, reactive, shallowRef, toValue, watch } from 'vue'
import { summarySourceKey, useSummariesStore } from '../stores/summaries.js'
import { useSettingsStore } from '../stores/settings.js'
import { useTextToSpeechStore } from '../stores/textToSpeech.js'
import { summaryGenerationAvailability } from '../utils/ttsConfig.js'
import { toast } from './useToast'

export const MESSAGE_SUMMARY_CONTEXT = Symbol('message-summary')
export const MESSAGE_SUMMARY_VISIT = Symbol('message-summary-visit')

// The list owns these choices for this visit, independently of virtualized row lifetimes. Nothing is asked
// for a message until a click: most messages never have a summary. Texts live in the store, a group only
// points at the saved one it shows.
export function createMessageSummaryVisit(reveal = () => {}, holdScroll = () => {}) {
    return { groups: reactive(new Map()), reveal, holdScroll }
}

export function useMessageSummary(sourceRequest, visit = null) {
    const store = useSummariesStore()
    const settings = useSettingsStore(), speech = useTextToSpeechStore()
    const routing = computed(() => summaryGenerationAvailability(settings.summaryModel, settings.enabledProviders, speech.keyPresent,
        descriptor.value?.openrouter_eligible))
    const sharedVisit = visit ?? inject(MESSAGE_SUMMARY_VISIT, null)
    const scope = sharedVisit ?? createMessageSummaryVisit()
    const source = computed(() => toValue(sourceRequest))
    const owned = new Set()
    let operation = 0
    const fresh = computed(() => source.value ? store.descriptorFor(source.value) : null)
    // An invalidation clears the store descriptor until the next lookup answers.
    // Keep the last answer for that window: only a different answer can change what the row shows.
    const retained = shallowRef(null)
    watch(fresh, current => {
        if (current) retained.value = { key: summarySourceKey(source.value), descriptor: current }
    }, { immediate: true, flush: 'sync' })
    const descriptor = computed(() => {
        if (fresh.value || !source.value) return fresh.value
        return retained.value?.key === summarySourceKey(source.value) ? retained.value.descriptor : null
    })
    const group = computed(() => {
        const d = descriptor.value
        if (!d?.source_key) return null
        const key = JSON.stringify([d.session_id, d.source_key, d.source_hash])
        if (!scope.groups.has(key)) scope.groups.set(key, { view: 'original', savedId: null, requested: null, error: null, loading: false })
        return scope.groups.get(key)
    })
    const generation = computed(() => {
        const current = group.value
        const entry = current?.requested?.entry ?? (source.value && store.entryFor(source.value))
        const d = entry?.descriptor
        if (d && (d.source_key !== descriptor.value?.source_key || d.source_hash !== descriptor.value?.source_hash
            || d.policy_key !== descriptor.value?.policy_key)) return null
        return entry || null
    })
    const saved = computed(() => {
        const id = group.value?.savedId
        const summary = id == null ? null : store.savedFor(id)
        return summary && { ...summary, previousPolicy: summary.policy_key !== descriptor.value?.policy_key }
    })
    const entry = computed(() => saved.value ?? generation.value)
    const view = computed(() => group.value?.view ?? 'original')
    const available = computed(() => !!source.value)
    const generationAvailable = computed(() => !!descriptor.value?.eligible && routing.value.available)
    const generationUnavailableReason = computed(() => !routing.value.available ? routing.value.unavailableReason
        : descriptor.value?.unavailable_reason === 'incomplete' ? 'This message is not finished yet.'
            : descriptor.value && !descriptor.value.eligible ? 'This source is unavailable for summary generation.' : '')
    // Before the first answer, only a click can tell: a saved summary needs no engine.
    const canSummarize = computed(() => available.value && (!descriptor.value
        || !!(entry.value || descriptor.value.saved || descriptor.value.previous || generationAvailable.value)))
    const running = computed(() => entry.value?.state === 'running' || generation.value?.state === 'running')
    const toggleTitle = computed(() => {
        if (view.value === 'summary') return 'Show original message'
        if (!canSummarize.value) return generationUnavailableReason.value
        return entry.value?.text || descriptor.value?.saved || descriptor.value?.previous ? 'Show summary' : 'Summarize message'
    })
    const identity = computed(() => descriptor.value ? {
        sourceKey: descriptor.value.source_key, sourceHash: descriptor.value.source_hash, variant: 'summary',
    } : null)
    const playbackReference = computed(() => {
        // Capture body and fingerprint together, before the button's deferred callback.
        const selected = entry.value
        const savedId = selected?.saved_id ?? selected?.id
        if (savedId && selected.result_hash) {
            const reference = { saved_id: savedId, result_hash: selected.result_hash }
            return () => reference
        }
        const subscription = group.value?.requested, expected = descriptor.value
        if (!selected || !subscription || !expected) return null
        return () => subscription.ready.then(() => {
            const actual = subscription.entry.descriptor
            if (!actual || actual.source_key !== expected.source_key || actual.source_hash !== expected.source_hash
                || actual.policy_key !== expected.policy_key) throw new Error('The message changed. Read the summary again.')
            return { id: subscription.id }
        })
    })
    function releaseOwned() {
        for (const subscription of owned) subscription.release()
        owned.clear()
    }
    watch(() => [source.value && summarySourceKey(source.value), source.value && store.versionFor(source.value)], ([key], previous) => {
        if (previous && previous[0] !== key) {
            operation++
            releaseOwned()
            if (!sharedVisit) scope.groups.clear()
        }
        // Only a source already asked for: its descriptor must come back after an invalidation.
        if (source.value && store.isTracked(source.value)) void store.resolve([source.value])
    }, { immediate: true, flush: 'sync' })
    // A policy change keeps an explicitly displayed completed snapshot available.
    watch(descriptor, (current, previous) => {
        if (!current) { releaseOwned(); return }
        const target = group.value, old = target?.requested?.entry
        if (old?.state === 'complete' && old.descriptor?.policy_key !== current.policy_key
            && old.descriptor?.source_key === current.source_key && old.descriptor?.source_hash === current.source_hash) {
            if (target.savedId == null) {
                store.rememberSaved({ ...old, id: old.saved_id, source_key: current.source_key, source_hash: current.source_hash,
                    policy_key: old.descriptor.policy_key })
                target.savedId = old.saved_id
            }
        }
        if (previous && (previous.source_hash !== current.source_hash || previous.policy_key !== current.policy_key)) releaseOwned()
    }, { flush: 'sync' })
    watch(() => generation.value?.state, state => {
        if (state === 'complete' && group.value) group.value.savedId = null
    })
    onScopeDispose(releaseOwned)

    // A regeneration rewrites the same row: only the result hash proves that a kept text is current.
    async function loadSaved(savedId, resultHash = null) {
        const target = group.value, d = descriptor.value, token = ++operation
        if (!target || !d) return
        if (resultHash && store.savedFor(savedId)?.result_hash === resultHash) { target.savedId = savedId; return }
        target.loading = true; target.error = null
        try {
            const loaded = await store.loadSaved(savedId)
            if (token !== operation || group.value !== target) return
            if (loaded.source_key !== d.source_key || loaded.source_hash !== d.source_hash) throw new Error('The message changed. Try again.')
            store.rememberSaved(loaded)
            target.savedId = loaded.id
        } catch (error) {
            if (token === operation && group.value === target) target.error = error.message || 'Could not load the summary'
        } finally {
            target.loading = false
        }
    }
    async function request(regenerate) {
        if (!group.value || ((regenerate || generation.value?.state !== 'running') && !generationAvailable.value)) return
        const target = group.value
        target.error = null
        const subscription = store.request({ ...source.value }, { manual: true, regenerate })
        owned.add(subscription); target.requested = markRaw(subscription)
        try { await subscription.ready } catch { /* The shared entry exposes the acquisition error. */ }
    }
    // A finished message keeps its answer. An ineligible one can become summarizable: it is asked again.
    async function lookup() {
        const known = descriptor.value
        if (known?.eligible || known?.saved || known?.previous) return
        if (known) store.forget(source.value)
        await store.resolve([source.value])
    }
    async function summarize() {
        if (!source.value) return
        const key = summarySourceKey(source.value)
        await lookup()
        if (!source.value || summarySourceKey(source.value) !== key) return
        if (!group.value || !canSummarize.value) {
            toast.error(store.lookupErrors[key] || generationUnavailableReason.value || 'This message cannot be summarized.')
            return
        }
        const target = group.value, d = descriptor.value
        target.view = 'summary'
        // Reveal after rendering the new height. The existing navigator owns scroll intent and anchoring.
        if (source.value.line_num && source.value.line_num !== d.first_line) void nextTick().then(() => scope.reveal(d.first_line))
        if (target.savedId != null) return
        if (generation.value) {
            if (generation.value.state === 'running' && !target.requested) await request(false)
            return
        }
        const stored = d.saved ?? d.previous
        if (stored) await loadSaved(stored.id, stored.result_hash)
        else await request(false)
    }
    async function regenerate() { if (!generationAvailable.value) return; if (group.value) group.value.view = 'summary'; await request(true) }
    function showOriginal() {
        if (!group.value) return
        // The original is usually longer: what the user looks at must not move.
        if (group.value.view !== 'original') scope.holdScroll()
        group.value.view = 'original'
    }
    function cancel() { if (generation.value) store.cancel(generation.value) }
    async function refreshSaved(savedId) { await loadSaved(savedId) }

    return reactive({
        descriptor, entry, generation, view, available, generationAvailable, generationUnavailableReason, canSummarize, identity, playbackReference,
        running, toggleTitle,
        loading: computed(() => !!group.value?.loading),
        error: computed(() => group.value?.error ?? (source.value && store.lookupErrors[summarySourceKey(source.value)]) ?? null),
        errors: computed(() => [group.value?.error, source.value && store.lookupErrors[summarySourceKey(source.value)], entry.value?.error, generation.value?.error]
            .filter((message, index, all) => message && all.indexOf(message) === index)),
        // Original view included: the switch stays once the message has a summary to go back to.
        hasSummary: computed(() => view.value === 'summary' || !!(entry.value?.text || descriptor.value?.saved || descriptor.value?.previous)),
        isHost: computed(() => !!descriptor.value && source.value?.line_num === descriptor.value.first_line),
        hidden: computed(() => view.value === 'summary' && !!descriptor.value?.text_lines?.includes(source.value?.line_num)),
        summarize, regenerate, showOriginal, cancel, refreshSaved,
    })
}

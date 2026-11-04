import { computed, inject, markRaw, nextTick, onScopeDispose, reactive, toValue, watch } from 'vue'
import { summarySourceKey, useSummariesStore } from '../stores/summaries.js'
import { useSettingsStore } from '../stores/settings.js'
import { useTextToSpeechStore } from '../stores/textToSpeech.js'
import { summaryGenerationAvailability } from '../utils/ttsConfig.js'

export const MESSAGE_SUMMARY_CONTEXT = Symbol('message-summary')
export const MESSAGE_SUMMARY_VISIT = Symbol('message-summary-visit')

// The list owns these choices for this visit, independently of virtualized row lifetimes.
export function createMessageSummaryVisit(reveal = () => {}) {
    return { groups: reactive(new Map()), reveal }
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
    const descriptor = computed(() => source.value ? store.descriptorFor(source.value) : null)
    const group = computed(() => {
        const d = descriptor.value
        if (!d?.source_key) return null
        const key = JSON.stringify([d.session_id, d.source_key, d.source_hash])
        if (!scope.groups.has(key)) scope.groups.set(key, { view: 'original', saved: null, requested: null, error: null, loading: false })
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
    const entry = computed(() => group.value?.saved ?? generation.value)
    const view = computed(() => group.value?.view ?? 'original')
    const available = computed(() => !!descriptor.value && !!(descriptor.value.eligible || descriptor.value.saved || descriptor.value.previous))
    const generationAvailable = computed(() => !!descriptor.value?.eligible && routing.value.available)
    const generationUnavailableReason = computed(() => !routing.value.available ? routing.value.unavailableReason
        : descriptor.value?.unavailable_reason || (!descriptor.value?.eligible ? 'This source is unavailable for summary generation.' : ''))
    const canSummarize = computed(() => available.value && !!(entry.value || descriptor.value.saved || descriptor.value.previous || generationAvailable.value))
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
        operation++
        if (previous && previous[0] !== key) {
            releaseOwned()
            if (!sharedVisit) scope.groups.clear()
        }
        if (source.value) void store.resolve([source.value])
    }, { immediate: true, flush: 'sync' })
    // A policy change keeps an explicitly displayed completed snapshot available.
    watch(descriptor, current => {
        if (!current) { releaseOwned(); return }
        const target = group.value, old = target?.requested?.entry
        if (old?.state === 'complete' && old.descriptor?.policy_key !== current.policy_key
            && old.descriptor?.source_key === current.source_key && old.descriptor?.source_hash === current.source_hash) {
            target.saved ??= { ...old, id: old.saved_id, source_key: current.source_key, source_hash: current.source_hash,
                policy_key: old.descriptor.policy_key, previousPolicy: true }
        }
        if (target?.saved) target.saved.previousPolicy = target.saved.policy_key !== current.policy_key
    }, { flush: 'sync' })
    watch(() => generation.value?.state, state => {
        if (state === 'complete' && group.value) group.value.saved = null
    })
    onScopeDispose(releaseOwned)

    async function loadSaved(savedId, previousPolicy = false) {
        const target = group.value, d = descriptor.value, token = ++operation
        if (!target || !d) return
        target.loading = true; target.error = null
        try {
            const loaded = await store.loadSaved(savedId)
            if (token !== operation || group.value !== target) return
            if (loaded.source_key !== d.source_key || loaded.source_hash !== d.source_hash) throw new Error('The message changed. Try again.')
            target.saved = { ...loaded, previousPolicy: previousPolicy || loaded.policy_key !== descriptor.value.policy_key }
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
    async function summarize() {
        if (!canSummarize.value) return
        const target = group.value, d = descriptor.value
        target.view = 'summary'
        // Reveal after rendering the new height. The existing navigator owns scroll intent and anchoring.
        if (source.value.line_num && source.value.line_num !== d.first_line) void nextTick().then(() => scope.reveal(d.first_line))
        if (target.saved) return
        if (generation.value) {
            if (generation.value.state === 'running' && !target.requested) await request(false)
            return
        }
        const saved = d.saved ?? d.previous
        if (saved) await loadSaved(saved.id, !d.saved)
        else await request(false)
    }
    async function showPrevious() {
        const previous = descriptor.value?.previous
        if (!previous || !group.value) return
        group.value.view = 'summary'
        await loadSaved(previous.id, true)
    }
    async function showCurrent() {
        if (!group.value || (!generation.value && !descriptor.value?.saved)) return
        group.value.saved = null
        await summarize()
    }
    async function regenerate() { if (!generationAvailable.value) return; if (group.value) group.value.view = 'summary'; await request(true) }
    function showOriginal() { if (group.value) group.value.view = 'original' }
    function cancel() { if (generation.value) store.cancel(generation.value) }
    async function refreshSaved(savedId) { await loadSaved(savedId, !!entry.value?.previousPolicy) }

    return reactive({
        descriptor, entry, generation, view, available, generationAvailable, generationUnavailableReason, canSummarize, identity, playbackReference,
        loading: computed(() => !!group.value?.loading),
        error: computed(() => group.value?.error ?? (source.value && store.lookupErrors[summarySourceKey(source.value)]) ?? null),
        isHost: computed(() => !!descriptor.value && source.value?.line_num === descriptor.value.first_line),
        hidden: computed(() => view.value === 'summary' && !!descriptor.value?.text_lines?.includes(source.value?.line_num)),
        summarize, regenerate, showOriginal, showPrevious, showCurrent, cancel, refreshSaved,
    })
}

import { computed, shallowRef, toValue, watch } from 'vue'
import { effectiveAdapt, summaryGenerationAvailability } from '../utils/ttsConfig.js'

// Only OpenRouter without an agent fallback needs source-specific evidence before generation.
export function useTemporarySummaryGeneration(source, settings, speech, summaries) {
    const descriptor = shallowRef(null), error = shallowRef(null), recheck = shallowRef(0)
    function retryLookup() { if (error.value) recheck.value++ }
    const needsLookup = computed(() => effectiveAdapt(settings.summaryModel === 'auto' ? null : settings.summaryModel,
        settings.enabledProviders, speech.keyPresent) === 'openrouter'
        && effectiveAdapt(null, settings.enabledProviders) === 'off')
    watch(() => [needsLookup.value, toValue(source)?.text, toValue(source)?.purpose, recheck.value], ([needed, text, purpose], _old, onCleanup) => {
        descriptor.value = null; error.value = null
        if (!needed || !text?.trim()) return
        const controller = new AbortController()
        onCleanup(() => controller.abort())
        summaries.resolve([{ text, purpose }], { signal: controller.signal }).then(([resolved]) => {
            if (controller.signal.aborted) return
            if (resolved?.error) error.value = resolved.error
            else if (typeof resolved?.openrouter_eligible !== 'boolean') error.value = 'Could not check summary engine availability.'
            else descriptor.value = resolved
        }).catch(failure => {
            if (!controller.signal.aborted) error.value = failure.message || 'Could not check summary engine availability.'
        })
    }, { immediate: true, flush: 'sync' })
    return computed(() => {
        const routing = summaryGenerationAvailability(settings.summaryModel, settings.enabledProviders, speech.keyPresent,
            descriptor.value?.openrouter_eligible)
        if (!needsLookup.value || descriptor.value) return { ...routing, lookupFailed: false, retryLookup }
        return { ...routing, available: false, lookupFailed: !!error.value, retryLookup,
            unavailableReason: error.value || 'Checking summary engine availability…' }
    })
}

// Shared broker lifetime. Visibility changes only affect prompt presentation.
import { ref, toValue, watch, onBeforeUnmount } from 'vue'
import { mountBrokerHost } from '../artifact-broker/host.js'

export const ARTIFACT_BROKER_HANDSHAKE_TIMEOUT = 30000

/** Injectable lifecycle core used by private and public frame owners. */
export function createArtifactBrokerBinding({ mount = mountBrokerHost,
    setTimer = setTimeout, clearTimer = clearTimeout } = {}) {
    const brokerPrompt = ref(null)
    const pendingPrompts = []
    let connection = null
    let boundWindow = null
    let boundIdentity = null
    let epoch = 0
    let timer = null
    let disposed = false

    function clearReadyTimer() {
        if (timer != null) clearTimer(timer)
        timer = null
    }

    function onBrokerDecision(decision) {
        brokerPrompt.value?.settle(decision)
    }

    function teardown() {
        epoch++
        clearReadyTimer()
        connection?.destroy()
        connection = null
        boundWindow = null
        boundIdentity = null
        for (const prompt of [...pendingPrompts]) prompt.settle('deny')
    }

    function bind(iframe, config) {
        if (disposed) return
        const win = iframe?.contentWindow
        const identity = config ? JSON.stringify([config.documentUrl, config.bindingKey ?? null,
            config.mode ?? 'owner', config.proxyUrl ?? null, config.inArtifactsRoot ?? false,
            config.inlineGeneration ?? null]) : null
        if (config && win && connection && win === boundWindow && identity === boundIdentity) return
        teardown()
        if (!config || !win) return
        const capturedEpoch = epoch
        const current = () => !disposed && capturedEpoch === epoch
        let ready = false
        const showPrompt = target => {
            if (!current()) return Promise.resolve('deny')
            return new Promise(resolve => {
                let done = false
                const settle = decision => {
                    if (done) return
                    done = true
                    const index = pendingPrompts.findIndex(prompt => prompt.settle === settle)
                    if (index >= 0) pendingPrompts.splice(index, 1)
                    if (brokerPrompt.value?.settle === settle) brokerPrompt.value = pendingPrompts[0] ?? null
                    resolve(decision)
                }
                const prompt = { ...target, settle }
                pendingPrompts.push(prompt)
                if (!brokerPrompt.value) brokerPrompt.value = prompt
            })
        }
        if (config.onInlineReady) {
            timer = setTimer(() => {
                timer = null
                if (!current() || ready) return
                config.onInlineError?.('document_unavailable')
            }, ARTIFACT_BROKER_HANDSHAKE_TIMEOUT)
        }
        try {
            connection = mount(iframe, { ...config,
                getBookmarkId: config.getBookmarkId ?? (() => null),
                inArtifactsRoot: config.inArtifactsRoot ?? false,
                mode: config.mode ?? 'owner',
                showPrompt,
                onInlineReady: config.onInlineReady ? () => {
                    if (!current() || ready) return
                    ready = true
                    clearReadyTimer()
                    config.onInlineReady()
                } : undefined,
                onInlineHeight: config.onInlineHeight ? height => {
                    if (current()) config.onInlineHeight(height)
                } : undefined,
                onInlineEscape: config.onInlineEscape ? () => {
                    if (current()) config.onInlineEscape()
                } : undefined,
                onInlineScroll: config.onInlineScroll ? report => {
                    if (current()) config.onInlineScroll(report)
                } : undefined,
            })
            boundWindow = win
            boundIdentity = identity
        } catch {
            clearReadyTimer()
            config.onInlineError?.('document_unavailable')
        }
    }

    function dispose() {
        if (disposed) return
        teardown()
        disposed = true
    }

    return { brokerPrompt, onBrokerDecision, bind, dispose }
}

/** Capture one accepted navigation, independently of pending probe generations. */
export function inlineArtifactBrokerConfig(entry, runtime, documentBase) {
    if (!entry.bindingKey || !entry.brokerConfig) return null
    const generation = JSON.parse(entry.bindingKey)[2]
    const key = entry.artifactKey
    return { ...entry.brokerConfig,
        documentUrl: new URL(entry.documentUrl, documentBase).href,
        bindingKey: entry.bindingKey,
        inlineGeneration: generation,
        inlineRequestedHeight: entry.descriptor.height,
        getInlineMode: () => runtime.fullscreenArtifactKey.value === key ? 'fullscreen' : 'inline',
        onInlineEscape: () => runtime.requestEscape(key, generation),
        onInlineScroll: report => runtime.forwardScroll(key, generation, report),
        onInlineReady: () => runtime.documentReady(key, generation),
        onInlineError: error => runtime.documentFailed(key, generation, error),
        onInlineHeight: height => {
            if (entry.generation === generation && entry.loadState === 'ready' && Number.isFinite(height)) {
                runtime.reportHeight(key, generation, height)
            }
        },
    }
}

/** Rebind on document identity, never on passive owner visibility. */
export function useArtifactBroker(iframeRef, getConfig, watchSources) {
    const binding = createArtifactBrokerBinding()
    let listeningIframe = null
    const setup = () => binding.bind(toValue(iframeRef), getConfig())
    function trackIframe() {
        const iframe = toValue(iframeRef)
        if (iframe === listeningIframe) return
        listeningIframe?.removeEventListener('load', setup)
        listeningIframe = iframe ?? null
        listeningIframe?.addEventListener('load', setup)
    }
    watch(watchSources ?? [iframeRef], () => {
        trackIframe()
        setup()
    }, { flush: 'post', immediate: true })
    onBeforeUnmount(() => {
        listeningIframe?.removeEventListener('load', setup)
        listeningIframe = null
        binding.dispose()
    })
    return { brokerPrompt: binding.brokerPrompt, onBrokerDecision: binding.onBrokerDecision }
}

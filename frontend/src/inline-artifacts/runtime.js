import { shallowReactive, shallowRef } from 'vue'
import { createInlineGeometryScheduler, returnInlineFocus } from './geometry.js'
import { artifactKey, INLINE_ARTIFACT_RELOAD_QUERY } from './context.js'
import { createScrollDriver } from './scrollDriver.js'

/** A cached view owns frames. Rows own disposable geometry attachments only. */
export function createInlineArtifactRuntime({ viewId, pool, adapter }) {
    const geometry = createInlineGeometryScheduler({ pool })
    const entries = shallowReactive(new Map())
    const loadedEntries = shallowReactive([])
    const fullscreenArtifactKey = shallowRef(null)
    const active = shallowRef(true)
    const attachments = new Map()
    const probes = new Map()
    let revision = -1
    let disposed = false

    const runnable = entry => entry.present && entry.descriptor.status === 'ready'
    const frameId = key => JSON.stringify(['inline-artifact', viewId, key])
    // Match the existing HTML artifact preview's document capabilities.
    const frameAttrs = descriptor => ({
        sandbox: 'allow-scripts allow-same-origin allow-forms', title: descriptor.title,
    })

    function cancelProbe(entry) {
        const controller = probes.get(entry.artifactKey)
        if (!controller) return
        controller.abort()
        probes.delete(entry.artifactKey)
        if (entry.loadState === 'probing') entry.loadState = 'idle'
    }

    function chooseAttachment(entry) {
        if (!active.value || !entry.present || !['ready', 'error'].includes(entry.descriptor.status)) return null
        for (const attachment of attachments.get(entry.artifactKey)?.values() || []) {
            const visible = attachment.isVisible ? attachment.isVisible() : entry.requestedVisible
            if (!attachment.isSuppressed?.() && visible) return attachment
        }
        return null
    }

    function updateVisibility(entry) {
        const previousAttachment = entry.attachment
        entry.attachment = chooseAttachment(entry)
        const fullscreen = active.value && fullscreenArtifactKey.value === entry.artifactKey && runnable(entry)
        const eligible = !!entry.attachment || fullscreen
        entry.visible = eligible && !entry.needsNavigation && ['loading', 'ready'].includes(entry.loadState)
        if (entry.registered) {
            if (!entry.visible) returnInlineFocus(pool, entry.frameId, previousAttachment || entry.focusAttachment)
            pool.patch(entry.frameId, { visible: entry.visible, zTier: fullscreen ? 'fullscreen' : 'base' })
        }
        geometry.schedule()
        if (!eligible) cancelProbe(entry)
        if (eligible && entry.needsNavigation && !entry.retryPending && entry.loadState === 'idle') startProbe(entry)
    }

    function navigationUrl(url, generation) {
        const [document, hash] = url.split('#', 2)
        return `${document}${document.includes('?') ? '&' : '?'}${INLINE_ARTIFACT_RELOAD_QUERY}=${generation}${hash ? `#${hash}` : ''}`
    }

    function startProbe(entry) {
        if (disposed || !runnable(entry) || probes.has(entry.artifactKey)) return
        const controller = new AbortController()
        const generation = entry.generation
        const descriptor = entry.descriptor
        probes.set(entry.artifactKey, controller)
        entry.loadState = 'probing'
        entry.error = null
        const current = () => !disposed && !controller.signal.aborted && entry.generation === generation
            && probes.get(entry.artifactKey) === controller

        // Start synchronously so visibility establishes the HEAD request boundary.
        let result
        try {
            result = adapter.probe(descriptor, { signal: controller.signal })
        } catch (error) {
            result = Promise.reject(error)
        }
        Promise.resolve(result).then(({ available, error }) => {
            if (!current()) return
            if (!available) {
                probes.delete(entry.artifactKey)
                entry.loadState = 'error'
                entry.error = error || 'document_unavailable'
                updateVisibility(entry)
                return
            }
            const url = adapter.documentUrl(descriptor)
            const brokerConfig = adapter.brokerConfig(descriptor)
            entry.documentUrl = url
            entry.brokerConfig = brokerConfig
            entry.bindingKey = JSON.stringify([descriptor.publicationKey, descriptor.codeRevision, generation])
            entry.loadState = 'loading'
            entry.needsNavigation = false
            const frame = pool.ensureRegistered(entry.frameId, {
                src: navigationUrl(url, generation), remountKey: 0,
                attrs: frameAttrs(descriptor),
                // The HTTP status is not observable through iframe load events.
                onLoad: () => {},
            })
            probes.delete(entry.artifactKey)
            if (!entry.registered) {
                entry.registered = true
                loadedEntries.push(entry)
            }
            entry.frame = frame
            updateVisibility(entry)
        }).catch(error => {
            if (!current()) return
            probes.delete(entry.artifactKey)
            entry.loadState = 'error'
            entry.error = typeof error === 'string' ? error : 'document_unavailable'
            updateVisibility(entry)
        })
    }

    function invalidate(entry, clearAttachments, closeFullscreen = true) {
        returnInlineFocus(pool, entry.frameId, entry.attachment || entry.focusAttachment)
        cancelProbe(entry)
        entry.generation++
        entry.loadState = 'idle'
        entry.error = null
        entry.needsNavigation = true
        entry.retryPending = false
        if (clearAttachments) {
            attachments.get(entry.artifactKey)?.clear()
            entry.requestedVisible = false
            entry.attachment = null
        }
        if (closeFullscreen && fullscreenArtifactKey.value === entry.artifactKey) fullscreenArtifactKey.value = null
        entry.visible = false
        if (entry.registered) pool.patch(entry.frameId, { visible: false, zTier: 'base' })
    }

    function reconcile(manifest) {
        if (disposed || manifest.revision <= revision) return false
        revision = manifest.revision
        const seen = new Set()
        for (const descriptor of manifest.descriptors) {
            const key = artifactKey(descriptor.sourceSessionId, descriptor.artifactId)
            if (seen.has(key)) continue
            seen.add(key)
            let entry = entries.get(key)
            if (!entry) {
                entry = shallowReactive({
                    artifactKey: key, frameId: frameId(key), descriptor, present: true,
                    generation: 1, registered: false, frame: null,
                    loadState: 'idle', error: null, needsNavigation: true, retryPending: false,
                    documentUrl: null, brokerConfig: null, bindingKey: null,
                    requestedVisible: false, visible: false, attachment: null,
                    height: Math.max(160, Math.min(900, descriptor.height || 360)),
                    inlineHeight: Math.max(160, Math.min(900, descriptor.height || 360)), focusAttachment: null,
                    geometryRect: null, geometryClipRect: null, geometryVisible: false,
                })
                entries.set(key, entry)
                attachments.set(key, new Map())
                geometry.attach(entry.frameId, {
                    getAttachment: () => entry.attachment || entry.focusAttachment,
                    isVisible: () => entry.visible || (entry.loadState === 'error' || entry.descriptor.status === 'error') && !!entry.attachment,
                    onGeometry: fields => {
                        entry.geometryVisible = fields.visible
                        if (fields.rect) entry.geometryRect = fields.rect
                        if ('clipRect' in fields) entry.geometryClipRect = fields.clipRect
                    },
                    isFullscreen: () => active.value && fullscreenArtifactKey.value === key,
                })
            } else {
                const publicationChanged = entry.descriptor.publicationKey !== descriptor.publicationKey
                const codeChanged = entry.descriptor.codeRevision !== descriptor.codeRevision
                const eligibilityChanged = runnable(entry) !== (descriptor.status === 'ready')
                if (publicationChanged || codeChanged || eligibilityChanged) {
                    invalidate(entry, publicationChanged)
                    if (publicationChanged) entry.inlineHeight = entry.height = Math.max(160, Math.min(900, descriptor.height || 360))
                }
                entry.descriptor = descriptor
                entry.present = true
                if (entry.registered) pool.patch(entry.frameId, { attrs: frameAttrs(descriptor) })
            }
            updateVisibility(entry)
        }
        for (const [key, entry] of entries) {
            if (seen.has(key) || !entry.present) continue
            invalidate(entry, true)
            entry.present = false
            updateVisibility(entry)
        }
        return true
    }

    function attach(key, publicationKey, attachment) {
        const entry = entries.get(key)
        if (disposed || !entry?.present || entry.descriptor.publicationKey !== publicationKey) return () => {}
        const token = Symbol('attachment')
        attachments.get(key).set(token, attachment)
        updateVisibility(entry)
        return () => {
            if (disposed) return
            attachments.get(key)?.delete(token)
            updateVisibility(entry)
        }
    }

    function setVisible(key, visible) {
        const entry = entries.get(key)
        if (disposed || !entry) return
        entry.requestedVisible = visible
        updateVisibility(entry)
    }

    function setActive(value) {
        if (disposed) return
        active.value = value
        if (!value) fullscreenArtifactKey.value = null
        for (const entry of entries.values()) updateVisibility(entry)
    }

    async function reload(key) {
        const entry = entries.get(key)
        if (disposed || !entry?.present) return
        invalidate(entry, false, false)
        entry.retryPending = true
        const generation = entry.generation
        try {
            await adapter.retry(entry.descriptor)
        } catch {
            if (!disposed && entry.generation === generation) {
                entry.retryPending = false
                entry.loadState = 'error'
                entry.error = 'document_unavailable'
            }
            return
        }
        if (!disposed && entry.generation === generation) {
            entry.retryPending = false
            updateVisibility(entry)
        }
    }

    function openFullscreen(key) {
        const entry = entries.get(key)
        if (disposed || !active.value || !entry?.registered || !runnable(entry)
            || !['loading', 'ready'].includes(entry.loadState)) return
        const previous = entries.get(fullscreenArtifactKey.value)
        entry.focusAttachment = entry.attachment
        fullscreenArtifactKey.value = key
        if (previous && previous !== entry) updateVisibility(previous)
        updateVisibility(entry)
    }

    function closeFullscreen() {
        const entry = entries.get(fullscreenArtifactKey.value)
        fullscreenArtifactKey.value = null
        if (entry && !disposed) {
            entry.focusAttachment?.focusConversation?.()
            updateVisibility(entry)
            entry.focusAttachment = null
        }
    }

    function documentReady(key, generation) {
        const entry = entries.get(key)
        if (disposed || entry?.generation !== generation || entry.loadState !== 'loading') return
        entry.loadState = 'ready'
        entry.error = null
        updateVisibility(entry)
    }

    function documentFailed(key, generation, error) {
        const entry = entries.get(key)
        if (disposed || entry?.generation !== generation || entry.loadState !== 'loading') return
        entry.loadState = 'error'
        entry.error = error
        updateVisibility(entry)
    }

    function reportHeight(key, generation, height) {
        const entry = entries.get(key)
        if (disposed || entry?.generation !== generation || entry.loadState !== 'ready'
            || fullscreenArtifactKey.value === key || !Number.isFinite(height)) return
        entry.inlineHeight = entry.height = Math.max(160, Math.min(900, Math.ceil(height)))
        geometry.schedule()
    }

    function requestEscape(key, generation) {
        const entry = entries.get(key)
        if (!disposed && entry?.generation === generation && fullscreenArtifactKey.value === key) closeFullscreen()
    }

    // Gestures the artifact cannot consume scroll the chat that owns the placeholder.
    const scrollDrivers = new Map()
    function forwardScroll(key, generation, report) {
        const entry = entries.get(key)
        if (disposed || entry?.generation !== generation || entry.loadState !== 'ready'
            || fullscreenArtifactKey.value === key) return
        const clip = [...attachments.get(key)?.values() || []].map(attachment => attachment.clipEl)
            .find(element => element?.isConnected)
        if (!clip) return
        let driver = scrollDrivers.get(clip)
        if (!driver) scrollDrivers.set(clip, driver = createScrollDriver(clip))
        const finite = Number.isFinite(report?.deltaY)
        if (report?.kind === 'wheel' && finite && [0, 1, 2].includes(report.deltaMode)) driver.wheel(report.deltaY, report.deltaMode)
        else if (report?.kind === 'touchstart') driver.touchStart()
        else if (report?.kind === 'touchmove' && finite) driver.touchMove(report.deltaY)
        else if (report?.kind === 'touchend' && Number.isFinite(report.velocity)) driver.touchEnd(report.velocity)
    }

    function dispose() {
        if (disposed) return
        disposed = true
        geometry.dispose()
        for (const driver of scrollDrivers.values()) driver.dispose()
        scrollDrivers.clear()
        fullscreenArtifactKey.value = null
        for (const entry of entries.values()) {
            cancelProbe(entry)
            if (entry.registered) {
                returnInlineFocus(pool, entry.frameId, entry.attachment || entry.focusAttachment)
                pool.unregister(entry.frameId)
            }
        }
        loadedEntries.length = 0
        entries.clear()
        attachments.clear()
        adapter.dispose()
    }

    return { entries, loadedEntries, fullscreenArtifactKey, active, reconcile, attach,
        setVisible, setActive, reload, openFullscreen, closeFullscreen,
        documentReady, documentFailed, reportHeight, requestEscape, forwardScroll, geometry, dispose }
}

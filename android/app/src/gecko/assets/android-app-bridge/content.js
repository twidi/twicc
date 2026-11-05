/* Register an engine-generated document identity. Use a separate native app for this privileged handshake. */
const documentId = crypto.randomUUID()
const ttsDocumentReady = browser.runtime.sendNativeMessage('twicc_tts_document', { ttsDocument: documentId }).then(() => {
    const port = browser.runtime.connect({ name: 'twicc-tts-document' })
    port.postMessage({ type: 'document', documentId })
}).catch(() => {})
/* The native delegate also checks this session, its saved origin, and the top-level frame. */
window.addEventListener('message', async event => {
    if (event.source !== window || event.origin !== location.origin) return
    if (event.data?.channel !== 'twicc.android.request') return
    await ttsDocumentReady
    try {
        const response = JSON.parse(await browser.runtime.sendNativeMessage('twicc_android', { ...event.data.request, documentToken: documentId }))
        window.postMessage({ channel: 'twicc.android.response', response }, location.origin)
    } catch { /* Native fallback remains available if the bridge is unavailable. */ }
})

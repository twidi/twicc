/* Register an engine-generated document identity. Use a separate native app for this privileged handshake. */
/* `crypto.randomUUID` only exists in secure contexts: fall back to `getRandomValues` on plain HTTP. */
const documentId = typeof crypto.randomUUID === 'function'
    ? crypto.randomUUID()
    : '10000000-1000-4000-8000-100000000000'.replace(/[018]/g, c => (+c ^ crypto.getRandomValues(new Uint8Array(1))[0] & 15 >> +c / 4).toString(16))
const ttsDocumentReady = browser.runtime.sendNativeMessage('twicc_tts_document', { ttsDocument: documentId }).then(() => {
    const app = browser.runtime.connectNative('twicc_android')
    app.onMessage.addListener(response => {
        window.postMessage({ channel: 'twicc.android.response', response }, location.origin)
    })
    const port = browser.runtime.connect({ name: 'twicc-tts-document' })
    port.postMessage({ type: 'document', documentId })
}).catch(() => {})
/* The native delegate also checks this session, its saved origin, and the top-level frame. */
window.addEventListener('message', async event => {
    if (event.source !== window || event.origin !== location.origin) return
    if (event.data?.channel !== 'twicc.android.request') return
    const request = event.data.request
    if (request?.protocol !== 1 || typeof request.id !== 'string') return
    const command = request.command
    // Transport evidence is available before native state or language reads complete.
    if (command === 'getState') {
        window.postMessage({ channel: 'twicc.android.environment', protocol: 1, id: request.id }, location.origin)
    }
    // Language reads are available before document registration or speech attachment.
    if (command === 'locale.set' || typeof command === 'string' && command.startsWith('tts.')) await ttsDocumentReady
    try {
        const response = JSON.parse(await browser.runtime.sendNativeMessage('twicc_android', { ...event.data.request, documentToken: documentId }))
        window.postMessage({ channel: 'twicc.android.response', response }, location.origin)
    } catch { /* Native fallback remains available if the bridge is unavailable. */ }
})

/* Credentials remain in this privileged background. Content scripts receive no cookie values. */
const native = browser.runtime.connectNative('twicc_tts')
const documents = new Map()
browser.runtime.onConnect.addListener(port => {
    const sender = port.sender
    if (port.name !== 'twicc-tts-document' || sender.frameId !== 0 || !sender.tab?.cookieStoreId) return
    if (!sender.url?.startsWith('https://')) return
    port.onMessage.addListener(async message => {
        if (message?.type !== 'document' || typeof message.documentId !== 'string') return
        const document = { documentId: message.documentId, url: sender.url, storeId: sender.tab.cookieStoreId }
        try {
            // Capture the actual principal attributes while this validated top-level document still exists.
            await browser.twiccCookies.capture(sender.tab.id, message.documentId, sender.url)
        } catch { return }
        documents.set(message.documentId, document)
        native.postMessage({ type: 'document', ...document })
        port.onDisconnect.addListener(() => {
            // Native freezes an accepted export before session closure. Its store remains usable without the tab.
            setTimeout(() => {
                documents.delete(message.documentId)
                browser.twiccCookies.release(message.documentId)
            }, 30000)
        })
    })
})
native.onMessage.addListener(async request => {
    if (request?.type !== 'export') return
    const document = documents.get(request.documentId)
    const identity = { requestId: request.requestId, instanceId: request.instanceId,
        generation: request.generation, documentId: request.documentId, url: request.url }
    try {
        if (!document || document.url !== request.url || document.storeId !== request.storeId)
            throw new Error('The browser document changed.')
        const origin = new URL(document.url).origin
        if (!Array.isArray(request.urls) || !request.urls.length || request.urls.some(url => {
            const parsed = new URL(url)
            return parsed.protocol !== 'https:' || parsed.origin !== origin
        })) throw new Error('Cookie export requires the active HTTPS instance.')
        const cookies = await browser.twiccCookies.read(request.documentId, request.urls)
        native.postMessage({ type: 'exported', ...identity, cookies })
    } catch (error) { native.postMessage({ type: 'exported', ...identity, error: String(error.message) }) }
})

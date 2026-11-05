/* The native delegate also checks this session, its saved origin, and the top-level frame. */
window.addEventListener('message', async event => {
    if (event.source !== window || event.origin !== location.origin) return
    if (event.data?.channel !== 'twicc.android.request') return
    try {
        const response = JSON.parse(await browser.runtime.sendNativeMessage('twicc_android', event.data.request))
        window.postMessage({ channel: 'twicc.android.response', response }, location.origin)
    } catch { /* Native fallback remains available if the bridge is unavailable. */ }
})

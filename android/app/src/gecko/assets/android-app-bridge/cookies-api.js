/* GeckoView 159's public cookies API cannot resolve its string session-context cookie stores.
 * This built-in addon_parent API uses the actual top-level principal, never a parsed context ID.
 * Keep the real two-profile HTTPS/WSS instrumentation test when updating GeckoView.
 */
Cu.importGlobalProperties(['URL'])
this.twiccCookies = class extends ExtensionAPI {
    getAPI(context) {
        if (context.envType !== 'addon_parent') throw new Error('Cookie export requires the privileged background.')
        const documents = new Map()
        context.callOnClose({ close: () => documents.clear() })
        return { twiccCookies: {
            async capture(tabId, documentId, url) {
                const browser = context.extension.tabManager.get(tabId).browser
                const principal = browser.browsingContext.currentWindowGlobal?.documentPrincipal
                if (!principal?.isContentPrincipal || browser.currentURI.spec !== url || new URL(url).protocol !== 'https:')
                    throw new Error('The browser document changed.')
                const attributes = { ...principal.originAttributes }
                if (attributes.partitionKey || attributes.firstPartyDomain)
                    throw new Error('Partitioned authentication cookies are unsupported.')
                documents.set(documentId, { origin: new URL(url).origin, attributes })
            },
            async read(documentId, urls) {
                const document = documents.get(documentId)
                if (!document || !urls.length) throw new Error('The browser document is unavailable.')
                const result = new Map()
                for (const value of urls) {
                    const url = new URL(value)
                    if (url.protocol !== 'https:' || url.origin !== document.origin || url.username || url.password)
                        throw new Error('Cookie export requires the active HTTPS instance.')
                    const host = url.hostname.replace(/^\[|\]$/g, '')
                    for (const cookie of Services.cookies.getCookiesFromHost(host, document.attributes, true)) {
                        if (!Object.entries(document.attributes).every(([key, attribute]) => cookie.originAttributes[key] === attribute))
                            throw new Error('The cookie profile does not match the browser document.')
                        if (cookie.rawHost !== host && !(cookie.isDomain && host.endsWith(cookie.host))) continue
                        const path = cookie.path
                        if (url.pathname !== path && !(url.pathname.startsWith(path) &&
                            (path.endsWith('/') || url.pathname[path.length] === '/'))) continue
                        if (!cookie.isSession && cookie.expiry * 1000 <= Date.now()) continue
                        result.set(JSON.stringify([cookie.name, cookie.host, path]), {
                            name: cookie.name, value: cookie.value, domain: cookie.rawHost, path,
                            hostOnly: !cookie.isDomain, secure: cookie.isSecure, httpOnly: cookie.isHttpOnly,
                            expiresAt: cookie.isSession ? null : cookie.expiry * 1000,
                        })
                    }
                }
                return [...result.values()]
            },
            async release(documentId) { documents.delete(documentId) },
        } }
    }
}

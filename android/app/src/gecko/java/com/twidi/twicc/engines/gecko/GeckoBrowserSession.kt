package com.twidi.twicc.engines.gecko

import android.app.Activity
import com.twidi.twicc.AndroidAppPolicy
import com.twidi.twicc.Instance
import com.twidi.twicc.NavigationDecision
import com.twidi.twicc.NavigationRequest
import com.twidi.twicc.browser.*
import com.twidi.twicc.platform.DownloadSource
import com.twidi.twicc.platform.NativeRequests
import org.mozilla.geckoview.*

internal class GeckoBrowserSession(
    activity: Activity,
    private val instance: Instance,
    private val events: BrowserEvents,
    requests: NativeRequests,
) : BrowserSession {
    override val view = GeckoView(activity)
    private val page = GeckoSession(GeckoSessionSettings.Builder().contextId(instance.profileName).build())
    private val prompts = GeckoPrompts(activity, requests)
    private var closed = false
    private var bridgeReady = false
    private var queuedLoad: Pair<String, Boolean>? = null

    init {
        try {
            page.promptDelegate = prompts
            page.navigationDelegate = object : GeckoSession.NavigationDelegate {
                override fun onLoadRequest(session: GeckoSession, request: GeckoSession.NavigationDelegate.LoadRequest): GeckoResult<AllowOrDeny> {
                    if (closed) return GeckoResult.fromValue(AllowOrDeny.DENY)
                    val decision = events.onNavigationRequest(NavigationRequest(request.uri,
                        newWindow = request.target == GeckoSession.NavigationDelegate.TARGET_WINDOW_NEW,
                        userGesture = request.hasUserGesture))
                    return GeckoResult.fromValue(if (decision == NavigationDecision.ALLOW) AllowOrDeny.ALLOW else AllowOrDeny.DENY)
                }
                override fun onLocationChange(session: GeckoSession, url: String?,
                    perms: MutableList<GeckoSession.PermissionDelegate.ContentPermission>, hasUserGesture: Boolean) {
                    if (!closed && url != null) events.onLocationChanged(url)
                }
                override fun onCanGoBack(session: GeckoSession, canGoBack: Boolean) {
                    if (!closed) events.onHistoryChanged(canGoBack)
                }
                override fun onLoadError(session: GeckoSession, uri: String?, error: WebRequestError): GeckoResult<String>? {
                    if (!closed) events.onFailure(if (error.category == WebRequestError.ERROR_CATEGORY_SECURITY)
                        "The HTTPS certificate could not be verified. Check the tunnel certificate and your phone's date."
                    else "Cannot reach ${instance.name}. Check your connection and HTTPS tunnel, then retry.")
                    return null // No certificate-exception error page.
                }
            }
            page.progressDelegate = object : GeckoSession.ProgressDelegate {
                override fun onPageStart(session: GeckoSession, url: String) { if (!closed) events.onPageStarted(url) }
                override fun onProgressChange(session: GeckoSession, progress: Int) { if (!closed) events.onProgressChanged(progress) }
                override fun onPageStop(session: GeckoSession, success: Boolean) { if (!closed) events.onPageFinished() }
            }
            page.contentDelegate = object : GeckoSession.ContentDelegate {
                override fun onExternalResponse(session: GeckoSession, response: WebResponse) {
                    if (closed) { runCatching { response.body?.close() }; return }
                    fun header(name: String) = response.headers.entries.firstOrNull { it.key.equals(name, true) }?.value
                    events.onDownload(DownloadSource.Stream(response.uri, header("content-type"), header("content-disposition"), response.body))
                }
                override fun onCrash(session: GeckoSession) { if (!closed) events.onRendererStopped() }
                override fun onKill(session: GeckoSession) { if (!closed) events.onRendererStopped() }
            }
            page.permissionDelegate = object : GeckoSession.PermissionDelegate {
                override fun onContentPermissionRequest(session: GeckoSession,
                    perm: GeckoSession.PermissionDelegate.ContentPermission): GeckoResult<Int> {
                    val autoplay = perm.permission == GeckoSession.PermissionDelegate.PERMISSION_AUTOPLAY_AUDIBLE ||
                        perm.permission == GeckoSession.PermissionDelegate.PERMISSION_AUTOPLAY_INAUDIBLE
                    val allowed = !closed && autoplay && instance.url.allowsNavigation(perm.uri)
                    return GeckoResult.fromValue(if (allowed) GeckoSession.PermissionDelegate.ContentPermission.VALUE_ALLOW
                        else GeckoSession.PermissionDelegate.ContentPermission.VALUE_DENY)
                }
            }
            page.open(GeckoEngine.runtime(activity))
            view.setSession(page)
            GeckoEngine.appExtension(activity).accept({ extension ->
                if (!closed && extension != null) {
                    page.webExtensionController.setMessageDelegate(extension, object : WebExtension.MessageDelegate {
                        override fun onMessage(nativeApp: String, message: Any, sender: WebExtension.MessageSender): GeckoResult<Any>? {
                            if (closed || sender.session !== page ||
                                sender.environmentType != WebExtension.MessageSender.ENV_TYPE_CONTENT_SCRIPT ||
                                !AndroidAppPolicy.acceptsFrame(instance.url, sender.url, sender.isTopLevel)) return null
                            return GeckoResult.fromValue(events.onAppMessage(message.toString()))
                        }
                    }, "twicc_android")
                }
                if (!closed) finishBridgeSetup()
            }, { if (!closed) finishBridgeSetup() })
        } catch (error: RuntimeException) { close(); throw error }
    }
    private fun finishBridgeSetup() {
        bridgeReady = true
        queuedLoad?.let { (url, bypassCache) -> queuedLoad = null; load(url, bypassCache) }
    }
    override fun load(url: String, bypassCache: Boolean) {
        if (closed) return
        if (!bridgeReady) { queuedLoad = url to bypassCache; return }
        page.stop()
        page.load(GeckoSession.Loader().uri(url).flags(if (bypassCache) GeckoSession.LOAD_FLAGS_BYPASS_CACHE else GeckoSession.LOAD_FLAGS_NONE))
    }
    override fun goBack() { if (!closed) page.goBack() }
    override fun setVisible(visible: Boolean) { if (!closed) page.setActive(visible) }
    override fun close() {
        if (closed) return
        closed = true
        queuedLoad = null
        view.releaseSession()
        if (page.isOpen) page.close()
        prompts.close()
    }
}

package com.twidi.twicc.engines.webview

import android.annotation.SuppressLint
import android.app.Activity
import android.graphics.Bitmap
import android.net.Uri
import android.net.http.SslError
import android.os.Message
import android.webkit.*
import androidx.webkit.WebMessageCompat
import androidx.webkit.WebViewCompat
import androidx.webkit.WebViewFeature
import com.twidi.twicc.AndroidAppPolicy
import com.twidi.twicc.BuildConfig
import com.twidi.twicc.Instance
import com.twidi.twicc.NavigationDecision
import com.twidi.twicc.NavigationRequest
import com.twidi.twicc.browser.*
import com.twidi.twicc.platform.*

@SuppressLint("SetJavaScriptEnabled")
internal class WebViewBrowserSession(
    activity: Activity,
    private val instance: Instance,
    private val events: BrowserEvents,
    private val requests: NativeRequests,
) : BrowserSession {
    override val view = WebView(activity)
    private lateinit var cookies: CookieManager
    private var closed = false
    private var appListenerAdded = false
    private var forceLoading = false
    private var safeUrl = "${instance.address}/"
    private val pending = mutableSetOf<PendingRequest>()
    private val popups = mutableSetOf<WebView>()

    init {
        try {
            WebViewCompat.setProfile(view, instance.profileName)
            cookies = WebViewCompat.getProfile(view).cookieManager.apply {
                setAcceptCookie(true)
                setAcceptThirdPartyCookies(view, true)
            }
            view.settings.apply {
                javaScriptEnabled = true
                domStorageEnabled = true
                useWideViewPort = true
                loadWithOverviewMode = true
                mediaPlaybackRequiresUserGesture = false
                mixedContentMode = WebSettings.MIXED_CONTENT_NEVER_ALLOW
                allowFileAccess = false
                allowContentAccess = true
                setSupportMultipleWindows(true)
                javaScriptCanOpenWindowsAutomatically = false
                builtInZoomControls = true
                displayZoomControls = false
            }
            configureAppBridge()
            WebView.setWebContentsDebuggingEnabled(BuildConfig.DEBUG)
            view.webViewClient = pageClient()
            view.webChromeClient = chromeClient()
            view.setDownloadListener { url, agent, disposition, mime, _ ->
                if (!closed) {
                    val headers = mutableMapOf<String, String>()
                    agent?.takeIf { it.isNotBlank() }?.let { headers["User-Agent"] = it }
                    if (instance.url.allowsNavigation(url)) cookies.getCookie(url)?.takeIf { it.isNotBlank() }?.let { headers["Cookie"] = it }
                    events.onDownload(DownloadSource.HttpRequest(url, mime, disposition, headers))
                }
            }
        } catch (error: RuntimeException) { close(); throw error }
    }

    private fun configureAppBridge() {
        if (WebViewFeature.isFeatureSupported(WebViewFeature.WEB_MESSAGE_LISTENER)) {
            WebViewCompat.addWebMessageListener(view, "TwiccAndroid", setOf(instance.address)) { _, message, origin, mainFrame, reply ->
                if (!closed && message.type == WebMessageCompat.TYPE_STRING &&
                    AndroidAppPolicy.acceptsFrame(instance.url, origin.toString(), mainFrame))
                    message.data?.let { reply.postMessage(events.onAppMessage(it)) }
            }
            appListenerAdded = true
        }
    }

    private fun pageClient() = object : WebViewClient() {
        override fun shouldOverrideUrlLoading(view: WebView, request: WebResourceRequest): Boolean =
            closed || events.onNavigationRequest(NavigationRequest(request.url.toString(), request.isForMainFrame,
                userGesture = request.hasGesture())) != NavigationDecision.ALLOW

        override fun onPageStarted(view: WebView, url: String?, favicon: Bitmap?) {
            if (closed) return
            if (url != null && !instance.url.allowsNavigation(url)) {
                view.stopLoading()
                val decision = events.onNavigationRequest(NavigationRequest(url))
                if (decision == NavigationDecision.OPEN_EXTERNAL) load(safeUrl, false)
                else fail("This page cannot open inside TwiCC. Retry to return to your server.")
                return
            }
            url?.let { safeUrl = it }
            events.onPageStarted(url ?: safeUrl)
        }
        override fun doUpdateVisitedHistory(view: WebView, url: String?, isReload: Boolean) {
            if (closed) return
            if (url != null && instance.url.allowsNavigation(url)) { safeUrl = url; events.onLocationChanged(url) }
            events.onHistoryChanged(view.canGoBack())
        }
        override fun onPageFinished(view: WebView, url: String?) {
            if (closed) return
            restoreCacheMode()
            cookies.flush()
            events.onHistoryChanged(view.canGoBack())
            events.onPageFinished()
        }
        override fun onReceivedError(view: WebView, request: WebResourceRequest, error: WebResourceError) {
            if (!closed && request.isForMainFrame) fail("Cannot reach ${instance.name}. Check your connection and HTTPS tunnel, then retry.")
        }
        override fun onReceivedHttpError(view: WebView, request: WebResourceRequest, response: WebResourceResponse) {
            if (!closed && request.isForMainFrame && response.statusCode >= 400)
                fail("The server returned HTTP ${response.statusCode}. Check your tunnel or server, then retry.")
        }
        override fun onReceivedSslError(view: WebView, handler: SslErrorHandler, error: SslError) {
            handler.cancel()
            if (!closed && (error.url == safeUrl || error.url == view.url))
                fail("The HTTPS certificate could not be verified. Check the tunnel certificate and your phone's date.")
        }
        override fun onRenderProcessGone(view: WebView, detail: RenderProcessGoneDetail): Boolean {
            if (!closed) events.onRendererStopped()
            return true
        }
    }

    private fun chromeClient() = object : WebChromeClient() {
        override fun onProgressChanged(view: WebView, newProgress: Int) { if (!closed) events.onProgressChanged(newProgress) }
        override fun onShowFileChooser(view: WebView, callback: ValueCallback<Array<Uri>>, params: FileChooserParams): Boolean {
            if (closed) { callback.onReceiveValue(null); return true }
            if (params.mode != FileChooserParams.MODE_OPEN && params.mode != FileChooserParams.MODE_OPEN_MULTIPLE) {
                requests.message("Folder uploads are not available. Select individual files instead.")
                callback.onReceiveValue(null)
                return true
            }
            track { done -> requests.pickFiles(FileRequest(params.acceptTypes.toList(), params.mode == FileChooserParams.MODE_OPEN_MULTIPLE)) {
                callback.onReceiveValue(it?.toTypedArray()); done()
            } }
            return true
        }
        override fun onJsAlert(view: WebView, url: String?, message: String?, result: JsResult): Boolean =
            prompt(PromptKind.ALERT, message) { if (it != null) result.confirm() else result.cancel() }
        override fun onJsConfirm(view: WebView, url: String?, message: String?, result: JsResult): Boolean =
            prompt(PromptKind.CONFIRM, message) { if (it != null) result.confirm() else result.cancel() }
        override fun onJsPrompt(view: WebView, url: String?, message: String?, defaultValue: String?, result: JsPromptResult): Boolean =
            prompt(PromptKind.TEXT, message, defaultValue.orEmpty()) { if (it != null) result.confirm(it.text) else result.cancel() }

        override fun onCreateWindow(view: WebView, isDialog: Boolean, isUserGesture: Boolean, resultMsg: Message): Boolean {
            if (closed || !isUserGesture) return false
            val popup = WebView(view.context)
            WebViewCompat.setProfile(popup, instance.profileName)
            popups.add(popup)
            var handled = false
            fun consume(url: String) {
                if (closed || handled || url == "about:blank") return
                handled = true
                events.onNavigationRequest(NavigationRequest(url, newWindow = true, userGesture = true))
                popup.post { destroyPopup(popup) }
            }
            popup.webViewClient = object : WebViewClient() {
                override fun shouldOverrideUrlLoading(view: WebView, request: WebResourceRequest): Boolean {
                    if (request.isForMainFrame) consume(request.url.toString())
                    return true
                }
                override fun onPageStarted(view: WebView, url: String?, favicon: Bitmap?) { url?.let { consume(it) } }
                override fun onRenderProcessGone(view: WebView, detail: RenderProcessGoneDetail): Boolean { destroyPopup(popup); return true }
            }
            (resultMsg.obj as WebView.WebViewTransport).webView = popup
            resultMsg.sendToTarget()
            this@WebViewBrowserSession.view.postDelayed({ destroyPopup(popup) }, 10_000)
            return true
        }
    }

    private fun track(start: (() -> Unit) -> PendingRequest) {
        var request: PendingRequest? = null
        var done = false
        val token = start { done = true; request?.let { pending.remove(it) } }
        request = token
        if (!done) pending.add(token)
    }
    private fun prompt(kind: PromptKind, message: String?, text: String = "", callback: (PromptAnswer?) -> Unit): Boolean {
        if (closed) { callback(null); return true }
        track { done -> requests.showPrompt(PromptRequest(kind, instance.name, message, text)) { callback(it); done() } }
        return true
    }
    private fun fail(message: String) { restoreCacheMode(); events.onFailure(message) }
    private fun restoreCacheMode() {
        if (forceLoading) { view.settings.cacheMode = WebSettings.LOAD_DEFAULT; forceLoading = false }
    }
    override fun load(url: String, bypassCache: Boolean) {
        if (closed) return
        view.stopLoading()
        restoreCacheMode()
        safeUrl = url
        if (bypassCache) {
            view.clearCache(true)
            forceLoading = true
            view.settings.cacheMode = WebSettings.LOAD_NO_CACHE
            view.loadUrl(url, mapOf("Cache-Control" to "no-cache", "Pragma" to "no-cache"))
        } else view.loadUrl(url)
    }
    override fun goBack() { if (!closed) view.goBack() }
    override fun setVisible(visible: Boolean) {
        if (!closed) {
            if (visible) view.onResume() else { cookies.flush(); view.onPause() }
        }
    }
    private fun destroyPopup(popup: WebView) {
        if (popups.remove(popup)) { popup.stopLoading(); popup.destroy() }
    }
    override fun close() {
        if (closed) return
        closed = true
        pending.toList().forEach { it.cancel() }
        pending.clear()
        popups.toList().forEach { destroyPopup(it) }
        if (::cookies.isInitialized) cookies.flush()
        if (appListenerAdded) WebViewCompat.removeWebMessageListener(view, "TwiccAndroid")
        view.stopLoading()
        view.destroy()
    }
}

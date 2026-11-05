package com.twidi.twicc.engines.gecko

import android.app.Activity
import android.os.Handler
import android.os.Looper
import com.twidi.twicc.tts.TtsCookie
import com.twidi.twicc.tts.TtsCookieJar
import okhttp3.HttpUrl.Companion.toHttpUrl
import org.json.JSONObject
import org.json.JSONArray
import java.util.UUID
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
    private var documentGeneration = 0L
    private var documentId: String? = null
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
                override fun onPageStart(session: GeckoSession, url: String) {
                    if (!closed) {
                        documentGeneration++
                        documentId?.let { TtsExports.retire(it) }
                        documentId = null
                        events.onPageStarted(url)
                    }
                }
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
                    TtsExports.attach(extension)
                    val delegate = object : WebExtension.MessageDelegate {
                        override fun onMessage(nativeApp: String, message: Any, sender: WebExtension.MessageSender): GeckoResult<Any>? {
                            if (closed || sender.session !== page ||
                                sender.environmentType != WebExtension.MessageSender.ENV_TYPE_CONTENT_SCRIPT ||
                                !AndroidAppPolicy.acceptsFrame(instance.url, sender.url, sender.isTopLevel)) return null
                            if (nativeApp == "twicc_tts_document" && message is JSONObject && message.has("ttsDocument")) {
                                val id = message.optString("ttsDocument")
                                if (runCatching { UUID.fromString(id) }.isFailure) return null
                                documentId?.let { TtsExports.retire(it) }
                                documentId = id
                                TtsExports.register(id, instance, documentGeneration, sender.url)
                                return GeckoResult.fromValue("{}")
                            }
                            if (nativeApp != "twicc_android") return null
                            return GeckoResult.fromValue(events.onAppMessage(message.toString()))
                        }
                    }
                    page.webExtensionController.setMessageDelegate(extension, delegate, "twicc_android")
                    page.webExtensionController.setMessageDelegate(extension, delegate, "twicc_tts_document")
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
    override fun exportTtsCookies(urls: List<String>, callback: (Result<List<TtsCookie>>) -> Unit) {
        val id = documentId
        if (closed || id == null || urls.isEmpty() || urls.any { !instance.url.allowsNavigation(it) }) {
            callback(Result.failure(IllegalStateException("The browser document is unavailable for cookie export.")))
            return
        }
        TtsExports.export(id, urls, callback)
    }
    override fun goBack() { if (!closed) page.goBack() }
    override fun setVisible(visible: Boolean) { if (!closed) page.setActive(visible) }
    override fun close() {
        if (closed) return
        closed = true
        queuedLoad = null
        documentId?.let { TtsExports.retire(it) }
        documentId = null
        view.releaseSession()
        if (page.isOpen) page.close()
        prompts.close()
    }
}

/** The extension and accepted operations belong to the runtime, never to an Activity or GeckoView. */
private object TtsExports {
    private val handler = Handler(Looper.getMainLooper())
    private var extension: WebExtension? = null
    private var port: WebExtension.Port? = null
    private data class Document(val instance: Instance, val generation: Long, val url: String,
        var storeId: String? = null, var retired: Boolean = false)
    private data class Export(val documentId: String, val urls: List<String>,
        val callback: (Result<List<TtsCookie>>) -> Unit, var sent: Boolean = false)
    private val documents = mutableMapOf<String, Document>()
    private val pending = mutableMapOf<String, Export>()

    fun attach(value: WebExtension) {
        if (extension === value) return
        extension = value
        value.setMessageDelegate(object : WebExtension.MessageDelegate {
            override fun onConnect(connected: WebExtension.Port) {
                if (connected.name != "twicc_tts" || connected.sender.session != null ||
                    connected.sender.environmentType != WebExtension.MessageSender.ENV_TYPE_EXTENSION ||
                    connected.sender.webExtension.id != value.id) { connected.disconnect(); return }
                port = connected
                connected.setDelegate(object : WebExtension.PortDelegate {
                    override fun onPortMessage(message: Any, source: WebExtension.Port) {
                        if (source !== port || message !is JSONObject) return
                        when (message.optString("type")) {
                            "document" -> {
                                val id = message.optString("documentId")
                                val document = documents[id] ?: return
                                if (message.optString("url") != document.url ||
                                    !document.instance.url.allowsNavigation(document.url)) return
                                document.storeId = message.optString("storeId").takeIf { it.isNotEmpty() } ?: return
                                pending.filterValues { it.documentId == id }.keys.toList().forEach { send(it) }
                            }
                            "exported" -> receive(message)
                        }
                    }
                    override fun onDisconnect(source: WebExtension.Port) {
                        if (port !== source) return
                        port = null
                        pending.keys.toList().forEach { finish(it, Result.failure(IllegalStateException("The cookie export bridge disconnected."))) }
                    }
                })
            }
        }, "twicc_tts")
    }
    fun register(id: String, instance: Instance, generation: Long, url: String) {
        documents[id] = Document(instance, generation, url)
    }
    /** Block new exports, but keep each accepted operation's frozen owner until delivery or timeout. */
    fun retire(id: String) {
        documents[id]?.retired = true
        if (pending.values.none { it.documentId == id }) documents.remove(id)
    }
    fun export(id: String, urls: List<String>, callback: (Result<List<TtsCookie>>) -> Unit) {
        // Accept only after the privileged adapter captures the actual principal attributes.
        // An accepted operation then needs neither the document nor its Activity to remain alive.
        val document = documents[id]
        if (document?.retired == true) {
            handler.post { callback(Result.failure(IllegalStateException("The browser document changed."))) }
            return
        }
        if (document?.storeId == null) {
            handler.post { callback(Result.failure(IllegalStateException("Native speech is still initializing. Retry speech."))) }
            return
        }
        val requestId = UUID.randomUUID().toString()
        pending[requestId] = Export(id, urls.toList(), callback)
        handler.postDelayed({ finish(requestId, Result.failure(IllegalStateException("Cookie export timed out."))) }, 15_000)
        send(requestId)
    }
    private fun send(requestId: String) {
        val operation = pending[requestId] ?: return
        val document = documents[operation.documentId] ?: return
        val store = document.storeId ?: return
        val channel = port ?: return
        if (operation.sent) return
        operation.sent = true
        channel.postMessage(JSONObject().put("type", "export").put("requestId", requestId)
            .put("documentId", operation.documentId).put("instanceId", document.instance.id)
            .put("generation", document.generation).put("url", document.url).put("storeId", store)
            .put("urls", JSONArray(operation.urls)))
    }
    private fun receive(message: JSONObject) {
        val requestId = message.optString("requestId")
        val operation = pending[requestId] ?: return
        val document = documents[operation.documentId] ?: return
        if (!operation.sent || message.optString("documentId") != operation.documentId ||
            message.optString("instanceId") != document.instance.id ||
            message.optLong("generation", -1) != document.generation || message.optString("url") != document.url) return
        finish(requestId, runCatching {
            check(!message.has("error")) { message.optString("error") }
            val cookies = message.getJSONArray("cookies")
            val origin = document.instance.address.toHttpUrl()
            val jar = TtsCookieJar(origin, (0 until cookies.length()).map {
                val value = cookies.getJSONObject(it)
                TtsCookie(value.getString("name"), value.getString("value"), value.getString("domain"),
                    value.getString("path"), value.getBoolean("hostOnly"), value.getBoolean("secure"),
                    value.getBoolean("httpOnly"), if (value.isNull("expiresAt")) null else value.getLong("expiresAt"))
            })
            operation.urls.flatMap { jar.loadForRequest(it.toHttpUrl()) }.distinctBy { Triple(it.name, it.domain, it.path) }
                .map { TtsCookie.fromCookie(it) }
        })
    }
    private fun finish(requestId: String, result: Result<List<TtsCookie>>) {
        val operation = pending.remove(requestId) ?: return
        val document = documents[operation.documentId]
        if (document?.retired == true && pending.values.none { it.documentId == operation.documentId })
            documents.remove(operation.documentId)
        operation.callback(result)
    }
}

package com.twidi.twicc.tts

import java.io.IOException
import okhttp3.Cookie
import okhttp3.CookieJar
import okhttp3.HttpUrl
import okhttp3.HttpUrl.Companion.toHttpUrl
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.WebSocket
import okhttp3.WebSocketListener

/** Engine-neutral credentials. Expiry is Unix time in milliseconds; null means a session cookie. */
data class TtsCookie(
    val name: String,
    val value: String,
    val domain: String,
    val path: String,
    val hostOnly: Boolean,
    val secure: Boolean,
    val httpOnly: Boolean,
    val expiresAt: Long?,
) {
    fun toCookie(): Cookie = Cookie.Builder().name(name).value(value).path(path).apply {
        if (hostOnly) hostOnlyDomain(domain) else domain(domain)
        if (secure) secure()
        if (httpOnly) httpOnly()
        expiresAt?.let { expiresAt(it) }
    }.build()

    companion object {
        fun fromCookie(cookie: Cookie) = TtsCookie(cookie.name, cookie.value, cookie.domain,
            cookie.path, cookie.hostOnly, cookie.secure, cookie.httpOnly,
            cookie.expiresAt.takeIf { cookie.persistent })
    }
}

/** A copied jar belongs to one exact HTTPS origin. It never writes back to the browser. */
class TtsCookieJar(val origin: HttpUrl, cookies: List<TtsCookie>) : CookieJar {
    private val values = mutableMapOf<Triple<String, String, String>, Cookie>()
    init {
        require(origin.isHttps && origin.username.isEmpty() && origin.password.isEmpty())
        cookies.map { it.toCookie() }.filter { domainMatches(it) }.forEach { put(it) }
    }
    fun allows(url: HttpUrl) = url.isHttps && url.host == origin.host && url.port == origin.port &&
        url.username.isEmpty() && url.password.isEmpty()
    private fun domainMatches(cookie: Cookie): Boolean =
        if (cookie.hostOnly) cookie.domain == origin.host
        else cookie.domain == origin.host || origin.host.endsWith(".${cookie.domain}")
    private fun put(cookie: Cookie) { values[Triple(cookie.name, cookie.domain, cookie.path)] = cookie }
    @Synchronized override fun saveFromResponse(url: HttpUrl, cookies: List<Cookie>) {
        if (allows(url)) cookies.filter { domainMatches(it) }.forEach { put(it) }
    }
    @Synchronized override fun loadForRequest(url: HttpUrl): List<Cookie> {
        if (!allows(url)) return emptyList()
        values.entries.removeAll { it.value.expiresAt <= System.currentTimeMillis() }
        return values.values.filter { it.matches(url) }.sortedByDescending { it.path.length }
    }
    @Synchronized fun clear() { values.clear() }
}

/** Credentials stay in native process memory. Never serialize this value into a bridge result. */
class TtsCredentials(val instanceId: String, val origin: String, val cookies: List<TtsCookie>) {
    override fun toString() = "TtsCredentials(instanceId=$instanceId)"
}
class TtsAuthorizationException : IOException("Authentication expired. Open TwiCC and sign in again.")
sealed interface TtsConnectionEvent {
    val connectionId: String
    data class Opened(override val connectionId: String) : TtsConnectionEvent
    data class Frame(override val connectionId: String, val frame: TtsFrame) : TtsConnectionEvent
    data class Lost(override val connectionId: String, val retryAfterMillis: Long) : TtsConnectionEvent
    data class AuthorizationLost(override val connectionId: String) : TtsConnectionEvent
    data class HeartbeatDue(override val connectionId: String) : TtsConnectionEvent
}
/** Retry delay resets after the owner accepts a reconciled state, never merely on a TCP open. */
class TtsBackoff {
    private var delay = 1000L
    @Synchronized fun failed(): Long = delay.also { delay = (delay * 2).coerceAtMost(30000) }
    @Synchronized fun reconciled() { delay = 1000 }
}
/** A single cancellable audio response. A failed read is terminal; callers must not reopen from byte zero. */
class TtsAudioStream internal constructor(private val call: okhttp3.Call, private val response: okhttp3.Response,
    private val onClose: () -> Unit) : java.io.Closeable {
    val input: java.io.InputStream = response.body!!.byteStream()
    val contentType: String? = response.header("Content-Type")
    override fun close() { call.cancel(); response.close(); onClose() }
}

/** Native HTTP and WSS share one TLS client and in-memory jar. Call HTTP methods on a worker thread. */
class TtsConnection(
    origin: String,
    cookies: List<TtsCookie>,
    configure: OkHttpClient.Builder.() -> Unit = {},
) {
    constructor(credentials: TtsCredentials, configure: OkHttpClient.Builder.() -> Unit = {}) :
        this(credentials.origin, credentials.cookies, configure) { instanceId = credentials.instanceId }

    val cookieJar = TtsCookieJar(origin.toHttpUrl(), cookies)
    val client = OkHttpClient.Builder().apply(configure).cookieJar(cookieJar)
        .followRedirects(false).followSslRedirects(false).retryOnConnectionFailure(false)
        .readTimeout(0, java.util.concurrent.TimeUnit.MILLISECONDS)
        .addInterceptor { chain ->
            if (!cookieJar.allows(chain.request().url)) throw IOException("TTS requires the active HTTPS instance.")
            chain.proceed(chain.request())
        }.build()
    /** This callback must enqueue events onto the service's single state owner. It must not mutate policy directly. */
    @Volatile var onEvent: (TtsConnectionEvent) -> Unit = {}
    private var instanceId: String? = null
    private val audioCalls = java.util.concurrent.ConcurrentHashMap<String, okhttp3.Call>()
    @Volatile private var currentId: String? = null
    @Volatile private var socket: WebSocket? = null
    private val backoff = TtsBackoff()
    @Volatile private var lastPong = System.nanoTime()
    private var heartbeatTask: java.util.concurrent.ScheduledFuture<*>? = null
    private val scheduler = java.util.concurrent.Executors.newSingleThreadScheduledExecutor { runnable ->
        Thread(runnable, "twicc-tts-heartbeat").apply { isDaemon = true }
    }

    fun request(url: String): Request {
        val request = Request.Builder().url(url).build()
        require(cookieJar.allows(request.url)) { "TTS requires the active HTTPS instance." }
        return request
    }
    // Compatibility seam used by the real engine credential boundary test.
    fun connect(url: String, listener: WebSocketListener): WebSocket = client.newWebSocket(
        request(url).newBuilder().header("Origin", originHeader()).build(), listener)
    private fun originHeader() = cookieJar.origin.newBuilder().encodedPath("/").query(null).fragment(null)
        .build().toString().removeSuffix("/")

    /** Send open/resume first. Retained commands belong inside resume and keep their original sequences. */
    @Synchronized fun connect(credentials: TtsCredentials, resume: TtsFrame): String {
        require(cookieJar.allows(credentials.origin.toHttpUrl()))
        require(instanceId == null || instanceId == credentials.instanceId)
        require(resume["type"] == "open" || resume["type"] == "resume")
        instanceId = credentials.instanceId
        currentId = null; socket?.cancel(); heartbeatTask?.cancel(false)
        val id = java.util.UUID.randomUUID().toString(); currentId = id
        val text = TtsJson.encode(resume)
        socket = connect(cookieJar.origin.newBuilder().encodedPath("/ws/tts/").query(null).fragment(null).build().toString(), object : WebSocketListener() {
            override fun onOpen(webSocket: WebSocket, response: okhttp3.Response) {
                opened(id, webSocket, text)
            }
            override fun onMessage(webSocket: WebSocket, text: String) {
                if (currentId != id) return
                val frame = try { TtsJson.decode(text) } catch (_: Exception) { lost(id); return }
                if (frame["protocol"] != 1 && frame["protocol"] != 1L) { lost(id); return }
                if (frame["type"] == "pong") lastPong = System.nanoTime()
                onEvent(TtsConnectionEvent.Frame(id, frame))
            }
            override fun onClosing(webSocket: WebSocket, code: Int, reason: String) {
                webSocket.close(code, null)
                if (code == 4001) unauthorized(id) else lost(id)
            }
            override fun onFailure(webSocket: WebSocket, t: Throwable, response: okhttp3.Response?) {
                val auth = response != null && (response.code in 300..399 || response.code == 401 || response.code == 403 ||
                    response.header("Content-Type")?.contains("html", true) == true)
                response?.close()
                if (auth) unauthorized(id) else lost(id)
            }
        })
        return id
    }
    @Synchronized private fun opened(id: String, webSocket: WebSocket, handshake: String) {
        if (currentId != id) { webSocket.cancel(); return }
        lastPong = System.nanoTime()
        onEvent(TtsConnectionEvent.Opened(id))
        if (!webSocket.send(handshake)) { lost(id); return }
        heartbeatTask = scheduler.scheduleAtFixedRate({ heartbeat(id) }, 30, 30, java.util.concurrent.TimeUnit.SECONDS)
    }
    /** The service calls this only after its controller accepts the resumed state. */
    fun reconciled(connectionId: String) { if (currentId == connectionId) backoff.reconciled() }
    /** Also used by the scheduled heartbeat. Sequence allocation stays with the policy owner. */
    fun heartbeat(connectionId: String) {
        if (currentId != connectionId) return
        if (System.nanoTime() - lastPong >= java.util.concurrent.TimeUnit.SECONDS.toNanos(75)) lost(connectionId)
        else onEvent(TtsConnectionEvent.HeartbeatDue(connectionId))
    }
    @Synchronized private fun lost(id: String) {
        if (currentId != id) return
        currentId = null; heartbeatTask?.cancel(false); socket?.cancel(); socket = null
        onEvent(TtsConnectionEvent.Lost(id, backoff.failed()))
    }
    @Synchronized private fun unauthorized(id: String) {
        if (currentId != id) return
        currentId = null; heartbeatTask?.cancel(false); socket?.cancel(); socket = null
        onEvent(TtsConnectionEvent.AuthorizationLost(id))
    }
    fun send(frame: TtsFrame): Boolean = socket?.send(TtsJson.encode(frame)) ?: false
    private fun resourceRequest(resource: AudioResource, ending: Boolean = false): Request {
        require(instanceId == resource.instanceId) { "Audio belongs to another instance" }
        require(listOf(resource.sessionId, resource.audioId).all { Regex("[A-Za-z0-9_-]+").matches(it) })
        val url = cookieJar.origin.newBuilder().encodedPath("/api/tts/native/").query(null).fragment(null)
            .addPathSegment(resource.sessionId).addPathSegment("audio").addPathSegment(resource.audioId).addPathSegment("")
            .apply { if (ending) addPathSegment("ending").addPathSegment("") }.build()
        return request(url.toString()).newBuilder().header("X-TwiCC-TTS-Session", resource.resumeSecret).build()
    }
    private fun validate(response: okhttp3.Response) {
        if (response.code in 300..399 || response.code == 401 || response.code == 403 ||
            response.header("Content-Type")?.contains("html", true) == true) {
            response.close(); throw TtsAuthorizationException()
        }
        if (!response.isSuccessful) { val status = response.code; response.close(); throw IOException("TTS request failed ($status)") }
    }
    private fun audioKey(resource: AudioResource) = "${resource.sessionId}/${resource.audioId}"
    /** Cancels an opening or streaming body immediately, without stopping the control socket. */
    fun cancelAudio(resource: AudioResource) { audioCalls.remove(audioKey(resource))?.cancel() }
    fun openAudio(resource: AudioResource): TtsAudioStream {
        val call = client.newCall(resourceRequest(resource))
        val key = audioKey(resource)
        if (audioCalls.putIfAbsent(key, call) != null) throw IOException("This audio resource is already open")
        var response: okhttp3.Response? = null
        try {
            response = call.execute()
            validate(response)
            val body = response.body ?: throw IOException("TTS audio is empty")
            // Detect a tunnel that returns HTML with a misleading media content type, without consuming audio bytes.
            val source = body.source()
            source.request(32)
            val prefix = source.peek().readUtf8(minOf(32L, source.buffer.size)).trimStart().lowercase()
            if (prefix.startsWith("<!doctype html") || prefix.startsWith("<html") || prefix.startsWith("<head")) throw TtsAuthorizationException()
            return TtsAudioStream(call, response) { audioCalls.remove(key, call) }
        } catch (error: Exception) { response?.close(); call.cancel(); audioCalls.remove(key, call); throw error }
    }
    fun readEnding(resource: AudioResource): TtsFrame {
        val call = client.newCall(resourceRequest(resource, ending = true))
        call.timeout().timeout(10, java.util.concurrent.TimeUnit.SECONDS)
        return call.execute().use { response ->
            validate(response)
            val body = response.body ?: throw IOException("TTS ending is empty")
            val source = body.source()
            if (source.request(65537) && source.buffer.size > 65536) throw IOException("TTS ending is too large")
            val text = source.readUtf8()
            if (text.trimStart().startsWith("<")) throw TtsAuthorizationException()
            try { TtsJson.decode(text) } catch (_: Exception) { throw IOException("Invalid TTS ending") }
        }
    }
    fun release(resource: AudioResource) {
        cancelAudio(resource)
        val call = client.newCall(resourceRequest(resource).newBuilder().delete().build())
        call.timeout().timeout(10, java.util.concurrent.TimeUnit.SECONDS)
        call.execute().use { response -> if (response.code != 404 && response.code != 410) validate(response) }
    }
    @Synchronized fun close() {
        currentId = null; heartbeatTask?.cancel(false); socket?.cancel(); socket = null
        scheduler.shutdownNow()
        audioCalls.values.forEach { it.cancel() }; audioCalls.clear()
        cookieJar.clear(); client.dispatcher.cancelAll(); client.connectionPool.evictAll()
    }
}

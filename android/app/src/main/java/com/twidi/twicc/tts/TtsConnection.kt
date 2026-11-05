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

/** Native HTTP and WSS share credentials, TLS, and origin restrictions. No redirect can hide a login challenge. */
class TtsConnection(
    origin: String,
    cookies: List<TtsCookie>,
    configure: OkHttpClient.Builder.() -> Unit = {},
) {
    val cookieJar = TtsCookieJar(origin.toHttpUrl(), cookies)
    val client = OkHttpClient.Builder().apply(configure).cookieJar(cookieJar)
        .followRedirects(false).followSslRedirects(false)
        .addInterceptor { chain ->
            if (!cookieJar.allows(chain.request().url)) throw IOException("TTS requires the active HTTPS instance.")
            chain.proceed(chain.request())
        }.build()

    fun request(url: String): Request {
        val request = Request.Builder().url(url).build()
        require(cookieJar.allows(request.url)) { "TTS requires the active HTTPS instance." }
        return request
    }
    fun connect(url: String, listener: WebSocketListener): WebSocket = client.newWebSocket(
        request(url).newBuilder().header("Origin", cookieJar.origin.newBuilder().encodedPath("/")
            .build().toString().removeSuffix("/")).build(), listener)
    fun close() {
        cookieJar.clear()
        client.dispatcher.cancelAll()
        client.connectionPool.evictAll()
    }
}

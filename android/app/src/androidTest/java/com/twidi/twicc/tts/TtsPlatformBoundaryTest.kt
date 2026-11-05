package com.twidi.twicc.tts

import android.app.ActivityManager
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import androidx.test.core.app.ActivityScenario
import androidx.test.platform.app.InstrumentationRegistry
import com.twidi.twicc.MainActivity
import androidx.test.ext.junit.runners.AndroidJUnit4
import org.junit.Assert.*
import org.junit.Test
import org.junit.runner.RunWith
import okhttp3.HttpUrl.Companion.toHttpUrl
import okhttp3.*
import okhttp3.mockwebserver.*
import okhttp3.tls.*
import okhttp3.tls.decodeCertificatePem
import com.twidi.twicc.Instance
import com.twidi.twicc.NavigationDecision
import com.twidi.twicc.NavigationRequest
import com.twidi.twicc.browser.*
import com.twidi.twicc.platform.*
import java.io.File
import java.util.UUID
import java.util.concurrent.CompletableFuture
import java.util.concurrent.TimeUnit

/** These tests use the real native jar. Browser profile tests follow in the same fixture. */
@RunWith(AndroidJUnit4::class)
class TtsPlatformBoundaryTest {
    @Test fun foreground_service_survives_activity_close() {
        val instrumentation = InstrumentationRegistry.getInstrumentation()
        val context = instrumentation.targetContext
        instrumentation.uiAutomation.executeShellCommand("pm grant ${context.packageName} android.permission.POST_NOTIFICATIONS").close()
        val activity = ActivityScenario.launch(MainActivity::class.java)
        activity.onActivity { TtsService.start(it) }
        activity.close()
        val dwell = if (InstrumentationRegistry.getArguments().getString("longProbe") == "true") 610_000L else 1_000L
        val end = android.os.SystemClock.elapsedRealtime() + dwell
        while (android.os.SystemClock.elapsedRealtime() < end) Thread.sleep(1_000)
        @Suppress("DEPRECATION")
        val service = context.getSystemService(ActivityManager::class.java).getRunningServices(100)
            .single { it.service.className == TtsService::class.java.name }
        assertTrue(service.foreground)
        val declaration = context.packageManager.getServiceInfo(service.service, 0)
        assertFalse(declaration.exported)
        assertEquals(0, declaration.flags and ServiceInfo.FLAG_STOP_WITH_TASK)
        assertEquals(ServiceInfo.FOREGROUND_SERVICE_TYPE_MEDIA_PLAYBACK or ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE,
            declaration.foregroundServiceType)
        context.stopService(Intent(context, TtsService::class.java))
    }

    @Test fun cookie_jar_filters_paths_expiry_origins_and_logout() {
        val origin = "https://localhost:4443/".toHttpUrl()
        val cookies = listOf(
            TtsCookie("auth", "root-secret", "localhost", "/", true, true, true, null),
            TtsCookie("auth", "api-secret", "localhost", "/api/", true, true, true, null),
            TtsCookie("auth", "other-instance-secret", "other.localhost", "/", true, true, true, null),
            TtsCookie("expired", "expired-secret", "localhost", "/", true, true, true, 1),
        )
        val jar = TtsCookieJar(origin, cookies)
        val exported = jar.loadForRequest(origin.resolve("/api/tts/")!!)
        assertEquals(setOf("/", "/api/"), exported.filter { it.name == "auth" }.map { it.path }.toSet())
        assertFalse(exported.any { it.value == "other-instance-secret" })
        assertFalse(exported.any { it.name == "expired" })
        assertTrue(jar.loadForRequest("https://other.localhost:4443/api/".toHttpUrl()).isEmpty())
        assertEquals(listOf("root-secret"), jar.loadForRequest(origin).map { it.value })
        assertTrue(jar.loadForRequest("https://localhost:4444/api/".toHttpUrl()).isEmpty())
        jar.clear()
        assertTrue(jar.loadForRequest(origin).isEmpty())
    }
    @Test fun export_keeps_same_name_path_cookies_and_profile_isolation() {
        val instrumentation = InstrumentationRegistry.getInstrumentation()
        val assets = instrumentation.context.assets
        val certificate = HeldCertificate.decode(assets.open("tts-cert.pem").bufferedReader().readText() +
            assets.open("tts-server-key.fixture").bufferedReader().readText())
        val tls = HandshakeCertificates.Builder().heldCertificate(certificate).build()
        val trust = HandshakeCertificates.Builder().addTrustedCertificate(assets.open("tts-ca.pem").bufferedReader().readText().decodeCertificatePem()).build()
        prepareGeckoTestProfile()
        val server = MockWebServer().apply { useHttps(tls.sslSocketFactory(), false) }
        server.dispatcher = object : okhttp3.mockwebserver.Dispatcher() {
            override fun dispatch(request: RecordedRequest): MockResponse = when (request.path) {
                "/seed/a", "/seed/b" -> {
                    val root = if (request.path == "/seed/a") "instance-secret" else "other-instance-secret"
                    MockResponse().addHeader("Content-Type", "text/html")
                        .addHeader("Set-Cookie", "auth=$root; Path=/; Secure; HttpOnly")
                        .addHeader("Set-Cookie", "auth=api-secret; Path=/api/; Secure; HttpOnly")
                        .addHeader("Set-Cookie", "tunnel=tunnel-secret; Path=/ws/; Secure; HttpOnly")
                        .addHeader("Set-Cookie", "private=unrelated-secret; Path=/unrelated/; Secure; HttpOnly")
                        .addHeader("Set-Cookie", "future=future-secret; Max-Age=3600; Path=/; Secure; HttpOnly")
                        .addHeader("Set-Cookie", "expired=gone; Max-Age=0; Path=/; Secure; HttpOnly")
                        .setBody("""<script>setTimeout(async () => {
                            const auth = await fetch('/browser-auth/' + location.pathname.split('/').pop());
                            const value = 'page-cookie:' + document.cookie + ':' + auth.status;
                            if (window.TwiccAndroid) window.TwiccAndroid.postMessage(value);
                            else {
                                const reply = event => {
                                    if (event.data?.channel !== 'twicc.android.response') return;
                                    window.removeEventListener('message', reply);
                                    window.postMessage({channel:'twicc.android.request',request:value}, location.origin);
                                };
                                window.addEventListener('message', reply);
                                window.postMessage({channel:'twicc.android.request',request:{protocol:1,id:'menu-probe',command:'getState',ttsDocument:'00000000-0000-0000-0000-000000000000'}}, location.origin);
                            }
                        }, 500)</script>""")
                }
                "/browser-auth/a", "/browser-auth/b" -> {
                    val expected = if (request.path == "/browser-auth/a") "instance-secret" else "other-instance-secret"
                    MockResponse().setResponseCode(if (request.getHeader("Cookie").orEmpty().contains("auth=$expected")) 200 else 401)
                }
                "/api/tts/" -> if (request.getHeader("Cookie").orEmpty().contains("auth=instance-secret") &&
                    request.getHeader("Cookie").orEmpty().contains("auth=api-secret")) MockResponse().setBody("authenticated")
                    else MockResponse().setResponseCode(401)
                "/ws/tts/" -> if (request.getHeader("Cookie").orEmpty().contains("tunnel=tunnel-secret"))
                    MockResponse().withWebSocketUpgrade(object : WebSocketListener() {
                        override fun onOpen(webSocket: WebSocket, response: Response) { webSocket.send("authenticated") }
                        override fun onClosing(webSocket: WebSocket, code: Int, reason: String) { webSocket.close(code, reason) }
                    }) else MockResponse().setResponseCode(401)
                "/redirect" -> MockResponse().setResponseCode(302).addHeader("Location", "https://other.localhost/steal")
                "/logout" -> MockResponse().addHeader("Set-Cookie", "auth=; Max-Age=0; Path=/; Secure; HttpOnly")
                    .addHeader("Set-Cookie", "auth=; Max-Age=0; Path=/api/; Secure; HttpOnly")
                else -> MockResponse().setResponseCode(404)
            }
        }
        server.start()
        val serverAddress = server.url("/").toString()
        val activity = ActivityScenario.launch(MainActivity::class.java)
        val sessions = mutableListOf<BrowserSession>()
        val nativeCookies = CompletableFuture<List<TtsCookie>>()
        try {
            repeat(2) { index ->
                val pageCookies = CompletableFuture<String>()
                activity.onActivity { owner ->
                    val instance = Instance(UUID.randomUUID().toString(), "Test", serverAddress.removeSuffix("/"))
                    val events = testEvents(pageCookies)
                    val session = EngineProvider.engine.createSession(owner, instance, events, NativeRequests(owner))
                    sessions.add(session)
                    session.load(serverAddress + if (index == 0) "seed/a" else "seed/b")
                }
                assertEquals("page-cookie::200", pageCookies.get(30, TimeUnit.SECONDS))
            }
            val rejected = CompletableFuture<Boolean>()
            activity.onActivity {
                sessions.first().exportTtsCookies(listOf("https://other.localhost/api/tts/")) { rejected.complete(it.isFailure) }
            }
            assertTrue(rejected.get(10, TimeUnit.SECONDS))
            // Closing the session in the SAME main-thread turn forces closure before an asynchronous reply.
            activity.onActivity {
                TtsService.start(it)
                logGeckoStoreDiagnostics()
                sessions.first().exportTtsCookies(listOf(serverAddress + "api/tts/", serverAddress + "ws/tts/")) {
                    it.fold(nativeCookies::complete, nativeCookies::completeExceptionally)
                }
                assertFalse("Export must still be pending when the Activity closes", nativeCookies.isDone)
                sessions.forEach { it.close() }
                sessions.clear()
                it.finish()
            }
            activity.close()
            val exported = nativeCookies.get(30, TimeUnit.SECONDS)
            assertEquals(setOf("/", "/api/"), exported.filter { it.name == "auth" }.map { it.path }.toSet())
            assertFalse(exported.any { it.value == "other-instance-secret" })
            assertFalse(exported.any { it.name == "expired" || it.name == "private" })
            assertTrue(exported.single { it.name == "future" }.expiresAt!! > System.currentTimeMillis())
            assertTrue(exported.all { it.httpOnly && it.secure && it.hostOnly })
            val connection = TtsConnection(server.url("/").toString(), exported) {
                sslSocketFactory(trust.sslSocketFactory(), trust.trustManager)
            }
            try {
                connection.client.newCall(connection.request(server.url("/api/tts/").toString())).execute().use {
                    assertEquals(200, it.code)
                    assertEquals("authenticated", it.body!!.string())
                }
                val socket = CompletableFuture<String>()
                val socketClosed = CompletableFuture<Unit>()
                val ws = connection.connect(server.url("/ws/tts/").toString().replace("https:", "wss:"), object : WebSocketListener() {
                    override fun onMessage(webSocket: WebSocket, text: String) { socket.complete(text) }
                    override fun onClosed(webSocket: WebSocket, code: Int, reason: String) { socketClosed.complete(Unit) }
                    override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) { socket.completeExceptionally(t) }
                })
                assertEquals("authenticated", socket.get(10, TimeUnit.SECONDS))
                ws.close(1000, "done")
                socketClosed.get(10, TimeUnit.SECONDS)
                connection.client.newCall(connection.request(server.url("/redirect").toString())).execute().use {
                    assertEquals(302, it.code)
                }
                connection.client.newCall(connection.request(server.url("/logout").toString())).execute().close()
                connection.client.newCall(connection.request(server.url("/api/tts/").toString())).execute().use {
                    assertEquals(401, it.code)
                }
            } finally { connection.close() }
        } finally {
            instrumentation.runOnMainSync { sessions.forEach { it.close() } }
            activity.close()
            instrumentation.targetContext.stopService(Intent(instrumentation.targetContext, TtsService::class.java))
            server.shutdown()
        }
    }

    private fun testEvents(pageCookies: CompletableFuture<String>) = object : BrowserEvents {
        override fun onNavigationRequest(request: NavigationRequest) = NavigationDecision.ALLOW
        override fun onPageStarted(url: String) {}
        override fun onLocationChanged(url: String) {}
        override fun onProgressChanged(progress: Int) {}
        override fun onPageFinished() {}
        override fun onHistoryChanged(canGoBack: Boolean) {}
        override fun onFailure(message: String) { pageCookies.completeExceptionally(AssertionError(message)) }
        override fun onRendererStopped() { pageCookies.completeExceptionally(AssertionError("Renderer stopped")) }
        override fun onAppMessage(message: String): String {
            if (message.startsWith("page-cookie:")) pageCookies.complete(message)
            return "{}"
        }
        override fun onDownload(source: DownloadSource) {}
    }

    /** Store identity is test diagnostic metadata. Never print cookie names or values. */
    private fun logGeckoStoreDiagnostics() {
        if (!EngineProvider.engine.displayName.startsWith("Gecko")) return
        val owner = Class.forName("com.twidi.twicc.engines.gecko.TtsExports")
        val field = owner.getDeclaredField("documents").apply { isAccessible = true }
        val documents = field.get(null) as Map<*, *>
        for (document in documents.values) {
            val store = document!!.javaClass.getDeclaredField("storeId").apply { isAccessible = true }.get(document)
            android.util.Log.i("TtsBoundary", "Pinned Gecko actual tab cookieStoreId=$store")
        }
    }

    /** Gecko gets an isolated test profile containing ONLY the fixture CA. Ordinary APK TLS is unchanged. */
    private fun prepareGeckoTestProfile() {
        if (!EngineProvider.engine.displayName.startsWith("Gecko")) return
        val instrumentation = InstrumentationRegistry.getInstrumentation()
        val profile = File(instrumentation.targetContext.cacheDir, "tts-boundary-gecko-profile").apply { mkdirs() }
        instrumentation.context.assets.open("tts-ca-profile/cert9.db").use { input ->
            File(profile, "cert9.db").outputStream().use { input.copyTo(it) }
        }
        instrumentation.runOnMainSync {
            val engine = Class.forName("com.twidi.twicc.engines.gecko.GeckoEngine")
            val field = engine.getDeclaredField("runtime").apply { isAccessible = true }
            if (field.get(null) == null) {
                val builder = Class.forName("org.mozilla.geckoview.GeckoRuntimeSettings\$Builder").getConstructor().newInstance()
                builder.javaClass.getMethod("arguments", Array<String>::class.java)
                    .invoke(builder, arrayOf("--profile", profile.absolutePath))
                val settings = builder.javaClass.getMethod("build").invoke(builder)
                val runtime = Class.forName("org.mozilla.geckoview.GeckoRuntime").getMethod("create", Context::class.java, settings.javaClass)
                    .invoke(null, instrumentation.targetContext, settings)
                field.set(null, runtime)
            }
        }
    }

}

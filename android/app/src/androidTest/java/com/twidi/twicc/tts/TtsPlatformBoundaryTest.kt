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
import org.junit.Assume.assumeTrue
import org.json.JSONObject
import java.lang.reflect.Proxy
import org.junit.Test
import org.junit.runner.RunWith
import org.junit.FixMethodOrder
import org.junit.runners.MethodSorters
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
@FixMethodOrder(MethodSorters.NAME_ASCENDING)
class TtsPlatformBoundaryTest {
    @Test fun foreground_service_survives_activity_close() {
        val instrumentation = InstrumentationRegistry.getInstrumentation()
        val context = instrumentation.targetContext
        instrumentation.uiAutomation.executeShellCommand("pm grant ${context.packageName} android.permission.POST_NOTIFICATIONS").close()
        val activity = ActivityScenario.launch(MainActivity::class.java)
        val binding = bindService(context)
        activity.onActivity {
            binding.binder.configure(TtsActivationSource("boundary", "Boundary", "https://example.test") { _, _ -> })
            binding.binder.dispatch(TtsCommand.AttachDocument(DocumentOwner("boundary", "page", 1)))
            assertTrue(binding.binder.dispatch(TtsCommand.Manual(DocumentOwner("boundary", "page", 1), "probe",
                mapOf("kind" to "text", "source" to "test", "text" to "Probe", "speech" to mapOf("text" to "Probe")))).accepted)
        }
        context.unbindService(binding.connection)
        activity.onActivity { it.finishAndRemoveTask() }
        activity.close()
        val arguments = InstrumentationRegistry.getArguments()
        val dwell = when {
            arguments.getString("longProbe") == "true" -> 610_000L
            arguments.getString("forceStopProbe") == "true" -> 30_000L
            else -> 1_000L
        }
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
        assertTrue(currentWakeLocks().contains("TwiCC:NativeTts"))
        context.stopService(Intent(context, TtsService::class.java))
        Thread.sleep(500)
        assertFalse(currentWakeLocks().contains("TwiCC:NativeTts"))
    }

    // This test lowers a persistent OS channel setting. Run it last; Gradle removes the fixture app after the suite.
    @Test fun zz_blockedNotificationChannelRejectsActivation() {
        val instrumentation = InstrumentationRegistry.getInstrumentation()
        val context = instrumentation.targetContext
        val manager = context.getSystemService(android.app.NotificationManager::class.java)
        manager.createNotificationChannel(android.app.NotificationChannel("tts_playback", "Speech", android.app.NotificationManager.IMPORTANCE_NONE))
        val activity = ActivityScenario.launch(MainActivity::class.java)
        val binding = bindService(context)
        try {
            activity.onActivity {
                binding.binder.configure(TtsActivationSource("blocked", "Blocked", "https://example.test") { _, _ -> fail("Rejected activation exports no cookies") })
                val owner = DocumentOwner("blocked", "page", 1)
                binding.binder.dispatch(TtsCommand.AttachDocument(owner))
                val acceptance = binding.binder.dispatch(TtsCommand.Manual(owner, "rejected", emptyMap()))
                assertFalse(acceptance.accepted)
                assertEquals("notification_settings", acceptance.error!!.action)
                assertEquals("idle", binding.binder.snapshot().sessionStatus)
            }
            assertTrue(manager.activeNotifications.none { it.id == 1001 })
            assertFalse(currentWakeLocks().contains("TwiCC:NativeTts"))
        } finally {
            context.unbindService(binding.connection)
            activity.close()
        }
    }

    @Test fun notificationStopDuringUnavailableConnectionReleasesForegroundAndWakeLock() {
        val instrumentation = InstrumentationRegistry.getInstrumentation()
        val context = instrumentation.targetContext
        val assets = instrumentation.context.assets
        val certificate = HeldCertificate.decode(assets.open("tts-cert.pem").bufferedReader().readText() +
            assets.open("tts-server-key.fixture").bufferedReader().readText())
        val tls = HandshakeCertificates.Builder().heldCertificate(certificate).build()
        val disconnected = CompletableFuture<Unit>()
        val frames = java.util.concurrent.ConcurrentLinkedQueue<String>()
        val server = MockWebServer().apply { useHttps(tls.sslSocketFactory(), false) }
        server.enqueue(MockResponse().withWebSocketUpgrade(object : WebSocketListener() {
            override fun onMessage(webSocket: WebSocket, text: String) {
                val type = JSONObject(text).optString("type")
                frames += type
                when (type) {
                    "open" -> webSocket.send("""{"protocol":1,"type":"opened_session","session_id":"session","resume_secret":"secret","revision":1}""")
                    "manual" -> { disconnected.complete(Unit); webSocket.close(1013, "Fixture outage") }
                }
            }
        }))
        server.start()
        val serverOrigin = server.url("/").toString().removeSuffix("/")
        shell("pm grant ${context.packageName} android.permission.POST_NOTIFICATIONS")
        val activity = ActivityScenario.launch(MainActivity::class.java)
        val binding = bindService(context)
        val manager = context.getSystemService(android.app.NotificationManager::class.java)
        try {
            activity.onActivity {
                binding.binder.configure(TtsActivationSource("stop", "Stop", serverOrigin) { _, callback -> callback(Result.success(emptyList())) })
                val owner = DocumentOwner("stop", "page", 1)
                binding.binder.dispatch(TtsCommand.AttachDocument(owner))
                assertTrue(binding.binder.dispatch(TtsCommand.Manual(owner, "stop-probe", mapOf("kind" to "text", "text" to "Probe"))).accepted)
            }
            try { disconnected.get(10, TimeUnit.SECONDS) } catch (error: java.util.concurrent.TimeoutException) {
                var state: NativeTtsSnapshot? = null
                instrumentation.runOnMainSync { state = binding.binder.snapshot() }
                val current = checkNotNull(state)
                fail("No manual frame: status=${current.sessionStatus}, error=${current.error}, frames=$frames, requests=${server.requestCount}")
            }
            val reconnectDeadline = android.os.SystemClock.elapsedRealtime() + 3000
            var reconnecting = false
            while (!reconnecting && android.os.SystemClock.elapsedRealtime() < reconnectDeadline) {
                instrumentation.runOnMainSync { reconnecting = binding.binder.snapshot().sessionStatus == "reconnecting" }
                if (!reconnecting) Thread.sleep(50)
            }
            assertTrue("Actual WSS failure must reach the service", reconnecting)
            context.unbindService(binding.connection)
            activity.close()
            val notification = manager.activeNotifications.single { it.id == 1001 }.notification
            assertEquals(listOf("Skip", "Stop all"), notification.actions.map { it.title.toString() })
            notification.actions.last().actionIntent.send()
            val deadline = android.os.SystemClock.elapsedRealtime() + 7000
            while (manager.activeNotifications.any { it.id == 1001 } && android.os.SystemClock.elapsedRealtime() < deadline) Thread.sleep(100)
            assertTrue(manager.activeNotifications.none { it.id == 1001 })
            assertFalse(currentWakeLocks().contains("TwiCC:NativeTts"))
            Thread.sleep(500)
            @Suppress("DEPRECATION")
            assertTrue(context.getSystemService(ActivityManager::class.java).getRunningServices(100).none { it.service.className == TtsService::class.java.name })
        } finally {
            context.stopService(Intent(context, TtsService::class.java))
            runCatching { context.unbindService(binding.connection) }
            activity.close()
            server.shutdown()
        }
    }

    @Test fun deniedNotificationsRejectBeforeCredentials() {
        val instrumentation = InstrumentationRegistry.getInstrumentation()
        val context = instrumentation.targetContext
        val activity = ActivityScenario.launch(MainActivity::class.java)
        val binding = bindService(context)
        try {
            if (InstrumentationRegistry.getArguments().getString("requireRuntimeDenied") == "true")
                assertEquals(android.content.pm.PackageManager.PERMISSION_DENIED,
                    context.checkSelfPermission(android.Manifest.permission.POST_NOTIFICATIONS))
            shell("appops set --uid ${context.packageName} POST_NOTIFICATION ignore")
            val appOps = shell("appops get ${context.packageName} POST_NOTIFICATION")
            assertTrue(appOps, appOps.contains("ignore"))
            assertNotNull(TtsNotification(context).error())
            activity.onActivity {
                binding.binder.configure(TtsActivationSource("denied", "Denied", "https://example.test") { _, _ -> fail("Denied activation exports no cookies") })
                val owner = DocumentOwner("denied", "page", 1)
                binding.binder.dispatch(TtsCommand.AttachDocument(owner))
                val acceptance = binding.binder.dispatch(TtsCommand.Manual(owner, "denied", emptyMap()))
                assertFalse(acceptance.accepted)
                assertEquals("notification_settings", acceptance.error!!.action)
            }
            assertTrue(context.getSystemService(android.app.NotificationManager::class.java).activeNotifications.none { it.id == 1001 })
        } finally {
            shell("appops set --uid ${context.packageName} POST_NOTIFICATION allow")
            context.unbindService(binding.connection)
            activity.close()
        }
    }

    @Test fun batteryExemptionIsOptionalForListening() {
        val instrumentation = InstrumentationRegistry.getInstrumentation()
        val context = instrumentation.targetContext
        shell("pm grant ${context.packageName} android.permission.POST_NOTIFICATIONS")
        shell("cmd deviceidle whitelist -${context.packageName}")
        val activity = ActivityScenario.launch(MainActivity::class.java)
        val binding = bindService(context)
        try {
            assertFalse(context.getSystemService(android.os.PowerManager::class.java).isIgnoringBatteryOptimizations(context.packageName))
            activity.onActivity {
                binding.binder.configure(TtsActivationSource("battery", "Battery", "https://example.test") { _, _ -> })
                val owner = DocumentOwner("battery", "page", 1)
                binding.binder.dispatch(TtsCommand.AttachDocument(owner))
                val acceptance = binding.binder.dispatch(TtsCommand.Listen(owner, ListeningScope("global", null), false, "2026-11-05T10:00:00.000000Z"))
                assertTrue(acceptance.accepted)
                assertNotNull(binding.binder.snapshot().listening)
                assertTrue(binding.binder.dispatch(TtsCommand.Manual(owner, "manual", mapOf("kind" to "text", "text" to "Probe"))).accepted)
            }
            val deadline = android.os.SystemClock.elapsedRealtime() + 5000
            val notifications = context.getSystemService(android.app.NotificationManager::class.java)
            while (notifications.activeNotifications.none { it.id == TtsNotification.ID } &&
                android.os.SystemClock.elapsedRealtime() < deadline) Thread.sleep(25)
            assertTrue("Android publishes the foreground notification", notifications.activeNotifications.any { it.id == TtsNotification.ID })
        } finally {
            context.stopService(Intent(context, TtsService::class.java))
            context.unbindService(binding.connection)
            activity.close()
        }
    }

    /** Real Service + transport + player. Only the provider/backend responses are fixtures. */
    private inner class ServiceFixture : AutoCloseable {
        val instrumentation = InstrumentationRegistry.getInstrumentation()
        val context = instrumentation.targetContext
        val instance = UUID.randomUUID().toString()
        val owner = DocumentOwner(instance, "fixture-page", TtsService.nextDocumentGeneration())
        val frames = java.util.concurrent.CopyOnWriteArrayList<JSONObject>()
        val requests = java.util.concurrent.CopyOnWriteArrayList<RecordedRequest>()
        val endingEntered = CompletableFuture<Unit>()
        val endingGate = java.util.concurrent.CountDownLatch(1)
        @Volatile var holdEnding = false
        @Volatile var socket: WebSocket? = null
        @Volatile var activation: String? = null
        @Volatile var revision = 1L
        @Volatile var pageScript = ""
        @Volatile var clientGate = java.util.concurrent.CountDownLatch(1)
        @Volatile var holdManualAudio = false
        val manualFrame = CompletableFuture<JSONObject>()
        val resumeFrame = CompletableFuture<JSONObject>()
        val resumeGate = java.util.concurrent.CountDownLatch(1)
        @Volatile var holdResume = false
        fun deliverAudio(frame: JSONObject) {
            socket!!.send(JSONObject().put("protocol", 1).put("type", "audio")
                .put("reading_id", frame.getString("reading_id")).put("generation", frame.getLong("generation"))
                .put("audio_id", frame.getString("reading_id")).put("content_type", "audio/pcm;rate=16000;channels=1").toString())
        }
        val server = MockWebServer()
        val activity: ActivityScenario<MainActivity>
        val binding: Binding
        var bound = true
        init {
            val assets = instrumentation.context.assets
            val certificate = HeldCertificate.decode(assets.open("tts-cert.pem").bufferedReader().readText() +
                assets.open("tts-server-key.fixture").bufferedReader().readText())
            server.useHttps(HandshakeCertificates.Builder().heldCertificate(certificate).build().sslSocketFactory(), false)
            server.protocols = listOf(Protocol.HTTP_1_1)
            val samples = assets.open("three-tones.pcm").use { it.readBytes() }
            server.dispatcher = object : okhttp3.mockwebserver.Dispatcher() {
                override fun dispatch(request: RecordedRequest): MockResponse {
                    requests += request
                    if (request.path == "/client") return MockResponse().setHeader("Content-Type", "text/html")
                        .addHeader("Set-Cookie", "fixture-auth=httponly; Secure; HttpOnly; Path=/")
                        .setBody("""<!doctype html><body>Native TTS fixture<script type="module">
                            import {createAndroidAppClient} from '/androidAppClient.js';
                            import {createNativeTtsClient} from '/nativeTtsClient.js';
                            const app = createAndroidAppClient(window, {timeoutMs:5000});
                            const native = createNativeTtsClient({appClient:app,document});
                            window.addEventListener('pagehide', () => native.detach());
                            await native.attach();
                            await fetch('/fixture/client-start');
                            $pageScript
                        </script>""")
                    if (request.path in setOf("/androidAppClient.js", "/nativeTtsClient.js")) return MockResponse()
                        .setHeader("Content-Type", "text/javascript").setBody(assets.open(request.path!!.removePrefix("/")).bufferedReader().readText())
                    if (request.path == "/api/tts/native/capabilities/") return MockResponse().setHeader("Content-Type", "application/json")
                        .setBody("""{"protocol":1,"available":true}""")
                    if (request.path == "/fixture/client-start") clientGate.await(15, TimeUnit.SECONDS)
                    if (request.path!!.startsWith("/fixture/")) return MockResponse().setResponseCode(204)
                    if (request.path == "/ws/tts/") return MockResponse().withWebSocketUpgrade(object : WebSocketListener() {
                        override fun onOpen(webSocket: WebSocket, response: Response) { socket = webSocket }
                        override fun onMessage(webSocket: WebSocket, text: String) {
                            val frame = JSONObject(text); frames += frame
                            when (frame.getString("type")) {
                                "open" -> webSocket.send("""{"protocol":1,"type":"opened_session","session_id":"fixture","resume_secret":"secret","revision":1}""")
                                "manual", "select" -> {
                                    manualFrame.complete(frame)
                                    if (!holdManualAudio) deliverAudio(frame)
                                }
                                "listen" -> activation = frame.getString("activation_id")
                                "resume" -> {
                                    resumeFrame.complete(frame)
                                    if (holdResume) resumeGate.await(10, TimeUnit.SECONDS)
                                }
                                "ping" -> webSocket.send(JSONObject().put("protocol", 1).put("type", "pong").put("ack_seq", frame.getLong("seq")).toString())
                            }
                            webSocket.send(JSONObject().put("protocol", 1).put("type", "state").put("revision", ++revision)
                                .put("ack_seq", frame.getLong("seq")).put("configuration_available", true).toString())
                        }
                        override fun onClosing(webSocket: WebSocket, code: Int, reason: String) { webSocket.close(code, reason) }
                    })
                    if (!request.path!!.startsWith("/api/tts/native/")) return MockResponse().setResponseCode(404)
                    assertEquals("secret", request.getHeader("X-TwiCC-TTS-Session"))
                    assertNull(request.getHeader("Range"))
                    if (request.method == "DELETE") return MockResponse().setResponseCode(204)
                    if (request.path!!.endsWith("/ending/")) {
                        if (holdEnding && !endingEntered.isDone) { endingEntered.complete(Unit); endingGate.await(20, TimeUnit.SECONDS) }
                        return MockResponse().setHeader("Content-Type", "application/json").setBody("""{"state":"complete"}""")
                    }
                    return MockResponse().setHeader("Content-Type", "audio/pcm;rate=16000;channels=1")
                        .setChunkedBody(okio.Buffer().write(samples), 797)
                        .throttleBody((samples.size / 3).toLong(), 1700, TimeUnit.MILLISECONDS)
                }
            }
            server.start()
            val origin = server.url("/").toString().removeSuffix("/")
            shell("pm grant ${context.packageName} android.permission.POST_NOTIFICATIONS")
            shell("cmd deviceidle whitelist +${context.packageName}")
            activity = ActivityScenario.launch(MainActivity::class.java)
            binding = bindService(context)
            activity.onActivity {
                binding.binder.configure(TtsActivationSource(instance, "Integration fixture", origin) { _, callback -> callback(Result.success(emptyList())) })
                assertTrue(binding.binder.dispatch(TtsCommand.AttachDocument(owner)).accepted)
            }
        }
        fun manual(operation: String) = instrumentation.runOnMainSync {
            assertTrue(binding.binder.dispatch(TtsCommand.Manual(owner, operation, mapOf("kind" to "text", "source" to "test",
                "text" to "Three real tones", "speech" to mapOf("text" to "Three real tones")))).accepted)
        }
        fun snapshot(): NativeTtsSnapshot {
            var value: NativeTtsSnapshot? = null
            instrumentation.runOnMainSync { value = TtsService.cachedSnapshot(instance) }
            return value!!
        }
        fun await(message: String, timeout: Long = 10000, condition: () -> Boolean) {
            val end = android.os.SystemClock.elapsedRealtime() + timeout
            while (!condition() && android.os.SystemClock.elapsedRealtime() < end) Thread.sleep(25)
            assertTrue("$message: ${snapshot()}", condition())
        }
        fun dismiss() {
            if (bound) { context.unbindService(binding.connection); bound = false }
            activity.onActivity { it.finishAndRemoveTask() }
            activity.close()
        }
        fun notification() = context.getSystemService(android.app.NotificationManager::class.java)
            .activeNotifications.single { it.id == TtsNotification.ID }.notification
        fun candidate(eventId: Int = 1) {
            assertTrue(socket!!.send(JSONObject().put("protocol", 1).put("type", "candidate").put("activation_id", activation)
                .put("event_id", eventId).put("text", "Eligible event after silent waiting").toString()))
        }
        fun assertReleasedOnce() {
            fun releases() = requests.filter { it.method == "DELETE" && it.path!!.startsWith("/api/tts/native/") }
            await("One DELETE per opened audio") { releases().size == frames.count { it.optString("type") in setOf("manual", "select") } }
            assertEquals(releases().map { it.path }.toSet().size, releases().size)
        }
        override fun close() {
            endingGate.countDown(); resumeGate.countDown(); clientGate.countDown()
            context.stopService(Intent(context, TtsService::class.java))
            if (bound) context.unbindService(binding.connection)
            activity.close()
            shell("cmd deviceidle unforce")
            shell("dumpsys battery reset")
            shell("input keyevent KEYCODE_WAKEUP")
            shell("wm dismiss-keyguard")
            shell("cmd deviceidle whitelist -${context.packageName}")
            server.shutdown()
        }
    }

    /** Run the actual JS adapters through each real engine, common bridge, and service binder. */
    private inner class BrowserClientFixture(val fixture: ServiceFixture, val holdCookies: Boolean = false,
        val holdOldStop: Boolean = false) : AutoCloseable {
        lateinit var page: BrowserSession
        lateinit var bridge: com.twidi.twicc.AndroidAppBridge
        var document = fixture.owner
        val accepted = CompletableFuture<JSONObject>()
        val oldStop = CompletableFuture<String>()
        val cookies = CompletableFuture<() -> Unit>()
        val tokens = mutableListOf<String>()
        init {
            prepareGeckoTestProfile()
            val address = fixture.server.url("/").toString().removeSuffix("/")
            fixture.activity.onActivity { activity ->
                val native = NativeTtsBridge({ page.nativeTtsSupported }, { document }, { fixture.binding.binder.snapshot() },
                    { page.nativeTtsDocumentToken }) { command ->
                    fixture.binding.binder.configure(TtsActivationSource(fixture.instance, "Real page fixture", address) { urls, callback ->
                        page.exportTtsCookies(urls) { result ->
                            if (holdCookies) cookies.complete { callback(result) } else callback(result)
                        }
                    })
                    fixture.binding.binder.dispatch(command)
                }
                bridge = com.twidi.twicc.AndroidAppBridge({ JSONObject().put("capabilities", JSONObject().put("nativeTts", 1)) },
                    { _, _ -> }, { _, _ -> null }, native::receive)
                page = EngineProvider.engine.createSession(activity, Instance(fixture.instance, "Real page fixture", address), object : BrowserEvents {
                    override fun onNavigationRequest(request: NavigationRequest) = NavigationDecision.ALLOW
                    override fun onPageStarted(url: String) {
                        document = DocumentOwner(fixture.instance, UUID.randomUUID().toString(), TtsService.nextDocumentGeneration())
                        fixture.binding.binder.dispatch(TtsCommand.AttachDocument(document))
                    }
                    override fun onLocationChanged(url: String) {}
                    override fun onProgressChanged(progress: Int) {}
                    override fun onPageFinished() {}
                    override fun onHistoryChanged(canGoBack: Boolean) {}
                    override fun onFailure(message: com.twidi.twicc.i18n.UiMessage) { accepted.completeExceptionally(AssertionError(message)); oldStop.completeExceptionally(AssertionError(message)) }
                    override fun onRendererStopped(crashed: Boolean) { onFailure(com.twidi.twicc.i18n.UiMessage("android.browser.errors.rendererStopped")) }
                    override fun onDownload(source: DownloadSource) {}
                    override fun onAppMessage(message: String): String {
                        val request = JSONObject(message)
                        if (holdOldStop && request.optString("command") == "tts.stop_all" && !oldStop.isDone) {
                            oldStop.complete(message)
                            return JSONObject().put("protocol", 1).put("id", request.getString("id")).put("ok", true)
                                .put("state", JSONObject().put("accepted", false)).toString()
                        }
                        val reply = bridge.receive(message)
                        if (request.optString("command") == "tts.attach") page.nativeTtsDocumentToken?.let { tokens += it }
                        if (request.optString("command") == "tts.manual") accepted.complete(JSONObject(reply).getJSONObject("state"))
                        return reply
                    }
                }, NativeRequests(activity))
                activity.setContentView(page.view)
                page.load("$address/client")
            }
            allowClientStart()
        }
        fun allowClientStart() {
            fixture.await("Real document and credential principal initialize", 30000) {
                var ready = false
                fixture.instrumentation.runOnMainSync {
                    val token = page.nativeTtsDocumentToken
                    ready = token != null
                    if (ready && EngineProvider.engine.displayName.startsWith("Gecko")) {
                        val exports = Class.forName("com.twidi.twicc.engines.gecko.TtsExports")
                        val documents = exports.getDeclaredField("documents").apply { isAccessible = true }.get(null) as Map<*, *>
                        val current = documents[token]
                        ready = current != null && current.javaClass.getDeclaredField("storeId").apply { isAccessible = true }.get(current) != null
                    }
                }
                ready
            }
            fixture.clientGate.countDown()
        }
        override fun close() { fixture.instrumentation.runOnMainSync { page.close() } }
    }

    @androidx.test.filters.MediumTest
    @Test fun realPageTemporarySummarySurvivesSourceReleaseCookieWaitAndActivityClose() {
        ServiceFixture().use { fixture ->
            fixture.holdManualAudio = true
            fixture.pageScript = """
                const captured = {kind:'summary',source:'summary',text:'Frozen selection',identity:null,config_id:null,
                    speech:{text:'',config_id:null},summary:{source:{text:'Frozen temporary source',purpose:'selection'},requested_model:'luna'}};
                native.manual(captured);
                captured.summary.source.text = 'Changed page source';
                await fetch('/fixture/page-source', {method:'DELETE'});
            """.trimIndent()
            BrowserClientFixture(fixture, holdCookies = true).use { browser ->
                assertTrue(browser.accepted.get(30, TimeUnit.SECONDS).getBoolean("accepted"))
                val deliver = browser.cookies.get(10, TimeUnit.SECONDS)
                fixture.await("Page releases its own source before native cookie completion") {
                    fixture.requests.any { it.path == "/fixture/page-source" && it.method == "DELETE" }
                }
                assertFalse(fixture.requests.any { it.path == "/ws/tts/" })
                browser.close(); fixture.dismiss()
                fixture.instrumentation.runOnMainSync { deliver() }
                val manual = fixture.manualFrame.get(10, TimeUnit.SECONDS)
                val summary = manual.getJSONObject("request").getJSONObject("summary")
                assertEquals("Frozen temporary source", summary.getJSONObject("source").getString("text"))
                assertEquals("luna", summary.getString("requested_model"))
                assertFalse(summary.has("handle_id"))
                assertTrue(fixture.requests.single { it.path == "/ws/tts/" }.getHeader("Cookie").orEmpty().contains("fixture-auth=httponly"))
                // Generation finishes after page and Activity destruction; the native owner alone receives audio.
                fixture.deliverAudio(manual)
                fixture.await("Accepted summary drains after page destruction", 15000) { fixture.snapshot().results.any { it.ended["ok"] == true } }
                assertEquals(1, fixture.frames.count { it.optString("type") == "started" })
                fixture.assertReleasedOnce()
            }
        }
    }

    @androidx.test.filters.MediumTest
    @Test fun sameUrlReloadRejectsDelayedRealDocumentIngressWithoutStoppingListening() {
        ServiceFixture().use { fixture ->
            fixture.pageScript = "await app.request('tts.stop_all');"
            BrowserClientFixture(fixture, holdOldStop = true).use { browser ->
                val delayed = browser.oldStop.get(30, TimeUnit.SECONDS)
                val oldToken = JSONObject(delayed).getString("documentToken")
                fixture.pageScript = """
                    const accepted = await native.listen({pageId:'stable-timeline-page',scope:{kind:'global',id:null,label:'All events'},
                        includeMuted:true,boundary:'2026-11-05T10:00:00.000000Z'});
                    await fetch('/fixture/reloaded?accepted='+accepted.accepted);
                """.trimIndent()
                fixture.clientGate = java.util.concurrent.CountDownLatch(1)
                val reloadUrl = fixture.server.url("/client").toString()
                fixture.activity.onActivity { browser.page.load(reloadUrl, bypassCache = true) }
                fixture.await("Reload invalidates the old committed token") {
                    var changed = false
                    fixture.instrumentation.runOnMainSync { changed = browser.page.nativeTtsDocumentToken != oldToken }
                    changed
                }
                browser.allowClientStart()
                fixture.await("New same-URL document attaches and listens", 30000) { fixture.requests.any { it.path == "/fixture/reloaded?accepted=true" } }
                fixture.activity.onActivity {
                    assertNotEquals(oldToken, browser.page.nativeTtsDocumentToken)
                    val reply = JSONObject(browser.bridge.receive(delayed)).getJSONObject("state")
                    assertFalse(reply.getBoolean("accepted"))
                    assertEquals("stale_document", reply.getJSONObject("error").getString("code"))
                    assertFalse(reply.has("snapshot")); assertFalse(reply.has("documentToken"))
                    val value = fixture.binding.binder.snapshot().listening!!
                    assertEquals("stable-timeline-page", value.pageId); assertTrue(value.includeMuted)
                }
                assertEquals(0, fixture.frames.count { it.optString("type") == "stop_all" })
                assertEquals(2, browser.tokens.distinct().size)
                fixture.instrumentation.runOnMainSync { fixture.binding.binder.dispatch(TtsCommand.StopAll) }
            }
        }
    }

    @androidx.test.filters.MediumTest
    @Test fun notificationTimelinePauseAllowsManualAudioAndResumesWithFreshBoundary() {
        ServiceFixture().use { fixture ->
            fixture.activity.onActivity {
                assertTrue(fixture.binding.binder.dispatch(TtsCommand.Listen(fixture.owner,
                    ListeningScope("project", "project-one", "One"), true,
                    "2026-11-05T10:00:00.000000Z", "project-one")).accepted)
            }
            fixture.await("Listening connects") { fixture.activation != null }
            val originalActivation = fixture.activation!!
            fixture.candidate()
            fixture.await("Automatic reading starts") { fixture.frames.any { it.optString("type") == "started" } }
            fixture.dismiss()
            fixture.await("Pause control reaches the real notification") { fixture.notification().actions.size == 3 }
            assertEquals(listOf("Skip", "Pause timeline", "Stop all"), fixture.notification().actions.map { it.title.toString() })
            val oldPause = fixture.notification().actions[1].actionIntent
            oldPause.send()
            fixture.await("Pause cancels the reading and remains enabled") {
                fixture.snapshot().listening?.paused == true && fixture.snapshot().playback.readingId == null
            }
            fixture.await("Paused controls replace active controls") {
                fixture.notification().actions.map { it.title.toString() } == listOf("Resume timeline", "Stop all")
            }
            assertTrue(currentWakeLocks().contains("TwiCC:NativeTts"))
            val selects = fixture.frames.count { it.optString("type") == "select" }
            val revision = fixture.snapshot().revision
            fixture.candidate(2)
            fixture.await("Paused candidate is processed") { fixture.snapshot().revision > revision }
            assertEquals(selects, fixture.frames.count { it.optString("type") == "select" })
            fixture.manual("manual-during-pause")
            fixture.await("Manual audio starts while timeline stays paused") {
                fixture.snapshot().playback.started && !fixture.snapshot().playback.automatic
            }
            assertTrue(fixture.snapshot().listening!!.paused)
            fixture.await("Manual audio completes without removing pause", 15000) {
                fixture.snapshot().results.any { it.operationId == "manual-during-pause" && it.ended["ok"] == true }
            }
            val oldResume = fixture.notification().actions.first { it.title == "Resume timeline" }.actionIntent
            val beforeResume = java.time.Instant.now()
            oldResume.send()
            fixture.await("Resume creates a new backend listener") { fixture.activation != originalActivation }
            val afterResume = java.time.Instant.now()
            val listening = fixture.snapshot().listening!!
            assertFalse(listening.paused)
            assertEquals(ListeningScope("project", "project-one", "One"), listening.scope)
            assertTrue(listening.includeMuted)
            assertTrue(Regex("\\d{4}-\\d{2}-\\d{2}T\\d{2}:\\d{2}:\\d{2}\\.\\d{6}Z").matches(listening.boundary))
            val boundary = java.time.Instant.parse(listening.boundary)
            assertFalse(boundary.isBefore(beforeResume)); assertFalse(boundary.isAfter(afterResume))
            oldPause.send(); oldResume.send()
            fixture.instrumentation.waitForIdleSync()
            assertEquals(listening, fixture.snapshot().listening)
            fixture.candidate(3)
            fixture.await("Only a fresh event starts after Resume", 15000) {
                fixture.frames.count { it.optString("type") == "select" } == selects + 1 && fixture.snapshot().playback.started
            }
            fixture.await("New Pause control is visible") { fixture.notification().actions.any { it.title == "Pause timeline" } }
            fixture.notification().actions.first { it.title == "Pause timeline" }.actionIntent.send()
            fixture.await("Paused again") { fixture.snapshot().listening?.paused == true }
            fixture.notification().actions.last().actionIntent.send()
            fixture.await("Stop all removes paused notification") {
                fixture.context.getSystemService(android.app.NotificationManager::class.java).activeNotifications.none { it.id == TtsNotification.ID }
            }
            assertNull(fixture.snapshot().listening)
            assertFalse(currentWakeLocks().contains("TwiCC:NativeTts"))
        }
    }

    @androidx.test.filters.MediumTest
    @Test fun offlineNotificationSkipReconcilesBeforeLaterEligibleAudioWithoutReplay() {
        ServiceFixture().use { fixture ->
            fixture.activity.onActivity {
                assertTrue(fixture.binding.binder.dispatch(TtsCommand.Listen(fixture.owner, ListeningScope("global", null), false,
                    "2026-11-05T10:00:00.000000Z")).accepted)
            }
            fixture.await("Listening connects") { fixture.activation != null }
            fixture.candidate()
            val old = fixture.manualFrame.get(10, TimeUnit.SECONDS)
            fixture.await("First automatic reading starts") { fixture.frames.any { it.optString("type") == "started" } }
            fixture.dismiss()
            fixture.holdResume = true
            assertTrue(fixture.socket!!.close(1013, "Fixture outage"))
            fixture.await("Socket loss reaches native owner") { fixture.snapshot().sessionStatus == "reconnecting" }
            fixture.notification().actions.first { it.title == "Skip" }.actionIntent.send()
            fixture.await("Offline Skip cancels locally without disabling listening") {
                fixture.snapshot().playback.readingId == null && fixture.snapshot().listening != null
            }
            val resumed = fixture.resumeFrame.get(10, TimeUnit.SECONDS)
            val replay = resumed.getJSONArray("commands")
            assertTrue((0 until replay.length()).any { replay.getJSONObject(it).optString("type") == "skip" })
            // A delayed old response arrives before resume's state acknowledgement.
            fixture.deliverAudio(old)
            fixture.resumeGate.countDown()
            fixture.await("Retained cancellation reconciles") { fixture.snapshot().sessionStatus == "connected" }
            fixture.candidate(2)
            fixture.await("Later event starts once", 15000) { fixture.frames.count { it.optString("type") == "started" } == 2 }
            fixture.await("Only later event completes", 15000) { fixture.frames.any { it.optString("type") == "finished" } }
            assertEquals(2, fixture.requests.count { it.method == "GET" && it.path!!.contains("/audio/") && !it.path!!.endsWith("/ending/") })
            assertEquals(1, fixture.frames.count { it.optString("type") == "finished" })
            fixture.assertReleasedOnce()
            fixture.notification().actions.last().actionIntent.send()
            fixture.await("Stop all clears listener") { fixture.snapshot().listening == null }
        }
    }

    @androidx.test.filters.MediumTest
    @Test fun notificationStopAllWhileRealEndingIsBlockedDoesNotWaitForEnding() {
        ServiceFixture().use { fixture ->
            fixture.holdEnding = true
            fixture.manual("blocked-ending")
            fixture.endingEntered.get(15, TimeUnit.SECONDS)
            fixture.dismiss()
            val start = android.os.SystemClock.elapsedRealtime()
            fixture.notification().actions.last().actionIntent.send()
            fixture.await("Stop all releases foreground despite blocked ending", 5000) {
                fixture.context.getSystemService(android.app.NotificationManager::class.java).activeNotifications.none { it.id == TtsNotification.ID }
            }
            assertTrue(android.os.SystemClock.elapsedRealtime() - start < 5000)
            assertFalse(currentWakeLocks().contains("TwiCC:NativeTts"))
            assertEquals(true, fixture.snapshot().results.single { it.operationId == "blocked-ending" }.ended["aborted"])
            fixture.endingGate.countDown()
            fixture.assertReleasedOnce()
            assertEquals(0, fixture.frames.count { it.optString("type") == "finished" })
        }
    }

    @androidx.test.filters.MediumTest
    @Test fun realServiceOwnsPlayerEndingReleaseAndShutdownWithoutActivity() {
        ServiceFixture().use { fixture ->
            fixture.manual("complete")
            fixture.dismiss()
            fixture.await("Real AudioTrack must start") { fixture.frames.any { it.optString("type") == "started" } }
            fixture.await("Real player must drain", 15000) { fixture.snapshot().results.any { it.operationId == "complete" } }
            val result = fixture.snapshot().results.single { it.operationId == "complete" }
            assertTrue(result.started); assertEquals(true, result.ended["ok"])
            fixture.assertReleasedOnce()
            fixture.await("Finished acknowledgement must stop foreground") {
                fixture.context.getSystemService(android.app.NotificationManager::class.java).activeNotifications.none { it.id == TtsNotification.ID }
            }
            assertEquals(1, fixture.frames.count { it.optString("type") == "started" })
            assertEquals(1, fixture.frames.count { it.optString("type") == "finished" })
            assertFalse(currentWakeLocks().contains("TwiCC:NativeTts"))
        }
    }

    @androidx.test.filters.MediumTest
    @Test fun replacementDuringRealPlayerPendingEndingCompletesOnlyReplacement() {
        ServiceFixture().use { fixture ->
            fixture.holdEnding = true
            fixture.manual("old")
            fixture.endingEntered.get(15, TimeUnit.SECONDS)
            fixture.manual("replacement")
            fixture.await("Replacement starts while old ending remains blocked", 10000) {
                fixture.frames.count { it.optString("type") == "started" } == 2
            }
            fixture.endingGate.countDown()
            fixture.dismiss()
            fixture.await("Replacement completes", 15000) { fixture.snapshot().results.any { it.operationId == "replacement" } }
            val results = fixture.snapshot().results
            assertEquals(true, results.single { it.operationId == "old" }.ended["aborted"])
            assertEquals(true, results.single { it.operationId == "replacement" }.ended["ok"])
            assertEquals(1, fixture.frames.count { it.optString("type") == "finished" })
            fixture.assertReleasedOnce()
        }
    }

    @androidx.test.filters.LargeTest
    @Test fun screenOffArrivalAfterThirtySilentMinutes() {
        assumeTrue("Explicit long emulator diagnostic", InstrumentationRegistry.getArguments().getString("longScreenOff") == "true")
        ServiceFixture().use { fixture ->
            fixture.activity.onActivity {
                assertTrue(fixture.binding.binder.dispatch(TtsCommand.Listen(fixture.owner, ListeningScope("global", null), false,
                    "2026-11-05T10:00:00.000000Z")).accepted)
            }
            fixture.await("Listening reaches actual WSS") { fixture.activation != null }
            fixture.dismiss()
            shell("cmd deviceidle enable")
            shell("dumpsys battery unplug")
            shell("input keyevent KEYCODE_SLEEP")
            fixture.await("Display becomes asleep before forced Doze") { shell("dumpsys power").contains("mWakefulness=Asleep") }
            val forced = shell("cmd deviceidle force-idle")
            assertTrue(forced, forced.contains("Now forced in to deep idle mode"))
            assertEquals("IDLE", shell("cmd deviceidle get deep").trim())
            val start = android.os.SystemClock.elapsedRealtime()
            android.util.Log.i("TtsScreenOffEvidence", "START utcMillis=${System.currentTimeMillis()} elapsedRealtimeMillis=$start engine=${EngineProvider.engine.displayName} screen=${shell("dumpsys power").contains("mWakefulness=Asleep")} state=${shell("cmd deviceidle get deep").trim()} wakeLock=${currentWakeLocks().contains("TwiCC:NativeTts")}")
            val end = start + 30 * 60 * 1000L
            while (android.os.SystemClock.elapsedRealtime() < end) {
                Thread.sleep(1000)
                assertTrue("No silent audio stream", fixture.requests.none { it.path!!.contains("/audio/") })
                assertNotNull(fixture.snapshot().listening)
            }
            val silentWaitMillis = android.os.SystemClock.elapsedRealtime() - start
            assertTrue(silentWaitMillis >= 30 * 60 * 1000L)
            assertTrue(shell("dumpsys deviceidle").contains("mState=IDLE"))
            assertTrue(currentWakeLocks().contains("TwiCC:NativeTts"))
            assertEquals(listOf("Pause timeline", "Stop all"), fixture.notification().actions.map { it.title.toString() })
            fixture.candidate()
            fixture.await("New event starts under forced Doze", 15000) { fixture.frames.any { it.optString("type") == "started" } }
            fixture.await("New event drains under forced Doze", 15000) { fixture.frames.any { it.optString("type") == "finished" } }
            val newEventStartedCount = fixture.frames.count { it.optString("type") == "started" }
            @Suppress("DEPRECATION")
            val activityWasReopened = fixture.context.getSystemService(ActivityManager::class.java).appTasks.any {
                it.taskInfo?.topActivity?.packageName == fixture.context.packageName
            }
            assertEquals(1, newEventStartedCount)
            assertFalse(activityWasReopened)
            fixture.assertReleasedOnce()
            android.util.Log.i("TtsScreenOffEvidence", "END utcMillis=${System.currentTimeMillis()} elapsedRealtimeMillis=${android.os.SystemClock.elapsedRealtime()} silentWaitMillis=$silentWaitMillis newEventStartedCount=$newEventStartedCount activityWasReopened=$activityWasReopened state=${shell("cmd deviceidle get deep").trim()} wakeLock=${currentWakeLocks().contains("TwiCC:NativeTts")}")
            fixture.notification().actions.last().actionIntent.send()
            fixture.await("Notification Stop all without Activity releases foreground") {
                fixture.context.getSystemService(android.app.NotificationManager::class.java).activeNotifications.none { it.id == TtsNotification.ID }
            }
            assertFalse(currentWakeLocks().contains("TwiCC:NativeTts"))
        }
    }

    @Test fun visibleRuntimePermissionDenialOpensRealNotificationSettings() {
        val instrumentation = InstrumentationRegistry.getInstrumentation()
        val context = instrumentation.targetContext
        assertEquals(android.content.pm.PackageManager.PERMISSION_DENIED,
            context.checkSelfPermission(android.Manifest.permission.POST_NOTIFICATIONS))
        context.getSharedPreferences("tts_platform", Context.MODE_PRIVATE).edit().clear().commit()
        val activity = ActivityScenario.launch(MainActivity::class.java)
        try {
            awaitActivityFocus(activity)
            activity.onActivity {
                val error = TtsNotification.visiblePreflight(it)!!
                assertEquals("permission_required", error.code)
            }
            clickVisibleNode(viewId = "com.android.permissioncontroller:id/permission_deny_button")
            awaitActivityFocus(activity)
            assertEquals(android.content.pm.PackageManager.PERMISSION_DENIED,
                context.checkSelfPermission(android.Manifest.permission.POST_NOTIFICATIONS))
            activity.onActivity {
                val error = TtsNotification.visiblePreflight(it)!!
                assertEquals("notification_settings", error.action)
            }
            clickVisibleNode(text = "Open Settings")
            val end = android.os.SystemClock.elapsedRealtime() + 5000
            var settings = ""
            while (!settings.contains("com.android.settings") && android.os.SystemClock.elapsedRealtime() < end) {
                settings = instrumentation.uiAutomation.rootInActiveWindow?.packageName?.toString().orEmpty()
                Thread.sleep(50)
            }
            assertEquals("com.android.settings", settings)
            assertTrue(shell("dumpsys activity activities").contains("android.settings.CHANNEL_NOTIFICATION_SETTINGS"))
            assertTrue(context.getSystemService(android.app.NotificationManager::class.java).activeNotifications.none { it.id == TtsNotification.ID })
            assertFalse(currentWakeLocks().contains("TwiCC:NativeTts"))
            shell("input keyevent KEYCODE_BACK")
            shell("pm grant ${context.packageName} android.permission.POST_NOTIFICATIONS")
            shell("appops set --uid ${context.packageName} POST_NOTIFICATION allow")
            awaitActivityFocus(activity)
            activity.onActivity { assertNull(TtsNotification.visiblePreflight(it)) }
            assertTrue("Settings return does not activate speech", context.getSystemService(android.app.NotificationManager::class.java)
                .activeNotifications.none { it.id == TtsNotification.ID })
        } finally { activity.close() }
    }

    @androidx.test.filters.MediumTest
    @Test fun batteryAdviceOpensRealSettingsWithoutActivatingOrRememberingConsent() {
        val instrumentation = InstrumentationRegistry.getInstrumentation()
        val context = instrumentation.targetContext
        val preferences = context.getSharedPreferences("tts_platform", Context.MODE_PRIVATE)
        preferences.edit().remove("battery_advice_acknowledged").commit()
        shell("pm grant ${context.packageName} android.permission.POST_NOTIFICATIONS")
        shell("cmd deviceidle whitelist -${context.packageName}")
        val activity = ActivityScenario.launch(MainActivity::class.java)
        var continued = false
        try {
            awaitActivityFocus(activity)
            activity.onActivity {
                assertNull(TtsNotification.visiblePreflight(it))
                assertTrue(TtsNotification.batteryAdviceNeeded(it))
                TtsNotification.showBatteryAdvice(it) { continued = true }
            }
            clickVisibleNode(text = "Open Settings")
            val end = android.os.SystemClock.elapsedRealtime() + 5000
            while (instrumentation.uiAutomation.rootInActiveWindow?.packageName != "com.android.settings" && android.os.SystemClock.elapsedRealtime() < end) Thread.sleep(50)
            assertEquals("com.android.settings", instrumentation.uiAutomation.rootInActiveWindow?.packageName)
            assertTrue(shell("dumpsys activity activities").contains("android.settings.IGNORE_BATTERY_OPTIMIZATION_SETTINGS"))
            assertFalse(continued)
            assertTrue(context.getSystemService(android.app.NotificationManager::class.java).activeNotifications.none { it.id == TtsNotification.ID })
            assertFalse(currentWakeLocks().contains("TwiCC:NativeTts"))
            shell("input keyevent KEYCODE_BACK")
            awaitActivityFocus(activity)
            activity.onActivity { assertTrue(TtsNotification.batteryAdviceNeeded(it)) }
            shell("cmd deviceidle whitelist +${context.packageName}")
            activity.onActivity { assertFalse(TtsNotification.batteryAdviceNeeded(it)) }
            assertFalse(continued)
        } finally {
            shell("cmd deviceidle whitelist -${context.packageName}")
            preferences.edit().remove("battery_advice_acknowledged").commit()
            activity.close()
        }
    }

    @androidx.test.filters.MediumTest
    @Test fun batteryAdviceContinuePersistsButDismissalDoesNot() {
        val instrumentation = InstrumentationRegistry.getInstrumentation()
        val context = instrumentation.targetContext
        val preferences = context.getSharedPreferences("tts_platform", Context.MODE_PRIVATE)
        preferences.edit().remove("battery_advice_acknowledged").commit()
        shell("cmd deviceidle whitelist -${context.packageName}")
        val activity = ActivityScenario.launch(MainActivity::class.java)
        var confirmations = 0
        try {
            awaitActivityFocus(activity)
            activity.onActivity { TtsNotification.showBatteryAdvice(it) { confirmations++ } }
            awaitVisibleNode(text = "Continue without changes")
            shell("input keyevent KEYCODE_BACK")
            awaitActivityFocus(activity)
            activity.onActivity {
                assertEquals(0, confirmations)
                assertTrue(TtsNotification.batteryAdviceNeeded(it))
                TtsNotification.showBatteryAdvice(it) { confirmations++ }
            }
            clickVisibleNode(text = "Continue without changes")
            awaitActivityFocus(activity)
            activity.onActivity {
                assertEquals(1, confirmations)
                assertFalse(TtsNotification.batteryAdviceNeeded(it))
            }
            activity.recreate()
            awaitActivityFocus(activity)
            activity.onActivity { assertFalse(TtsNotification.batteryAdviceNeeded(it)) }
            assertFalse(context.getSystemService(android.os.PowerManager::class.java).isIgnoringBatteryOptimizations(context.packageName))
        } finally {
            preferences.edit().remove("battery_advice_acknowledged").commit()
            activity.close()
        }
    }

    @androidx.test.filters.MediumTest
    @Test fun browserBatteryAdviceContinueStartsListeningWithoutExemption() {
        prepareGeckoTestProfile()
        ServiceFixture().use { fixture ->
            val context = fixture.context
            val preferences = context.getSharedPreferences("tts_platform", Context.MODE_PRIVATE)
            preferences.edit().remove("battery_advice_acknowledged").commit()
            shell("cmd deviceidle whitelist -${context.packageName}")
            fixture.clientGate.countDown()
            val store = com.twidi.twicc.InstanceStore(context)
            val saved = store.save(null, "Battery advice fixture", fixture.server.url("/").toString())
            store.rememberPage(saved, fixture.server.url("/client").toString())
            val activity = ActivityScenario.launch<com.twidi.twicc.BrowserActivity>(
                Intent(context, com.twidi.twicc.BrowserActivity::class.java).putExtra("instance", saved.id))
            try {
                fixture.await("Real browser attaches the native document", timeout = 20000) {
                    fixture.requests.any { it.path == "/fixture/client-start" }
                }
                awaitActivityFocus(activity)
                activity.onActivity {
                    val result = it.dispatchNativeTts(TtsCommand.Listen(it.nativeTtsDocumentOwner(),
                        ListeningScope("global", null), false, "2026-11-05T10:00:00.000000Z", "global"))
                    assertFalse(result.accepted)
                    assertEquals("confirmation_required", result.error!!.code)
                    assertNull(it.nativeTtsSnapshot().listening)
                }
                // Native confirmation must not inherit the bridge RPC's short deadline.
                Thread.sleep(1800)
                assertTrue(context.getSystemService(android.app.NotificationManager::class.java).activeNotifications.none { it.id == TtsNotification.ID })
                clickVisibleNode(text = "Continue without changes")
                awaitActivityFocus(activity)
                fixture.await("Window focus continuation activates the service") {
                    var listening = false
                    activity.onActivity { listening = it.nativeTtsSnapshot().listening != null }
                    listening
                }
                assertTrue(context.getSystemService(android.app.NotificationManager::class.java).activeNotifications.any { it.id == TtsNotification.ID })
                assertTrue(currentWakeLocks().contains("TwiCC:NativeTts"))
                assertFalse(context.getSystemService(android.os.PowerManager::class.java).isIgnoringBatteryOptimizations(context.packageName))
                activity.onActivity {
                    assertFalse(TtsNotification.batteryAdviceNeeded(it))
                    it.dispatchNativeTts(TtsCommand.StopAll)
                }
            } finally {
                activity.close()
                store.remove(saved)
                preferences.edit().remove("battery_advice_acknowledged").commit()
            }
        }
    }

    @androidx.test.filters.MediumTest
    @Test fun browserBatteryAdviceDismissAndBackgroundCannotActivateListening() {
        prepareGeckoTestProfile()
        ServiceFixture().use { fixture ->
            val context = fixture.context
            val preferences = context.getSharedPreferences("tts_platform", Context.MODE_PRIVATE)
            preferences.edit().remove("battery_advice_acknowledged").commit()
            shell("cmd deviceidle whitelist -${context.packageName}")
            fixture.clientGate.countDown()
            val store = com.twidi.twicc.InstanceStore(context)
            val saved = store.save(null, "Battery cancellation fixture", fixture.server.url("/").toString())
            store.rememberPage(saved, fixture.server.url("/client").toString())
            val activity = ActivityScenario.launch<com.twidi.twicc.BrowserActivity>(
                Intent(context, com.twidi.twicc.BrowserActivity::class.java).putExtra("instance", saved.id))
            try {
                fixture.await("Real browser attaches the native document", timeout = 20000) {
                    fixture.requests.any { it.path == "/fixture/client-start" }
                }
                awaitActivityFocus(activity)
                activity.onActivity {
                    it.dispatchNativeTts(TtsCommand.Listen(it.nativeTtsDocumentOwner(), ListeningScope("global", null),
                        false, "2026-11-05T10:00:00.000000Z", "global"))
                }
                awaitVisibleNode(text = "Continue without changes")
                shell("input keyevent KEYCODE_BACK")
                awaitActivityFocus(activity)
                activity.onActivity {
                    assertNull(it.nativeTtsSnapshot().listening)
                    assertTrue(TtsNotification.batteryAdviceNeeded(it))
                    it.dispatchNativeTts(TtsCommand.Listen(it.nativeTtsDocumentOwner(), ListeningScope("global", null),
                        false, "2026-11-05T10:00:00.000000Z", "global"))
                }
                activity.moveToState(androidx.lifecycle.Lifecycle.State.CREATED)
                activity.moveToState(androidx.lifecycle.Lifecycle.State.RESUMED)
                awaitActivityFocus(activity)
                activity.onActivity {
                    assertNull(it.nativeTtsSnapshot().listening)
                    assertTrue(TtsNotification.batteryAdviceNeeded(it))
                }
                assertTrue(context.getSystemService(android.app.NotificationManager::class.java).activeNotifications.none { it.id == TtsNotification.ID })
                assertFalse(currentWakeLocks().contains("TwiCC:NativeTts"))
            } finally {
                activity.close()
                store.remove(saved)
                preferences.edit().remove("battery_advice_acknowledged").commit()
            }
        }
    }

    private fun <T : android.app.Activity> awaitActivityFocus(activity: ActivityScenario<T>) {
        val end = android.os.SystemClock.elapsedRealtime() + 5000
        var focused = false
        while (!focused && android.os.SystemClock.elapsedRealtime() < end) {
            activity.onActivity { focused = it.hasWindowFocus() }
            if (!focused) Thread.sleep(25)
        }
        assertTrue("Visible preflight requires real window focus", focused)
    }
    private fun clickVisibleNode(viewId: String? = null, text: String? = null) {
        val node = awaitVisibleNode(viewId, text)
        assertTrue(node.performAction(android.view.accessibility.AccessibilityNodeInfo.ACTION_CLICK))
    }
    private fun awaitVisibleNode(viewId: String? = null, text: String? = null): android.view.accessibility.AccessibilityNodeInfo {
        val automation = InstrumentationRegistry.getInstrumentation().uiAutomation
        val end = android.os.SystemClock.elapsedRealtime() + 5000
        while (android.os.SystemClock.elapsedRealtime() < end) {
            val root = automation.rootInActiveWindow
            val nodes = if (viewId != null) root?.findAccessibilityNodeInfosByViewId(viewId) else root?.findAccessibilityNodeInfosByText(text)
            val node = nodes?.firstOrNull { it.isClickable }
            if (node != null) return node
            Thread.sleep(50)
        }
        throw AssertionError("Missing visible Android control: ${viewId ?: text}")
    }

    private data class Binding(val binder: TtsService.LocalBinder, val connection: android.content.ServiceConnection)
    private fun bindService(context: Context): Binding {
        val ready = CompletableFuture<TtsService.LocalBinder>()
        val connection = object : android.content.ServiceConnection {
            override fun onServiceConnected(name: android.content.ComponentName, binder: android.os.IBinder) { ready.complete(binder as TtsService.LocalBinder) }
            override fun onServiceDisconnected(name: android.content.ComponentName) {}
        }
        InstrumentationRegistry.getInstrumentation().runOnMainSync {
            context.bindService(Intent(context, TtsService::class.java), connection, Context.BIND_AUTO_CREATE)
        }
        return Binding(ready.get(10, TimeUnit.SECONDS), connection)
    }
    private fun currentWakeLocks(): String = shell("dumpsys power").substringAfter("Wake Locks: size=").substringBefore("Suspend Blockers:")
    private fun shell(command: String): String = InstrumentationRegistry.getInstrumentation().uiAutomation
        .executeShellCommand(command).use { android.os.ParcelFileDescriptor.AutoCloseInputStream(it).bufferedReader().readText() }

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
        verifyBrowserBoundary(reloadDuringExport = false)
    }

    @Test fun gecko_export_survives_reload_while_pending() {
        assumeTrue(EngineProvider.engine.displayName.startsWith("Gecko"))
        verifyBrowserBoundary(reloadDuringExport = true)
    }

    private fun verifyBrowserBoundary(reloadDuringExport: Boolean) {
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
        val reloadStarted = CompletableFuture<Unit>()
        var waitingForReload = false
        var replyGate: GeckoReplyGate? = null
        var deliverReply: (() -> Unit)? = null
        try {
            repeat(2) { index ->
                val pageCookies = CompletableFuture<String>()
                activity.onActivity { owner ->
                    val instance = Instance(UUID.randomUUID().toString(), "Test", serverAddress.removeSuffix("/"))
                    val events = testEvents(pageCookies) {
                        if (index == 0 && waitingForReload) reloadStarted.complete(Unit)
                    }
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
                logGeckoStoreDiagnostics()
                if (reloadDuringExport) replyGate = holdNextGeckoExportReply()
                sessions.first().exportTtsCookies(listOf(serverAddress + "api/tts/", serverAddress + "ws/tts/")) {
                    it.fold(nativeCookies::complete, nativeCookies::completeExceptionally)
                }
                assertFalse("Export must still be pending when the Activity closes", nativeCookies.isDone)
                if (!reloadDuringExport) {
                    sessions.forEach { it.close() }
                    sessions.clear()
                    it.finish()
                }
            }
            if (reloadDuringExport) {
                // Hold a REAL privileged cookie reply, then observe the actual Gecko page-start callback.
                deliverReply = replyGate!!.reply.get(10, TimeUnit.SECONDS)
                activity.onActivity {
                    assertFalse("Export must remain pending before reload", nativeCookies.isDone)
                    waitingForReload = true
                    sessions.first().load(serverAddress + "seed/a", bypassCache = true)
                }
                reloadStarted.get(10, TimeUnit.SECONDS)
                activity.onActivity {
                    assertFalse("Reload must not cancel an accepted cookie export", nativeCookies.isDone)
                    deliverReply!!.invoke()
                    deliverReply = null
                    sessions.forEach { it.close() }
                    sessions.clear()
                    it.finish()
                }
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
            instrumentation.runOnMainSync {
                deliverReply?.invoke()
                replyGate?.restore?.invoke()
                sessions.forEach { it.close() }
            }
            activity.close()
            instrumentation.targetContext.stopService(Intent(instrumentation.targetContext, TtsService::class.java))
            server.shutdown()
        }
    }

    private fun testEvents(pageCookies: CompletableFuture<String>, pageStarted: () -> Unit = {}) = object : BrowserEvents {
        override fun onNavigationRequest(request: NavigationRequest) = NavigationDecision.ALLOW
        override fun onPageStarted(url: String) { pageStarted() }
        override fun onLocationChanged(url: String) {}
        override fun onProgressChanged(progress: Int) {}
        override fun onPageFinished() {}
        override fun onHistoryChanged(canGoBack: Boolean) {}
        override fun onFailure(message: com.twidi.twicc.i18n.UiMessage) { pageCookies.completeExceptionally(AssertionError(message)) }
        override fun onRendererStopped(crashed: Boolean) { pageCookies.completeExceptionally(AssertionError("Renderer stopped")) }
        override fun onAppMessage(message: String): String {
            if (message.startsWith("page-cookie:")) pageCookies.complete(message)
            return "{}"
        }
        override fun onDownload(source: DownloadSource) {}
    }

    private data class GeckoReplyGate(val reply: CompletableFuture<() -> Unit>, val restore: () -> Unit)

    /** Delay only delivery of a real engine reply. Keep this scheduling gate outside production code. */
    private fun holdNextGeckoExportReply(): GeckoReplyGate {
        val owner = Class.forName("com.twidi.twicc.engines.gecko.TtsExports")
        val port = owner.getDeclaredField("port").apply { isAccessible = true }.get(null)!!
        val delegateType = Class.forName("org.mozilla.geckoview.WebExtension\$PortDelegate")
        val delegate = port.javaClass.getDeclaredField("delegate").apply { isAccessible = true }.get(port)
        val reply = CompletableFuture<() -> Unit>()
        val gate = Proxy.newProxyInstance(delegateType.classLoader, arrayOf(delegateType)) { _, method, args ->
            val message = args?.firstOrNull()
            if (method.name == "onPortMessage" && message is JSONObject && message.optString("type") == "exported" && !reply.isDone) {
                reply.complete { method.invoke(delegate, *args) }
                null
            } else method.invoke(delegate, *(args ?: emptyArray()))
        }
        val setDelegate = port.javaClass.getMethod("setDelegate", delegateType)
        setDelegate.invoke(port, gate)
        return GeckoReplyGate(reply) { setDelegate.invoke(port, delegate) }
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

    private fun prepareGeckoTestProfile() = com.twidi.twicc.prepareBrowserTestProfile()

}

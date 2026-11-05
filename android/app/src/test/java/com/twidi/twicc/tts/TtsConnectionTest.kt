package com.twidi.twicc.tts

import java.io.IOException
import java.util.concurrent.LinkedBlockingQueue
import java.util.concurrent.TimeUnit
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okhttp3.tls.HandshakeCertificates
import okhttp3.tls.HeldCertificate
import org.junit.Assert.*
import org.junit.Test

class TtsConnectionTest {
    private fun cookie(name: String, path: String = "/", domain: String = "localhost", expiry: Long? = null) =
        TtsCookie(name, "value", domain, path, true, true, true, expiry)
    private fun fixture(clockMillis: () -> Long = { System.nanoTime() / 1_000_000 },
        test: (MockWebServer, TtsConnection, TtsCredentials) -> Unit) {
        val certificate = HeldCertificate.Builder().addSubjectAlternativeName("localhost").build()
        val server = MockWebServer()
        server.useHttps(HandshakeCertificates.Builder().heldCertificate(certificate).build().sslSocketFactory(), false)
        server.start()
        val trust = HandshakeCertificates.Builder().addTrustedCertificate(certificate.certificate).build()
        val credentials = TtsCredentials("instance", server.url("/").toString(), listOf(cookie("sessionid")))
        val connection = TtsConnection(credentials, clockMillis = clockMillis, random = { 0.5 }) { sslSocketFactory(trust.sslSocketFactory(), trust.trustManager) }
        try { test(server, connection, credentials) } finally { connection.close(); server.shutdown() }
    }
    private fun resource() = AudioResource("instance", "session", "secret", "reading", 1, "audio", "audio/mpeg")
    @Test fun cookiesRespectOriginPathExpiryAndNativeUpdates() = fixture { server, c, _ ->
        val jar = TtsCookieJar(server.url("/"), listOf(cookie("root"), cookie("path", "/api/tts"), cookie("foreign", domain="example.com"), cookie("expired", expiry=1)))
        assertEquals(listOf("path", "root"), jar.loadForRequest(server.url("/api/tts/native/x")).map { it.name })
        assertEquals(listOf("root"), jar.loadForRequest(server.url("/ws/tts/")).map { it.name })
        assertTrue(jar.loadForRequest(server.url("/").newBuilder().port(server.port + 1).build()).isEmpty())
        server.enqueue(MockResponse().addHeader("Set-Cookie", "new=token; Path=/; Secure; HttpOnly"))
        c.client.newCall(c.request(server.url("/").toString())).execute().close()
        assertTrue(c.cookieJar.loadForRequest(server.url("/")).any { it.name == "new" })
        assertThrows(IllegalArgumentException::class.java) { c.request("https://example.com/") }
    }
    @Test fun authenticatedWebsocketSendsResumeBeforeAnyCandidateAndHeartbeatUsesOwner() = fixture { server, c, creds ->
        val received = LinkedBlockingQueue<String>()
        server.enqueue(MockResponse().withWebSocketUpgrade(object: WebSocketListener() {
            override fun onMessage(webSocket: WebSocket, text: String) { received.offer(text) }
        }))
        val events = LinkedBlockingQueue<TtsConnectionEvent>(); c.onEvent = { events.offer(it) }
        val id = c.connect(creds, mapOf("protocol" to 1, "type" to "resume", "seq" to 10, "commands" to emptyList<Any>()))
        val first = TtsJson.decode(received.poll(5, TimeUnit.SECONDS)!!)
        assertEquals("resume", first["type"])
        val upgrade = server.takeRequest(5, TimeUnit.SECONDS)!!
        assertEquals("sessionid=value", upgrade.getHeader("Cookie")); assertEquals(creds.origin.removeSuffix("/"), upgrade.getHeader("Origin"))
        assertEquals("/ws/tts/", upgrade.path)
        c.heartbeat(id)
        assertTrue(events.any { it is TtsConnectionEvent.HeartbeatDue && it.connectionId == id })
        assertTrue(c.send(mapOf("protocol" to 1, "type" to "ping", "seq" to 11)))
        assertEquals("ping", TtsJson.decode(received.poll(5, TimeUnit.SECONDS)!!)["type"])
    }
    @Test fun audioUsesSecretRejectsRedirectAndHtmlAndNeverRetriesStream() = fixture { server, c, _ ->
        server.enqueue(MockResponse().setBody("audio bytes").addHeader("Content-Type", "audio/mpeg"))
        c.openAudio(resource()).use { assertEquals("audio bytes", it.input.readBytes().toString(Charsets.UTF_8)) }
        val audio = server.takeRequest()
        assertEquals("/api/tts/native/session/audio/audio/", audio.path)
        assertEquals("secret", audio.getHeader("X-TwiCC-TTS-Session")); assertEquals("sessionid=value", audio.getHeader("Cookie"))
        server.enqueue(MockResponse().setResponseCode(302).addHeader("Location", "/login"))
        assertThrows(TtsAuthorizationException::class.java) { c.openAudio(resource()) }
        server.enqueue(MockResponse().setBody("<!doctype html><title>Tunnel verification</title>").addHeader("Content-Type", "text/html"))
        assertThrows(TtsAuthorizationException::class.java) { c.openAudio(resource()) }
        assertFalse(c.client.retryOnConnectionFailure); assertEquals(0, c.client.readTimeoutMillis)
        assertEquals(3, server.requestCount)
    }
    @Test fun rawPcmStartingWithLessThanIsAudioAndCutStreamNeverRetries() = fixture { server, c, _ ->
        server.enqueue(MockResponse().setBody("<sample data, not HTML").addHeader("Content-Type", "audio/pcm;rate=24000"))
        c.openAudio(resource()).use { assertEquals("<sample data, not HTML", it.input.readBytes().toString(Charsets.UTF_8)) }
        server.takeRequest()
        server.enqueue(MockResponse().setBody("x".repeat(4096)).addHeader("Content-Type", "audio/mpeg")
            .setSocketPolicy(okhttp3.mockwebserver.SocketPolicy.DISCONNECT_DURING_RESPONSE_BODY))
        c.openAudio(resource()).use { stream ->
            assertEquals(64, stream.input.read(ByteArray(64)))
            assertThrows(IOException::class.java) { stream.input.readBytes() }
        }
        assertEquals(2, server.requestCount)
    }
    @Test fun endingAndReleaseStayOwnedAndRejectHtmlJson() = fixture { server, c, _ ->
        server.enqueue(MockResponse().setBody("{\"state\":\"running\",\"error\":null}").addHeader("Content-Type", "application/json"))
        assertEquals("running", c.readEnding(resource())["state"])
        assertEquals("/api/tts/native/session/audio/audio/ending/", server.takeRequest().path)
        server.enqueue(MockResponse().setResponseCode(204)); c.release(resource())
        assertEquals("DELETE", server.takeRequest().method)
        server.enqueue(MockResponse().setBody("<html>Sign in</html>").addHeader("Content-Type", "text/html"))
        assertThrows(TtsAuthorizationException::class.java) { c.readEnding(resource()) }
        assertThrows(IllegalArgumentException::class.java) { c.openAudio(AudioResource("other", "s", "secret", "r", 1, "a", "audio/mpeg")) }
    }
    @Test fun pendingAudioOpenCancelsBeforeHeadersWithoutClosingControlSocket() = fixture { server, c, _ ->
        server.enqueue(MockResponse().setSocketPolicy(okhttp3.mockwebserver.SocketPolicy.NO_RESPONSE))
        val outcome = LinkedBlockingQueue<Throwable>()
        val worker = Thread { try { c.openAudio(resource()).close() } catch (error: Throwable) { outcome.offer(error) } }
        worker.start(); assertNotNull(server.takeRequest(5, TimeUnit.SECONDS))
        c.cancelAudio(resource())
        assertTrue(outcome.poll(5, TimeUnit.SECONDS) is IOException)
        worker.join(1000); assertFalse(worker.isAlive)
    }
    @Test fun actualSocketFailuresPreserveBackoffUntilOwnerReconciles() = fixture { server, c, creds ->
        val events = LinkedBlockingQueue<TtsConnectionEvent>(); c.onEvent = { events.offer(it) }
        val hello = mapOf("protocol" to 1, "type" to "open", "seq" to 1)
        repeat(2) { attempt ->
            server.enqueue(MockResponse().setResponseCode(503))
            c.connect(creds, hello)
            val lost = events.poll(5, TimeUnit.SECONDS) as TtsConnectionEvent.Lost
            assertEquals(if (attempt == 0) 1000L else 2000L, lost.retryAfterMillis)
        }
        val sockets = LinkedBlockingQueue<WebSocket>()
        fun upgrade() {
            server.enqueue(MockResponse().withWebSocketUpgrade(object : WebSocketListener() {
                override fun onOpen(webSocket: WebSocket, response: Response) { sockets.offer(webSocket) }
            }))
        }
        upgrade(); c.connect(creds, hello)
        assertTrue(events.poll(5, TimeUnit.SECONDS) is TtsConnectionEvent.Opened)
        sockets.poll(5, TimeUnit.SECONDS)!!.close(1000, null)
        assertEquals(4000L, (events.poll(5, TimeUnit.SECONDS) as TtsConnectionEvent.Lost).retryAfterMillis)
        upgrade(); val current = c.connect(creds, hello)
        assertTrue(events.poll(5, TimeUnit.SECONDS) is TtsConnectionEvent.Opened)
        c.reconciled(current)
        sockets.poll(5, TimeUnit.SECONDS)!!.close(1000, null)
        assertEquals(1000L, (events.poll(5, TimeUnit.SECONDS) as TtsConnectionEvent.Lost).retryAfterMillis)
    }
    @Test fun reconnectBackoffResetsOnlyAfterReconciledState() {
        val backoff = TtsBackoff { 0.5 }
        assertEquals(listOf(1000L, 2000L, 4000L, 8000L, 16000L, 30000L, 30000L), (1..7).map { backoff.failed() })
        backoff.reconciled(); assertEquals(1000L, backoff.failed())
    }
    @Test fun missingPongFailsAtFiveSecondsAfterTheActualPing() {
        var now = 0L
        fixture(clockMillis = { now }) { server, c, creds ->
            val received = LinkedBlockingQueue<String>()
            val events = LinkedBlockingQueue<TtsConnectionEvent>(); c.onEvent = { events.offer(it) }
            server.enqueue(MockResponse().withWebSocketUpgrade(object : WebSocketListener() {
                override fun onMessage(webSocket: WebSocket, text: String) { received.offer(text) }
            }))
            val id = c.connect(creds, mapOf("protocol" to 1, "type" to "open", "seq" to 1))
            assertNotNull(received.poll(5, TimeUnit.SECONDS))
            assertTrue(events.poll(5, TimeUnit.SECONDS) is TtsConnectionEvent.Opened)
            // A due callback alone is not a sent heartbeat and cannot start its deadline.
            now = 100_000L; c.heartbeat(id)
            assertTrue(events.poll(5, TimeUnit.SECONDS) is TtsConnectionEvent.HeartbeatDue)
            assertTrue(c.send(mapOf("protocol" to 1, "type" to "ping", "seq" to 2)))
            assertEquals("ping", TtsJson.decode(received.poll(5, TimeUnit.SECONDS)!!)["type"])
            now = 104_999L; c.heartbeat(id)
            assertTrue(events.poll(5, TimeUnit.SECONDS) is TtsConnectionEvent.HeartbeatDue)
            now = 105_000L; c.heartbeat(id)
            assertTrue(events.poll(5, TimeUnit.SECONDS) is TtsConnectionEvent.Lost)
        }
    }
    @Test fun validPongCancelsItsDeadlineButOldPongCannotAcknowledgeTheNextPing() {
        var now = 0L
        fixture(clockMillis = { now }) { server, c, creds ->
            val events = LinkedBlockingQueue<TtsConnectionEvent>(); c.onEvent = { events.offer(it) }
            server.enqueue(MockResponse().withWebSocketUpgrade(object : WebSocketListener() {
                override fun onMessage(webSocket: WebSocket, text: String) {
                    if (TtsJson.decode(text)["type"] == "ping")
                        webSocket.send("{\"protocol\":1,\"type\":\"pong\",\"ack_seq\":2}")
                }
            }))
            val id = c.connect(creds, mapOf("protocol" to 1, "type" to "open", "seq" to 1))
            assertTrue(events.poll(5, TimeUnit.SECONDS) is TtsConnectionEvent.Opened)
            assertTrue(c.send(mapOf("protocol" to 1, "type" to "ping", "seq" to 2)))
            assertTrue(events.poll(5, TimeUnit.SECONDS) is TtsConnectionEvent.Frame)
            now = 5000L; c.heartbeat(id)
            assertTrue(events.poll(5, TimeUnit.SECONDS) is TtsConnectionEvent.HeartbeatDue)
            assertTrue(c.send(mapOf("protocol" to 1, "type" to "ping", "seq" to 3)))
            assertTrue(events.poll(5, TimeUnit.SECONDS) is TtsConnectionEvent.Frame)
            now = 9999L; c.heartbeat(id)
            assertTrue(events.poll(5, TimeUnit.SECONDS) is TtsConnectionEvent.HeartbeatDue)
            now = 10000L; c.heartbeat(id)
            assertTrue(events.poll(5, TimeUnit.SECONDS) is TtsConnectionEvent.Lost)
        }
    }
    @Test fun retryJitterStaysWithinTwentyPercentOfEachCappedBase() {
        val low = TtsBackoff { 0.0 }; val high = TtsBackoff { 1.0 }
        assertEquals(listOf(800L,1600L,3200L,6400L,12800L,24000L,24000L), (1..7).map { low.failed() })
        assertEquals(listOf(1200L,2400L,4800L,9600L,19200L,36000L,36000L), (1..7).map { high.failed() })
        high.reconciled(); assertEquals(1200L, high.failed())
    }

}

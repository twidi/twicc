package com.twidi.twicc.tts

import android.content.Context
import android.media.AudioFocusRequest
import android.media.AudioManager
import androidx.media3.common.C
import androidx.media3.common.audio.AudioProcessor
import androidx.media3.common.audio.BaseAudioProcessor
import androidx.media3.common.util.UnstableApi
import androidx.media3.exoplayer.DefaultRenderersFactory
import androidx.media3.exoplayer.audio.AudioSink
import androidx.media3.exoplayer.audio.DefaultAudioSink
import androidx.test.core.app.ActivityScenario
import com.twidi.twicc.MainActivity
import androidx.test.core.app.ApplicationProvider
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import java.io.ByteArrayOutputStream
import java.nio.ByteBuffer
import java.util.concurrent.CopyOnWriteArrayList
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicInteger
import okhttp3.Protocol
import okhttp3.mockwebserver.Dispatcher
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okhttp3.mockwebserver.RecordedRequest
import okhttp3.mockwebserver.SocketPolicy
import okhttp3.tls.HandshakeCertificates
import okhttp3.tls.HeldCertificate
import okio.Buffer
import org.junit.Assert.*
import org.junit.Test
import org.junit.Before
import org.junit.After
import org.junit.runner.RunWith

@UnstableApi
@RunWith(AndroidJUnit4::class)
class TtsPlaybackTest {
    private lateinit var activity: ActivityScenario<MainActivity>
    @Before fun visibleActivityPermitsAndroid15AudioFocus() { activity = ActivityScenario.launch(MainActivity::class.java) }
    @After fun closeActivity() { activity.close() }
    private val instrumentation get() = InstrumentationRegistry.getInstrumentation()
    private val context get() = ApplicationProvider.getApplicationContext<Context>()
    private fun main(block: () -> Unit) = instrumentation.runOnMainSync(block)
    private class Counter : BaseAudioProcessor() {
        private val bytes = ByteArrayOutputStream()
        @Synchronized fun captured() = bytes.toByteArray()
        override fun onConfigure(inputAudioFormat: AudioProcessor.AudioFormat): AudioProcessor.AudioFormat = inputAudioFormat
        @Synchronized override fun queueInput(inputBuffer: ByteBuffer) {
            val output = replaceOutputBuffer(inputBuffer.remaining())
            val copy = ByteArray(inputBuffer.remaining()); inputBuffer.get(copy)
            bytes.write(copy); output.put(copy).flip()
        }
    }
    private inner class Harness(val file: String = "three-tones.pcm", val contentType: String = "audio/pcm;rate=16000;channels=1",
        val ending: String? = "complete", val cut: Boolean = false, val unauthorized: Boolean = false,
        val endingUnauthorized: Boolean = false, val noHeaders: Boolean = false) : AutoCloseable {
        val expected = instrumentation.context.assets.open(file).use { it.readBytes() }
        val server = MockWebServer()
        val requests = CopyOnWriteArrayList<RecordedRequest>()
        val signals = CopyOnWriteArrayList<PlayerSignal>()
        val started = CountDownLatch(1)
        val settled = CountDownLatch(1)
        val released = CountDownLatch(1)
        val releaseCount = AtomicInteger()
        @Volatile var releaseError: Throwable? = null
        val output = Counter()
        val connection: TtsConnection
        val transport: TtsTransport
        val player: TtsPlayer
        val resource = AudioResource("instance", "session", "secret", "reading", 1, "audio", contentType)
        init {
            val certificate = HeldCertificate.Builder().addSubjectAlternativeName("localhost").build()
            server.useHttps(HandshakeCertificates.Builder().heldCertificate(certificate).build().sslSocketFactory(), false)
            server.dispatcher = object : Dispatcher() {
                override fun dispatch(request: RecordedRequest): MockResponse {
                    requests += request
                    assertNull(request.getHeader("Range"))
                    assertEquals("secret", request.getHeader("X-TwiCC-TTS-Session"))
                    if (request.method == "DELETE") { releaseCount.incrementAndGet(); return MockResponse().setResponseCode(204) }
                    if (request.path!!.endsWith("ending/")) {
                        if (endingUnauthorized) return MockResponse().setResponseCode(401)
                        if (ending == null) return MockResponse().setResponseCode(503)
                        return MockResponse().setHeader("Content-Type", "application/json").setBody("{\"state\":\"$ending\",\"error\":\"Generation failed\"}")
                    }
                    if (unauthorized) return MockResponse().setResponseCode(401)
                    if (noHeaders) return MockResponse().setSocketPolicy(SocketPolicy.NO_RESPONSE)
                    return MockResponse().setHeader("Content-Type", contentType)
                        .setChunkedBody(Buffer().write(expected), 797)
                        .throttleBody((expected.size / 3).toLong(), 1700, TimeUnit.MILLISECONDS)
                        .apply { if (cut) setSocketPolicy(SocketPolicy.DISCONNECT_DURING_RESPONSE_BODY) }
                }
            }
            server.protocols = listOf(Protocol.HTTP_1_1)
            server.start()
            val trust = HandshakeCertificates.Builder().addTrustedCertificate(certificate.certificate).build()
            connection = TtsConnection(TtsCredentials("instance", server.url("/").toString(), emptyList())) {
                sslSocketFactory(trust.sslSocketFactory(), trust.trustManager)
            }
            transport = object : TtsTransport {
                override val connection get() = this@Harness.connection
                override var event: (TtsConnectionEvent) -> Unit = {}
                override fun connect(handshake: TtsFrame) = "unused"
                override fun send(frame: TtsFrame) = true
                override fun reconciled(connectionId: String) {}
                override fun cancelAudio(resource: AudioResource) = connection.cancelAudio(resource)
                override fun release(resource: AudioResource) = connection.release(resource)
                override fun close() = connection.close()
            }
            var created: TtsPlayer? = null
            main {
                created = TtsPlayer(context, object : DefaultRenderersFactory(context) {
                    override fun buildAudioSink(context: Context, enableFloatOutput: Boolean, enableAudioOutputPlaybackParams: Boolean): AudioSink =
                        DefaultAudioSink.Builder(context).setAudioProcessors(arrayOf(output)).build()
                })
            }
            player = created!!
        }
        fun start() = main {
            player.prepare(resource, transport) {
                signals += it
                if (it == PlayerSignal.Started) started.countDown()
                if (it is PlayerSignal.Ended || it == PlayerSignal.AuthorizationLost) {
                    settled.countDown()
                    // The production service owns Release. This fixture uses the same post-settlement ordering.
                    Thread {
                        try { transport.release(resource) } catch (error: Throwable) { releaseError = error }
                        finally { released.countDown() }
                    }.start()
                }
            }
            player.play(resource.readingId, resource.generation)
        }
        fun awaitEnd(): PlayerSignal.Ended {
            assertTrue("Playback does not settle: $signals", settled.await(15, TimeUnit.SECONDS))
            assertTrue(released.await(5, TimeUnit.SECONDS))
            assertNull(releaseError)
            android.util.Log.i("TtsPlaybackEvidence", "file=$file submittedFrames=${output.captured().size / 2} signals=$signals releases=${releaseCount.get()} requests=${requests.size}")
            return signals.filterIsInstance<PlayerSignal.Ended>().single()
        }
        override fun close() {
            if (settled.count == 0L) released.await(5, TimeUnit.SECONDS)
            main { player.close() }; connection.close(); server.shutdown()
        }
    }
    @Test fun threeDelayedFragmentsProduceOneCompleteReading() {
        Harness().use { h ->
            h.start(); assertTrue(h.started.await(8, TimeUnit.SECONDS))
            assertFalse("A generation-complete response cannot finish buffered playback", h.settled.await(150, TimeUnit.MILLISECONDS))
            assertEquals(PlayerSignal.Ended(true), h.awaitEnd())
            assertEquals(24000, h.output.captured().size / 2)
            assertArrayEquals(h.expected, h.output.captured())
            assertEquals(1, h.signals.count { it == PlayerSignal.Started })
            val firstOutput = h.signals.indexOf(PlayerSignal.Started)
            assertTrue(h.signals.drop(firstOutput + 1).contains(PlayerSignal.Buffering))
            assertTrue(h.signals.drop(firstOutput + 1).contains(PlayerSignal.Resumed))
            assertTrue(h.signals.indexOf(PlayerSignal.Settling) > firstOutput)
            assertEquals(1, h.signals.count { it is PlayerSignal.Ended && it.ok })
            assertEquals(1, h.releaseCount.get())
            assertEquals(1, h.requests.count { it.method == "GET" && !it.path!!.endsWith("ending/") })
        }
    }
    @Test fun wavScriptNormalizedSamplesUseTheSamePcmOutput() {
        Harness("wav-origin.pcm").use { h ->
            h.start(); assertEquals(PlayerSignal.Ended(true), h.awaitEnd())
            assertArrayEquals(h.expected, h.output.captured())
            assertEquals(24000, h.output.captured().size / 2)
        }
    }
    @Test fun mp3UsesTheSamePlayerAndDrainsAcrossFragmentGaps() {
        Harness("three-tones.mp3", "audio/mpeg").use { h ->
            h.start(); assertEquals(PlayerSignal.Ended(true), h.awaitEnd())
            assertEquals(1, h.signals.count { it == PlayerSignal.Started })
            assertEquals(25344, h.output.captured().size / 2) // 44 MPEG-2 frames after backend Info-frame stripping.
            assertEquals(1, h.releaseCount.get())
        }
    }
    @Test fun drainedPlayerWithUnknownOrMissingEndingSucceeds() {
        for (ending in listOf("unknown", null)) Harness(ending = ending).use { h ->
            h.start(); assertEquals(PlayerSignal.Ended(true), h.awaitEnd())
        }
    }
    @Test fun backendRunningOrFailedAfterDrainFails() {
        for (ending in listOf("running", "failed")) Harness(ending = ending).use { h ->
            h.start(); assertFalse(h.awaitEnd().ok); assertEquals(1, h.releaseCount.get())
            assertArrayEquals(h.expected, h.output.captured())
        }
    }
    @Test fun networkFailureAfterOutputNeverReplaysOrSucceedsFromGenerationComplete() {
        Harness(cut = true).use { h ->
            h.start(); assertTrue(h.started.await(8, TimeUnit.SECONDS)); assertFalse(h.awaitEnd().ok)
            assertEquals(1, h.requests.count { it.method == "GET" && !it.path!!.endsWith("ending/") })
            assertEquals(1, h.releaseCount.get())
        }
    }
    @Test fun skipMidGapCancelsWithoutLateStartedOrSettlement() {
        Harness().use { h ->
            h.start(); assertTrue(h.started.await(8, TimeUnit.SECONDS))
            val firstOutput = h.signals.indexOf(PlayerSignal.Started)
            val gapDeadline = System.nanoTime() + TimeUnit.SECONDS.toNanos(5)
            while (!h.signals.drop(firstOutput + 1).contains(PlayerSignal.Buffering) && System.nanoTime() < gapDeadline) Thread.sleep(10)
            assertTrue("Skip must occur inside a delayed fragment gap", h.signals.drop(firstOutput + 1).contains(PlayerSignal.Buffering))
            main { h.player.cancel("reading", 1) }
            // Cancel reports no terminal callback; the service policy already settles and releases Skip.
            h.transport.release(h.resource)
            assertFalse(h.settled.await(2500, TimeUnit.MILLISECONDS))
            assertEquals(1, h.signals.count { it == PlayerSignal.Started })
            assertEquals(1, h.releaseCount.get())
        }
    }
    @Test fun authenticationFailureKeepsItsTypedSessionTerminationSignal() {
        Harness(unauthorized = true).use { h ->
            h.start(); assertTrue(h.settled.await(8, TimeUnit.SECONDS))
            assertTrue(h.signals.contains(PlayerSignal.AuthorizationLost))
            assertFalse(h.signals.contains(PlayerSignal.Started))
        }
    }
    @Test fun authenticationLossDuringEndingRemainsASessionFailureAfterAudioDrains() {
        Harness(endingUnauthorized = true).use { h ->
            h.start(); assertTrue(h.settled.await(12, TimeUnit.SECONDS))
            assertTrue(h.signals.contains(PlayerSignal.Started))
            assertEquals(PlayerSignal.AuthorizationLost, h.signals.last())
            assertArrayEquals(h.expected, h.output.captured())
        }
    }
    @Test fun cancelBeforeResponseHeadersDoesNotWaitForNetworkOrReportLateFailure() {
        Harness(noHeaders = true).use { h ->
            h.start()
            val deadline = System.nanoTime() + TimeUnit.SECONDS.toNanos(5)
            while (h.requests.isEmpty() && System.nanoTime() < deadline) Thread.sleep(10)
            assertEquals(1, h.requests.size)
            main { h.player.cancel("reading", 1) }
            h.transport.release(h.resource)
            assertFalse(h.settled.await(200, TimeUnit.MILLISECONDS))
            assertFalse(h.signals.contains(PlayerSignal.Started))
        }
    }
    @Test fun permanentFocusDenialFailsWithoutStartedFeedback() {
        activity.close()
        Harness().use { h ->
            h.start(); assertFalse(h.awaitEnd().ok)
            assertFalse(h.signals.contains(PlayerSignal.Started))
            assertEquals(0, h.output.captured().size)
        }
    }
    @Test fun competingNonDuckableFocusCancelsTheReading() {
        Harness().use { h ->
            h.start(); assertTrue(h.started.await(8, TimeUnit.SECONDS))
            val manager = context.getSystemService(AudioManager::class.java)
            val request = AudioFocusRequest.Builder(AudioManager.AUDIOFOCUS_GAIN_TRANSIENT)
                .setAudioAttributes(android.media.AudioAttributes.Builder().setUsage(android.media.AudioAttributes.USAGE_ALARM).build())
                .setOnAudioFocusChangeListener { }.build()
            try {
                assertEquals(AudioManager.AUDIOFOCUS_REQUEST_GRANTED, manager.requestAudioFocus(request))
                assertFalse(h.awaitEnd().ok)
            } finally { manager.abandonAudioFocusRequest(request) }
        }
    }
}

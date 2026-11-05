package com.twidi.twicc.tts

import android.content.Context
import android.net.Uri
import android.os.Handler
import android.os.Looper
import androidx.media3.common.AudioAttributes
import androidx.media3.common.C
import androidx.media3.common.MediaItem
import androidx.media3.common.PlaybackException
import androidx.media3.common.Player
import androidx.media3.common.Timeline
import androidx.media3.common.util.UnstableApi
import androidx.media3.datasource.BaseDataSource
import androidx.media3.datasource.DataSource
import androidx.media3.datasource.DataSpec
import androidx.media3.exoplayer.DefaultLoadControl
import androidx.media3.exoplayer.DefaultRenderersFactory
import androidx.media3.exoplayer.ExoPlayer
import androidx.media3.exoplayer.RenderersFactory
import androidx.media3.exoplayer.analytics.AnalyticsListener
import androidx.media3.exoplayer.source.ProgressiveMediaSource
import androidx.media3.exoplayer.upstream.DefaultLoadErrorHandlingPolicy
import androidx.media3.exoplayer.upstream.LoadErrorHandlingPolicy
import androidx.media3.extractor.Extractor
import androidx.media3.extractor.ExtractorsFactory
import androidx.media3.extractor.mp3.Mp3Extractor
import java.io.IOException
import java.util.concurrent.Executors
import java.util.concurrent.atomic.AtomicBoolean

/** Mirrors speechOutcome.js. A decoder/network error cannot become success after generation completes. */
internal fun speechEndResult(drained: Boolean, started: Boolean, refused: Boolean, backend: TtsFrame?): PlayerSignal.Ended {
    val state = backend?.get("state")
    if (state == "failed") return PlayerSignal.Ended(false, backend["error"] as? String ?: "Speech generation failed")
    if (drained) return if (state == "running") PlayerSignal.Ended(false, "The audio stopped before the end") else PlayerSignal.Ended(true)
    if (backend == null) return PlayerSignal.Ended(false, "Could not reach TwiCC")
    if (state != "complete" && state != "running") return PlayerSignal.Ended(false, "The audio is no longer available")
    if (refused) return PlayerSignal.Ended(false, "Android refused to play the audio")
    return PlayerSignal.Ended(false, if (started) "The audio stopped before the end" else "Could not reach TwiCC")
}

/** One response, one forward read. Media3 cannot reopen a partially consumed resource or issue a Range request. */
@UnstableApi
internal class TtsAudioDataSource(private val connection: TtsConnection, private val resource: AudioResource,
    private val opened: AtomicBoolean, private val cancelled: AtomicBoolean) : BaseDataSource(true) {
    private var stream: TtsAudioStream? = null
    private var uri: Uri? = null
    private var headers: Map<String, List<String>> = emptyMap()
    override fun open(dataSpec: DataSpec): Long {
        if (dataSpec.position != 0L || !opened.compareAndSet(false, true)) throw IOException("Speech cannot seek, retry or replay")
        transferInitializing(dataSpec)
        val response = connection.openAudio(resource) { cancelled.get() }
        stream = response; uri = dataSpec.uri
        val contentType = response.contentType ?: run {
            stream = null; response.close(); throw IOException("Speech response has no Content-Type")
        }
        headers = mapOf("Content-Type" to listOf(contentType))
        transferStarted(dataSpec)
        return C.LENGTH_UNSET.toLong()
    }
    override fun read(buffer: ByteArray, offset: Int, length: Int): Int {
        if (length == 0) return 0
        val count = (stream ?: throw IOException("Speech response is closed")).input.read(buffer, offset, length)
        if (count > 0) bytesTransferred(count)
        return count
    }
    override fun getUri() = uri
    override fun getResponseHeaders() = headers
    override fun close() {
        val current = stream; stream = null
        if (current != null) { current.close(); transferEnded() }
        uri = null
    }
}

/** One common output for both browser engines. The service owns all backend resource releases. */
@UnstableApi
class TtsPlayer(context: Context, renderersFactory: RenderersFactory = DefaultRenderersFactory(context)) : TtsPlaybackOutput {
    private val handler = Handler(Looper.getMainLooper())
    private val workers = Executors.newCachedThreadPool { runnable -> Thread(runnable, "twicc-tts-ending").apply { isDaemon = true } }
    private val player = ExoPlayer.Builder(context.applicationContext, renderersFactory).setLooper(Looper.getMainLooper())
        .setLoadControl(DefaultLoadControl.Builder().setBufferDurationsMs(1000, 10000, 100, 100).build()).build().apply {
        setAudioAttributes(AudioAttributes.Builder().setUsage(C.USAGE_MEDIA).setContentType(C.AUDIO_CONTENT_TYPE_SPEECH).build(), true)
        setHandleAudioBecomingNoisy(true)
        repeatMode = Player.REPEAT_MODE_OFF
    }
    private class Reading(val resource: AudioResource, val connection: TtsConnection, val feedback: (PlayerSignal) -> Unit) {
        val mediaId = "${resource.readingId}/${resource.generation}"
        val cancelled = AtomicBoolean()
        var started = false
        var settling = false
        lateinit var listener: Player.Listener
        lateinit var analytics: AnalyticsListener
    }
    private var current: Reading? = null
    private var closed = false
    private fun checkOwner() = check(Looper.myLooper() == Looper.getMainLooper()) { "Speech output requires the service main looper" }
    private fun active(reading: Reading) = !closed && current === reading && !reading.settling
    override fun prepare(resource: AudioResource, connection: TtsTransport, feedback: (PlayerSignal) -> Unit) {
        checkOwner(); check(!closed)
        current?.let { cancel(it.resource.readingId, it.resource.generation) }
        val http = connection.connection ?: run { feedback(PlayerSignal.Ended(false, "Native speech connection is unavailable")); return }
        val reading = Reading(resource, http, feedback); current = reading
        reading.listener = object : Player.Listener {
            override fun onPlaybackStateChanged(playbackState: Int) {
                if (!active(reading) || player.currentMediaItem?.mediaId != reading.mediaId || playbackState != player.playbackState) return
                when (playbackState) {
                    Player.STATE_BUFFERING -> feedback(PlayerSignal.Buffering)
                    Player.STATE_ENDED -> settle(reading, drained = true)
                }
            }
            override fun onPlayerError(error: PlaybackException) {
                if (!active(reading)) return
                if (generateSequence<Throwable>(error) { it.cause }.any { it is TtsAuthorizationException }) authorizationLost(reading)
                else settle(reading, drained = false)
            }
            override fun onPlayWhenReadyChanged(playWhenReady: Boolean, reason: Int) {
                if (active(reading) && !playWhenReady && reason in setOf(Player.PLAY_WHEN_READY_CHANGE_REASON_AUDIO_FOCUS_LOSS,
                        Player.PLAY_WHEN_READY_CHANGE_REASON_AUDIO_BECOMING_NOISY)) {
                    feedback(PlayerSignal.Buffering)
                    settle(reading, drained = false, refused = true)
                }
            }
            override fun onIsPlayingChanged(isPlaying: Boolean) {
                if (active(reading) && reading.started && isPlaying) feedback(PlayerSignal.Resumed)
            }
            override fun onPlaybackSuppressionReasonChanged(playbackSuppressionReason: Int) {
                if (!active(reading) || playbackSuppressionReason == Player.PLAYBACK_SUPPRESSION_REASON_NONE) return
                feedback(PlayerSignal.Buffering)
                settle(reading, drained = false, refused = true)
            }
        }
        reading.analytics = object : AnalyticsListener {
            override fun onAudioPositionAdvancing(eventTime: AnalyticsListener.EventTime, playoutStartSystemTimeMs: Long) {
                if (!active(reading) || reading.started || player.playbackSuppressionReason != Player.PLAYBACK_SUPPRESSION_REASON_NONE) return
                if (eventTime.timeline.isEmpty || eventTime.timeline.getWindow(eventTime.windowIndex, Timeline.Window()).mediaItem.mediaId != reading.mediaId) return
                reading.started = true
                feedback(PlayerSignal.Started)
            }
        }
        player.addListener(reading.listener); player.addAnalyticsListener(reading.analytics)
        val once = AtomicBoolean()
        val extractors = object : ExtractorsFactory {
            override fun createExtractors(): Array<Extractor> = throw IllegalStateException("Speech extraction requires response headers")
            override fun createExtractors(uri: Uri, responseHeaders: Map<String, List<String>>): Array<Extractor> {
                val contentType = responseHeaders.entries.firstOrNull { it.key.equals("Content-Type", true) }?.value?.firstOrNull()
                    ?: throw IOException("Speech response has no Content-Type")
                return when (contentType.substringBefore(';').trim().lowercase()) {
                    "audio/pcm" -> arrayOf(PcmExtractor(PcmFormat.parse(contentType)))
                    "audio/mpeg", "audio/mp3" -> arrayOf(Mp3Extractor().apply { disableSeeking() })
                    else -> throw IOException("Unsupported speech audio format")
                }
            }
        }
        val source = ProgressiveMediaSource.Factory(DataSource.Factory { TtsAudioDataSource(http, resource, once, reading.cancelled) }, extractors)
            .setLoadErrorHandlingPolicy(object : DefaultLoadErrorHandlingPolicy(0) {
                override fun getRetryDelayMsFor(loadErrorInfo: LoadErrorHandlingPolicy.LoadErrorInfo) = C.TIME_UNSET
            })
            .createMediaSource(MediaItem.Builder().setUri("https://tts.invalid/audio").setMediaId(reading.mediaId).build())
        player.setMediaSource(source)
        player.prepare()
    }
    override fun play(readingId: String, generation: Long) {
        checkOwner()
        current?.takeIf { it.resource.readingId == readingId && it.resource.generation == generation && active(it) }?.let { player.play() }
    }
    private fun detach(reading: Reading) {
        reading.cancelled.set(true)
        reading.connection.cancelAudio(reading.resource)
        player.removeListener(reading.listener); player.removeAnalyticsListener(reading.analytics)
        player.stop(); player.clearMediaItems()
    }
    private fun authorizationLost(reading: Reading) {
        if (!active(reading)) return
        reading.settling = true; detach(reading); current = null
        reading.feedback(PlayerSignal.AuthorizationLost)
    }
    private fun settle(reading: Reading, drained: Boolean, refused: Boolean = false) {
        if (!active(reading)) return
        reading.settling = true
        detach(reading)
        reading.feedback(PlayerSignal.Settling)
        workers.execute {
            val result = try {
                speechEndResult(drained, reading.started, refused, reading.connection.readEnding(reading.resource))
            } catch (_: TtsAuthorizationException) { PlayerSignal.AuthorizationLost }
              catch (_: Exception) { speechEndResult(drained, reading.started, refused, null) }
            handler.post {
                if (!closed && current === reading) { current = null; reading.feedback(result) }
            }
        }
    }
    override fun cancel(readingId: String, generation: Long) {
        checkOwner()
        val reading = current?.takeIf { it.resource.readingId == readingId && it.resource.generation == generation } ?: return
        current = null
        reading.connection.cancelAudio(reading.resource)
        detach(reading)
    }
    override fun close() {
        checkOwner()
        if (closed) return
        current?.let { cancel(it.resource.readingId, it.resource.generation) }
        closed = true; player.release(); workers.shutdownNow()
    }
}

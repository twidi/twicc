package com.twidi.twicc.tts

import androidx.media3.common.C
import androidx.media3.common.Format
import androidx.media3.common.MimeTypes
import androidx.media3.common.util.ParsableByteArray
import androidx.media3.common.util.UnstableApi
import androidx.media3.extractor.Extractor
import androidx.media3.extractor.ExtractorInput
import androidx.media3.extractor.ExtractorOutput
import androidx.media3.extractor.PositionHolder
import androidx.media3.extractor.SeekMap
import androidx.media3.extractor.TrackOutput
import java.io.EOFException
import java.io.InterruptedIOException

/** The server normalizes WAV scripts to this signed, little-endian PCM-16 wire format. */
internal data class PcmFormat(val rate: Int, val channels: Int) {
    val frameBytes get() = channels * 2
    companion object {
        fun parse(contentType: String): PcmFormat {
            val parts = contentType.lowercase().split(';').map { it.trim() }
            require(parts.first() == "audio/pcm") { "Unsupported speech audio format" }
            val parameters = mutableMapOf<String, String>()
            for (part in parts.drop(1)) {
                val pair = part.split('=', limit = 2)
                require(pair.size == 2 && pair[0] in setOf("rate", "channels")) { "Unsupported PCM parameters" }
                require(parameters.put(pair[0], pair[1]) == null) { "Duplicate PCM parameter" }
            }
            val rate = parameters["rate"]?.toIntOrNull()
            val channels = parameters["channels"]?.toIntOrNull()
            require(rate != null && rate in 1..384000 && channels != null && channels in 1..8) { "Unsupported PCM rate or channels" }
            return PcmFormat(rate, channels)
        }
    }
}
internal data class PcmChunk(val bytes: ByteArray, val timeUs: Long)

/** Retains only an incomplete interleaved frame. Timestamps never depend on arrival time. */
internal class PcmFrames(private val format: PcmFormat) {
    private var tail = byteArrayOf()
    var frameCount = 0L
        private set
    @Volatile private var cancelled = false
    private fun checkActive() { if (cancelled) throw InterruptedIOException("Speech extraction cancelled") }
    fun append(bytes: ByteArray, offset: Int, length: Int): PcmChunk? {
        checkActive()
        val data = ByteArray(tail.size + length)
        tail.copyInto(data); bytes.copyInto(data, tail.size, offset, offset + length)
        val complete = data.size - data.size % format.frameBytes
        tail = data.copyOfRange(complete, data.size)
        if (complete == 0) return null
        val timeUs = frameCount * 1_000_000 / format.rate
        frameCount += complete / format.frameBytes
        return PcmChunk(data.copyOf(complete), timeUs)
    }
    fun end() { checkActive(); if (tail.isNotEmpty()) throw EOFException("The PCM stream ends inside an audio frame") }
    fun cancel() { cancelled = true }
}

/** One unseekable track with unknown length. Backend EOF, rather than a fabricated WAV size, ends it. */
@UnstableApi
internal class PcmExtractor(private val format: PcmFormat) : Extractor {
    private val frames = PcmFrames(format)
    private val buffer = ByteArray(8192)
    private lateinit var track: TrackOutput
    override fun sniff(input: ExtractorInput) = true // The response Content-Type selects this extractor.
    override fun init(output: ExtractorOutput) {
        track = output.track(0, C.TRACK_TYPE_AUDIO)
        track.format(Format.Builder().setSampleMimeType(MimeTypes.AUDIO_RAW)
            .setSampleRate(format.rate).setChannelCount(format.channels).setPcmEncoding(C.ENCODING_PCM_16BIT).build())
        output.endTracks()
        output.seekMap(SeekMap.Unseekable(C.TIME_UNSET))
    }
    override fun read(input: ExtractorInput, seekPosition: PositionHolder): Int {
        val length = input.read(buffer, 0, buffer.size)
        if (length == C.RESULT_END_OF_INPUT) { frames.end(); return Extractor.RESULT_END_OF_INPUT }
        frames.append(buffer, 0, length)?.let {
            track.sampleData(ParsableByteArray(it.bytes), it.bytes.size)
            track.sampleMetadata(it.timeUs, C.BUFFER_FLAG_KEY_FRAME, it.bytes.size, 0, null)
        }
        return Extractor.RESULT_CONTINUE
    }
    override fun seek(position: Long, timeUs: Long) {
        check(position == 0L && timeUs == 0L && frames.frameCount == 0L) { "Speech cannot seek or replay" }
    }
    override fun release() = frames.cancel()
}

package com.twidi.twicc.tts

import org.junit.Assert.*
import org.junit.Test
import java.io.EOFException
import java.io.InterruptedIOException

class PcmExtractorTest {
    @Test fun oddChunksKeepEveryCompleteStereoFrameAndByteCountTimestamps() {
        val frames = PcmFrames(PcmFormat.parse("audio/pcm;rate=24000;channels=2"))
        val input = ByteArray(24004) { (it % 251).toByte() }
        val chunks = mutableListOf<PcmChunk>()
        var offset = 0
        listOf(1, 3, 101, 1, 239, 8191, 15468).forEach { size ->
            frames.append(input, offset, size)?.let { chunks += it }; offset += size
        }
        frames.end()
        assertArrayEquals(input, chunks.flatMap { it.bytes.asIterable() }.toByteArray())
        var count = 0L
        for (chunk in chunks) {
            assertEquals(count * 1_000_000 / 24000, chunk.timeUs)
            assertEquals(0, chunk.bytes.size % 4)
            count += chunk.bytes.size / 4
        }
        assertEquals(6001L, count)
    }
    @Test fun incompleteFinalFrameFailsInsteadOfSilentlyTruncating() {
        val frames = PcmFrames(PcmFormat.parse("audio/pcm;rate=8000;channels=2"))
        assertNull(frames.append(byteArrayOf(1,2,3), 0, 3))
        assertThrows(EOFException::class.java) { frames.end() }
    }
    @Test fun cancellationRejectsFurtherSamplesAndDropsPartialFrame() {
        val frames = PcmFrames(PcmFormat.parse("audio/pcm;rate=16000;channels=1"))
        frames.append(byteArrayOf(1), 0, 1); frames.cancel()
        assertThrows(InterruptedIOException::class.java) { frames.append(byteArrayOf(2), 0, 1) }
        assertThrows(InterruptedIOException::class.java) { frames.end() }
    }
    @Test fun rejectsUndeclaredOrUnsupportedPcmFormats() {
        listOf("audio/wav", "audio/pcm", "audio/pcm;rate=0;channels=1", "audio/pcm;rate=24000;channels=0",
            "audio/pcm;rate=24000;channels=9", "audio/pcm;rate=24000;channels=1;bits=24",
            "audio/pcm;rate=24000;channels=1;encoding=float", "audio/pcm;rate=24000;rate=8000;channels=1")
            .forEach { contentType -> assertThrows(contentType, IllegalArgumentException::class.java) { PcmFormat.parse(contentType) } }
    }
    @Test fun acceptsDeclaredServerLimitsAndRejectsMissingChannels() {
        assertEquals(PcmFormat(384000, 8), PcmFormat.parse("audio/pcm;rate=384000;channels=8"))
        assertThrows(IllegalArgumentException::class.java) { PcmFormat.parse("audio/pcm;rate=24000") }
    }
}

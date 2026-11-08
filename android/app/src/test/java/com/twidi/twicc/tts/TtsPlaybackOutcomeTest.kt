package com.twidi.twicc.tts

import org.junit.Assert.*
import org.junit.Test

class TtsPlaybackOutcomeTest {
    @Test fun drainedPlayerRequiresNoRunningOrFailedBackend() {
        assertEquals(PlayerSignal.Ended(true), speechEndResult(true, false, false, null))
        assertEquals(PlayerSignal.Ended(true), speechEndResult(true, false, false, mapOf("state" to "unknown")))
        assertEquals(PlayerSignal.Ended(true), speechEndResult(true, true, false, mapOf("state" to "complete")))
        assertEquals(PlayerSignal.Ended(false, "The audio stopped before the end", mapOf("key" to "session.speech.errors.cut", "params" to emptyMap<String, Any?>())), speechEndResult(true, true, false, mapOf("state" to "running")))
        assertEquals(PlayerSignal.Ended(false, "Generation failed"), speechEndResult(true, true, false, mapOf("state" to "failed", "error" to "Generation failed")))
    }
    @Test fun generationCompletionNeverTurnsPlayerErrorIntoSuccess() {
        assertEquals(PlayerSignal.Ended(false, "Could not reach TwiCC", mapOf("key" to "session.speech.errors.unreachable", "params" to emptyMap<String, Any?>())), speechEndResult(false, true, false, null))
        assertEquals(PlayerSignal.Ended(false, "The audio is no longer available", mapOf("key" to "session.speech.errors.gone", "params" to emptyMap<String, Any?>())), speechEndResult(false, true, false, mapOf("state" to "unknown")))
        assertEquals(PlayerSignal.Ended(false, "Could not reach TwiCC", mapOf("key" to "session.speech.errors.unreachable", "params" to emptyMap<String, Any?>())), speechEndResult(false, false, false, mapOf("state" to "complete")))
        assertEquals(PlayerSignal.Ended(false, "The audio stopped before the end", mapOf("key" to "session.speech.errors.cut", "params" to emptyMap<String, Any?>())), speechEndResult(false, true, false, mapOf("state" to "complete")))
        assertEquals(PlayerSignal.Ended(false, "Audio playback could not start.", mapOf("key" to "android.speech.errors.playbackFailed", "params" to emptyMap<String, Any?>())), speechEndResult(false, false, true, mapOf("state" to "complete")))
        assertEquals(PlayerSignal.Ended(false, "Audio playback was interrupted.", mapOf("key" to "android.speech.errors.interrupted", "params" to emptyMap<String, Any?>())), speechEndResult(false, true, true, mapOf("state" to "complete")))
        assertEquals(PlayerSignal.Ended(false, "Audio playback was interrupted.", mapOf("key" to "android.speech.errors.interrupted", "params" to emptyMap<String, Any?>())), speechEndResult(false, true, true, null))
        assertEquals(PlayerSignal.Ended(false, "Generation failed"), speechEndResult(false, true, true, mapOf("state" to "failed", "error" to "Generation failed")))
    }
    @Test fun backendFailureMetadataSurvivesAudioSettlement() {
        val metadata = mapOf("key" to "backend.tts.failed", "params" to emptyMap<String, Any?>())
        val result = speechEndResult(true, true, false, mapOf("state" to "failed", "error" to "Generation failed", "ui_message" to metadata))
        assertEquals("Generation failed", result.error)
        assertEquals(metadata, result.uiMessage)
    }

    @Test fun allOwnedSpeechEndFailuresCarryDescriptors() {
        val cases = listOf(
            speechEndResult(true, true, false, mapOf("state" to "running")) to "session.speech.errors.cut",
            speechEndResult(false, true, true, null) to "android.speech.errors.interrupted",
            speechEndResult(false, false, true, null) to "android.speech.errors.playbackFailed",
            speechEndResult(false, true, false, null) to "session.speech.errors.unreachable",
            speechEndResult(false, true, false, mapOf("state" to "unknown")) to "session.speech.errors.gone",
            speechEndResult(false, true, false, mapOf("state" to "complete")) to "session.speech.errors.cut",
            speechEndResult(false, false, false, mapOf("state" to "complete")) to "session.speech.errors.unreachable",
            speechEndResult(false, true, false, mapOf("state" to "running")) to "session.speech.errors.cut",
            speechEndResult(false, false, false, mapOf("state" to "running")) to "session.speech.errors.unreachable",
            speechEndResult(false, false, false, mapOf("state" to "failed")) to "android.speech.errors.generationFailed",
        )
        for ((result, key) in cases) {
            assertFalse(result.ok)
            assertEquals(key, result.uiMessage?.get("key"))
        }
        val external = speechEndResult(false, true, false, mapOf("state" to "failed", "error" to "Raw upstream diagnostic"))
        assertEquals("Raw upstream diagnostic", external.error)
        assertNull(external.uiMessage)
    }

    @Test fun nativeDiagnosticsSurviveUnlessBackendOwnsTheFailure() {
        val native = mapOf("key" to "android.speech.errors.audioFormat", "params" to emptyMap<String, Any?>())
        assertEquals(native, speechEndResult(false, true, false, null, native).uiMessage)
        val backend = mapOf("key" to "backend.tts.failed", "params" to emptyMap<String, Any?>())
        assertEquals(backend, speechEndResult(false, true, false,
            mapOf("state" to "failed", "error" to "Upstream diagnostic", "ui_message" to backend), native).uiMessage)
        val external = speechEndResult(false, true, false, mapOf("state" to "failed", "error" to "Raw upstream diagnostic"), native)
        assertEquals("Raw upstream diagnostic", external.error)
        assertNull(external.uiMessage)
    }

}

package com.twidi.twicc.tts

import org.junit.Assert.*
import org.junit.Test

class TtsPlaybackOutcomeTest {
    @Test fun drainedPlayerRequiresNoRunningOrFailedBackend() {
        assertEquals(PlayerSignal.Ended(true), speechEndResult(true, false, false, null))
        assertEquals(PlayerSignal.Ended(true), speechEndResult(true, false, false, mapOf("state" to "unknown")))
        assertEquals(PlayerSignal.Ended(true), speechEndResult(true, true, false, mapOf("state" to "complete")))
        assertEquals(PlayerSignal.Ended(false, "The audio stopped before the end"), speechEndResult(true, true, false, mapOf("state" to "running")))
        assertEquals(PlayerSignal.Ended(false, "Generation failed"), speechEndResult(true, true, false, mapOf("state" to "failed", "error" to "Generation failed")))
    }
    @Test fun generationCompletionNeverTurnsPlayerErrorIntoSuccess() {
        assertEquals(PlayerSignal.Ended(false, "Could not reach TwiCC"), speechEndResult(false, true, false, null))
        assertEquals(PlayerSignal.Ended(false, "The audio is no longer available"), speechEndResult(false, true, false, mapOf("state" to "unknown")))
        assertEquals(PlayerSignal.Ended(false, "Could not reach TwiCC"), speechEndResult(false, false, false, mapOf("state" to "complete")))
        assertEquals(PlayerSignal.Ended(false, "The audio stopped before the end"), speechEndResult(false, true, false, mapOf("state" to "complete")))
        assertEquals(PlayerSignal.Ended(false, "Audio playback could not start."), speechEndResult(false, false, true, mapOf("state" to "complete")))
        assertEquals(PlayerSignal.Ended(false, "Audio playback was interrupted."), speechEndResult(false, true, true, mapOf("state" to "complete")))
        assertEquals(PlayerSignal.Ended(false, "Audio playback was interrupted."), speechEndResult(false, true, true, null))
        assertEquals(PlayerSignal.Ended(false, "Generation failed"), speechEndResult(false, true, true, mapOf("state" to "failed", "error" to "Generation failed")))
    }
    @Test fun backendFailureMetadataSurvivesAudioSettlement() {
        val metadata = mapOf("key" to "backend.tts.failed", "params" to emptyMap<String, Any?>())
        val result = speechEndResult(true, true, false, mapOf("state" to "failed", "error" to "Generation failed", "ui_message" to metadata))
        assertEquals("Generation failed", result.error)
        assertEquals(metadata, result.uiMessage)
    }

}

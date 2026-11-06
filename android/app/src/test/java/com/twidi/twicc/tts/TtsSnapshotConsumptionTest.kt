package com.twidi.twicc.tts

import org.junit.Assert.*
import org.junit.Test

class TtsSnapshotConsumptionTest {
    private fun state(results: List<NativeTtsResult> = emptyList(), error: TtsFrame? = null) =
        NativeTtsSnapshot(revision = 1, instanceId = "instance", sessionStatus = "error",
            playback = NativePlayback(), listening = null, pendingCount = 0, error = error,
            configurationAvailable = true, results = results)
    private fun failed(id: String) = NativeTtsResult(id, true, mapOf("ok" to true),
        mapOf("ok" to false, "error" to "Playback interrupted"), true, id, 1)

    @Test fun consumingAnErrorRemovesItAndItsResultWithoutChangingPlaybackState() {
        val cache = TtsSnapshotCache()
        val value = cache.stopped(state(listOf(failed("old")), mapOf("code" to "session_lost", "message" to "Stopped")))
        val consumed = cache.acknowledge("instance", value.revision)
        assertEquals(setOf("old"), consumed.operationIds)
        assertNotNull(consumed.error)
        val next = cache.snapshot("instance")
        assertNull(next.error)
        assertTrue(next.results.isEmpty())
        assertEquals("error", next.sessionStatus)
        assertTrue(cache.acknowledge("instance", value.revision).operationIds.isEmpty())
    }

    @Test fun anAcknowledgementCannotDiscardANewerError() {
        val cache = TtsSnapshotCache()
        val first = cache.publish(state(listOf(failed("first")), mapOf("message" to "First")))
        cache.publish(state(listOf(failed("first"), failed("second")), mapOf("message" to "Second")))
        cache.acknowledge("instance", first.revision)
        assertEquals("Second", cache.snapshot("instance").error!!["message"])
        assertEquals(listOf("second"), cache.snapshot("instance").results.map { it.operationId })
    }

    @Test fun undisplayedErrorsExpireWithoutLeavingAnErrorHistory() {
        var now = 0L
        val cache = TtsSnapshotCache { now }
        cache.stopped(state(listOf(failed("old")), mapOf("message" to "Old")))
        now = 9 * 60 * 1000L
        cache.publish(state(listOf(failed("old")), mapOf("message" to "Old")))
        now = 10 * 60 * 1000L
        val expired = cache.expire("instance")
        assertEquals(setOf("old"), expired.operationIds)
        assertNotNull(expired.error)
        assertNull(cache.snapshot("instance").error)
        assertTrue(cache.snapshot("instance").results.isEmpty())
    }

    @Test fun consumingOneInstancePreservesAnotherInstancesPendingError() {
        val cache = TtsSnapshotCache()
        val first = cache.stopped(state(listOf(failed("first")), mapOf("message" to "First")))
        cache.stopped(state(listOf(failed("second")), mapOf("message" to "Second")).copy(instanceId = "other"))
        cache.acknowledge("instance", first.revision)
        assertNull(cache.snapshot("instance").error)
        assertEquals("Second", cache.snapshot("other").error!!["message"])
        assertEquals(listOf("second"), cache.snapshot("other").results.map { it.operationId })
    }
}

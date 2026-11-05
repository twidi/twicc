package com.twidi.twicc.tts

import java.io.File
import java.util.UUID
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test

/** The backend suites assert these same frames against actual TimelineListener output. */
class NativeTtsDispositionContractTest {
    private val owner = DocumentOwner("instance", "page", 1)
    private fun fixture(name: String): TtsFrame = TtsJson.decode(JSONObject(
        File("../../tests/fixtures/native_tts_dispositions.json").readText()).getJSONObject(name).toString())
    private fun controller(): TtsController {
        var next = 1L
        return TtsController("instance", newId = { UUID(0, next++).toString() }).also {
            it.dispatch(TtsCommand.AttachDocument(owner))
            it.dispatch(TtsCommand.Listen(owner, ListeningScope("global", null), false, "2026-11-05T10:00:00.000000Z"))
            it.dispatch(TtsCommand.TransportOpened("socket"))
            it.dispatch(TtsCommand.ServerFrame("socket", mapOf("protocol" to 1, "type" to "opened_session",
                "session_id" to "session", "resume_secret" to "secret", "ack_seq" to 1)))
            candidate(it, 1)
            assertEquals(fixture("rejected")["reading_id"], it.snapshot().playback.readingId)
        }
    }
    private fun candidate(c: TtsController, id: Int) = c.dispatch(TtsCommand.ServerFrame("socket", mapOf(
        "protocol" to 1, "type" to "candidate", "event_id" to id, "activation_id" to c.snapshot().listening!!.activationId)))
    @Test fun backendRejectionReleasesReservationAndAdmitsNextCandidate() {
        val c = controller()
        c.dispatch(TtsCommand.ServerFrame("socket", fixture("rejected")))
        assertNull(c.snapshot().playback.readingId); assertNull(c.snapshot().error)
        val effects = candidate(c, 2)
        assertTrue(effects.filterIsInstance<TtsEffect.Send>().any { it.frame["type"] == "select" })
        assertEquals(2L, c.snapshot().playback.generation); assertTrue(c.snapshot().results.isEmpty())
    }
    @Test fun backendLateAutomaticErrorAndListenerLossPreserveManualReplacement() {
        val c = controller()
        c.dispatch(TtsCommand.Manual(owner, "manual", mapOf("kind" to "text", "source" to "message", "text" to "Manual",
            "speech" to mapOf("text" to "Manual"))))
        val before = c.snapshot().playback
        c.dispatch(TtsCommand.ServerFrame("socket", fixture("summary_error")))
        val effects = c.dispatch(TtsCommand.ServerFrame("socket", fixture("listener_error")))
        assertEquals(before, c.snapshot().playback); assertNull(c.snapshot().listening)
        assertTrue(effects.none { it is TtsEffect.Cancel || it is TtsEffect.Release })
        assertTrue(c.snapshot().results.isEmpty())
    }
}

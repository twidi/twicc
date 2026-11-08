package com.twidi.twicc.tts

import org.junit.Assert.*
import org.junit.Test

class TtsPresentationMetadataTest {
    private val owner = DocumentOwner("instance", "page", 1)
    private val metadata = mapOf("key" to "backend.tts.failed", "params" to emptyMap<String, Any?>())
    private fun controller(listening: Boolean = false): TtsController {
        val controller = TtsController("instance")
        controller.dispatch(TtsCommand.AttachDocument(owner))
        if (listening) controller.dispatch(TtsCommand.Listen(owner, ListeningScope("global", null), false, "now"))
        else controller.dispatch(TtsCommand.Manual(owner, "operation", mapOf("kind" to "text", "text" to "hello")))
        controller.dispatch(TtsCommand.TransportOpened("socket"))
        controller.dispatch(TtsCommand.ServerFrame("socket", mapOf("protocol" to 1, "type" to "opened_session",
            "session_id" to "session", "resume_secret" to "secret", "ack_seq" to 1)))
        return controller
    }
    private fun fail(controller: TtsController, code: String) {
        val reading = controller.snapshot().playback
        controller.dispatch(TtsCommand.ServerFrame("socket", mapOf("protocol" to 1, "type" to "error", "code" to code,
            "message" to "Original English failure", "ui_message" to metadata,
            "reading_id" to reading.readingId, "generation" to reading.generation)))
    }

    @Test fun preparationFailurePreservesEnglishAndMetadataInBothOutcomesAndSnapshot() {
        val controller = controller()
        fail(controller, "failed")
        val result = controller.snapshot().results.single()
        assertEquals("Original English failure", result.start["error"])
        assertEquals(metadata, result.start["ui_message"])
        assertEquals(metadata, result.ended["ui_message"])
        assertEquals(metadata, controller.snapshot().error!!["ui_message"])
        assertEquals("failed", controller.snapshot().error!!["code"])
    }

    @Test fun terminalFailuresPreserveMetadataWhenCancellingReading() {
        for (code in listOf("unauthorized", "session_lost")) {
            val controller = controller()
            fail(controller, code)
            assertEquals(metadata, controller.snapshot().results.single().ended["ui_message"])
            assertEquals(metadata, controller.snapshot().error!!["ui_message"])
            assertEquals(code, controller.snapshot().error!!["code"])
        }
    }

    @Test fun listeningFailurePreservesMetadataInSnapshot() {
        val controller = controller(listening = true)
        fail(controller, "scope_unavailable")
        assertNull(controller.snapshot().listening)
        assertEquals(metadata, controller.snapshot().error!!["ui_message"])
    }

    @Test fun audioEndingPreservesMetadataAfterPlaybackStarts() {
        val controller = controller()
        val reading = controller.snapshot().playback
        controller.dispatch(TtsCommand.PlayerFeedback(reading.readingId!!, reading.generation, PlayerSignal.Started))
        controller.dispatch(TtsCommand.PlayerFeedback(reading.readingId, reading.generation,
            PlayerSignal.Ended(false, "Original English failure", metadata)))
        val result = controller.snapshot().results.single()
        assertEquals(mapOf("ok" to true), result.start)
        assertEquals("Original English failure", result.ended["error"])
        assertEquals(metadata, result.ended["ui_message"])
    }
    @Test fun nativeAuthorizationAndOutageFailuresRetainIdentity() {
        val closed = controller()
        val descriptor = mapOf("key" to "android.speech.errors.instanceClosed", "params" to emptyMap<String, Any?>())
        closed.dispatch(TtsCommand.AuthorizationLost("Original fallback", descriptor))
        assertEquals(descriptor, closed.snapshot().error?.get("ui_message"))
        assertEquals(descriptor, closed.snapshot().results.single().start["ui_message"])
        assertEquals(descriptor, closed.snapshot().results.single().ended["ui_message"])
        val offline = controller()
        offline.dispatch(TtsCommand.TransportLost("socket"))
        offline.dispatch(TtsCommand.Manual(owner, "offline", mapOf("kind" to "text", "text" to "Unchanged")))
        assertEquals("session.speech.errors.unreachable", (offline.snapshot().error?.get("ui_message") as? Map<*, *>)?.get("key"))
        assertNotNull(offline.snapshot().results.last().start["ui_message"])
        assertNotNull(offline.snapshot().results.last().ended["ui_message"])
    }

    @Test fun unstructuredBackendDiagnosticRemainsVerbatim() {
        val controller = controller()
        controller.dispatch(TtsCommand.ServerFrame("socket", mapOf("protocol" to 1, "type" to "error", "code" to "failed",
            "message" to "External diagnostic: status 9876")))
        assertEquals("External diagnostic: status 9876", controller.snapshot().error?.get("message"))
        assertNull(controller.snapshot().error?.get("ui_message"))
    }

}

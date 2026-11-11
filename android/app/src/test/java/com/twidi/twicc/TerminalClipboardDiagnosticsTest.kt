package com.twidi.twicc

import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test

class TerminalClipboardDiagnosticsTest {
    private fun diagnostic(index: Int) = JSONObject().put("operation", "paste").put("trigger", "button")
        .put("method", "readText").put("errorName", "NotAllowedError").put("errorMessage", "Denied $index")

    @Test fun keepsOnlyRecentErrorsAndExcludesPayloadFields() {
        val journal = TerminalClipboardDiagnostics()
        repeat(25) { journal.record(diagnostic(it).put("clipboardText", "private command")) }
        val report = journal.report(JSONObject().put("appVersion", "test"))
        val parsed = JSONObject(report)
        val errors = parsed.getJSONArray("errors")
        assertEquals(20, errors.length())
        assertEquals("Denied 5", errors.getJSONObject(0).getString("errorMessage"))
        assertEquals("Denied 24", errors.getJSONObject(19).getString("errorMessage"))
        assertFalse(report.contains("private command"))
        assertEquals("test", parsed.getJSONObject("environment").getString("appVersion"))
    }

    @Test fun boundsStringsAndReportsAnEmptyJournal() {
        val journal = TerminalClipboardDiagnostics()
        assertEquals(0, JSONObject(journal.report(JSONObject())).getJSONArray("errors").length())
        journal.record(diagnostic(0).put("errorMessage", "x".repeat(10000)))
        val error = JSONObject(journal.report(JSONObject())).getJSONArray("errors").getJSONObject(0)
        assertTrue(error.getString("errorMessage").length <= 512)
        assertTrue(error.has("recordedAt"))
    }

    @Test fun bridgeAcceptsDiagnosticsWithoutAddingThemToState() {
        val journal = TerminalClipboardDiagnostics()
        val bridge = AndroidAppBridge({ JSONObject() }, { _, _ -> }, { _, _ -> null }, terminalClipboardDiagnostic = journal::record)
        val request = JSONObject().put("protocol", 1).put("id", "error-1").put("command", "terminalClipboard.record")
            .put("diagnostic", diagnostic(0))
        assertTrue(JSONObject(bridge.receive(request.toString())).getBoolean("ok"))
        val state = JSONObject(bridge.receive("""{"protocol":1,"id":"state","command":"getState"}"""))
        assertFalse(state.toString().contains("Denied"))
        assertEquals(1, JSONObject(journal.report(JSONObject())).getJSONArray("errors").length())
    }
}

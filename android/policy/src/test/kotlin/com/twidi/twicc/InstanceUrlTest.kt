package com.twidi.twicc

import org.junit.Assert.*
import org.junit.Test

class InstanceUrlTest {
    @Test fun normalizesOriginAndDefaultPort() {
        assertEquals("https://example.com", InstanceUrl.parse(" HTTPS://Example.COM:443/ ").origin)
        assertEquals("https://example.com:8443", InstanceUrl.parse("https://example.com:8443").origin)
        assertEquals("https://[::1]:8443", InstanceUrl.parse("https://[::1]:8443/").origin)
    }

    @Test fun rejectsNonServerAddresses() {
        listOf(
            "http://example.com", "example.com", "https:///missing", "https://user:pass@example.com",
            "https://example.com/login", "https://example.com/?token=x", "https://example.com/#x",
            "https://example.com:0", "https://example.com:65536", "https://example.com\\@evil.test",
            "javascript:alert(1)", "https://example.com/%2f", "https://example.com:",
        ).forEach { input ->
            try {
                InstanceUrl.parse(input)
                fail("Accepted $input")
            } catch (_: IllegalArgumentException) { }
        }
    }

    @Test fun onlySameHttpsOriginStaysInTheShell() {
        val instance = InstanceUrl.parse("https://twicc.example.com")
        listOf("https://twicc.example.com/session/abc?tab=1#end", "https://TWICC.EXAMPLE.COM:443/")
            .forEach { assertTrue(it, instance.allowsNavigation(it)) }
        listOf(
            "http://twicc.example.com/", "https://twicc.example.com:8443/", "https://other.example.com/",
            "https://twicc.example.com.evil.test/", "https://twicc.example.com@evil.test/",
            "https://user@twicc.example.com/", "javascript:alert(1)", "/relative", "garbage",
        ).forEach { assertFalse(it, instance.allowsNavigation(it)) }
    }

    @Test fun onlySupportedExternalSchemesOpenOtherApps() {
        listOf("https://example.com/", "http://example.com/", "mailto:a@example.com", "tel:+33123456789")
            .forEach { assertTrue(it, InstanceUrl.canOpenExternally(it)) }
        listOf("intent://example.com", "file:///data/private", "javascript:alert(1)", "data:text/html,x", "https://user:pass@example.com", "mailto:")
            .forEach { assertFalse(it, InstanceUrl.canOpenExternally(it)) }
    }
}

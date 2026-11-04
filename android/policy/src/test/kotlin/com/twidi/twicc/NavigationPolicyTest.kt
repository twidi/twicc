package com.twidi.twicc

import org.junit.Assert.assertEquals
import org.junit.Test

class NavigationPolicyTest {
    private val instance = InstanceUrl.parse("https://twicc.example.com:8443")
    private fun decision(url: String, main: Boolean = true, popup: Boolean = false, gesture: Boolean = false) =
        NavigationPolicy.decide(instance, NavigationRequest(url, main, popup, gesture))

    @Test fun internalRoutesStayInsideAndOtherOriginsOpenExternally() {
        assertEquals(NavigationDecision.ALLOW, decision("https://twicc.example.com:8443/session/abc?tab=1"))
        assertEquals(NavigationDecision.OPEN_EXTERNAL, decision("https://twicc.example.com/session/abc"))
        assertEquals(NavigationDecision.OPEN_EXTERNAL, decision("https://other.example.com/"))
        assertEquals(NavigationDecision.OPEN_EXTERNAL, decision("mailto:a@example.com"))
    }
    @Test fun iframeNavigationRemainsEngineManaged() {
        assertEquals(NavigationDecision.ALLOW, decision("https://other.example.com/", main = false))
        assertEquals(NavigationDecision.ALLOW, decision("about:srcdoc", main = false))
    }
    @Test fun onlyUserInitiatedNewWindowsOpenExternally() {
        val internal = "https://twicc.example.com:8443/"
        assertEquals(NavigationDecision.OPEN_EXTERNAL, decision(internal, popup = true, gesture = true))
        assertEquals(NavigationDecision.BLOCK, decision(internal, popup = true))
        assertEquals(NavigationDecision.BLOCK, decision("https://other.example.com/", popup = true))
    }
    @Test fun unsafeTopLevelSchemesAndCredentialsAreBlocked() {
        listOf("javascript:alert(1)", "file:///private", "data:text/html,x", "intent://x", "https://user:pass@example.com/")
            .forEach { assertEquals(it, NavigationDecision.BLOCK, decision(it)) }
    }
}

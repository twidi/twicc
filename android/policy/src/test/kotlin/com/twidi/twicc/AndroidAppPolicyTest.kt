package com.twidi.twicc

import org.junit.Assert.*
import org.junit.Test

class AndroidAppPolicyTest {
    @Test fun onlyExplicitAppCommandsAreAccepted() {
        val commands = listOf("getState", "menuVisibility", "switchInstance", "manageInstances", "reload", "forceRefresh", "serverHome", "openInBrowser")
        commands.forEach { assertNotNull(AppCommand.fromWire(it)) }
        listOf("eval", "loadUrl", "deleteProfile", "GETSTATE", "", "openInBrowser ", "locale.get", "locale.set").forEach { assertNull(AppCommand.fromWire(it)) }
    }
    @Test fun onlySavedOriginTopLevelFramesCanSendCommands() {
        val instance = InstanceUrl.parse("https://twicc.example:8443")
        assertTrue(AndroidAppPolicy.acceptsFrame(instance, "https://twicc.example:8443/page", true))
        assertFalse(AndroidAppPolicy.acceptsFrame(instance, "https://twicc.example:8443/page", false))
        assertFalse(AndroidAppPolicy.acceptsFrame(instance, "https://twicc.example/page", true))
        assertFalse(AndroidAppPolicy.acceptsFrame(instance, "https://other.example/", true))
        assertFalse(AndroidAppPolicy.acceptsFrame(instance, "about:blank", true))
    }
    @Test fun menuLeaseExpiresAndAnOldOwnerCannotHideTheNewMenu() {
        val lease = AppMenuLease()
        assertFalse(lease.isVisible(0))
        lease.report("old", true, 10)
        assertTrue(lease.isVisible(6009))
        assertFalse(lease.isVisible(6010))
        lease.report("new", true, 6020)
        lease.report("old", false, 6030)
        assertTrue(lease.isVisible(6030))
        lease.report("new", false, 6040)
        assertFalse(lease.isVisible(6040))
        lease.report("new", true, 6050)
        lease.reset()
        assertFalse(lease.isVisible(6050))
    }
    @Test fun anOldOwnerReportDoesNotExtendTheFallbackDeadline() {
        val lease = AppMenuLease()
        lease.report("new", true, 0)
        lease.report("old", false, 5900)
        assertEquals(100L, lease.remainingMs(5900))
        assertEquals(0L, lease.remainingMs(6000))
        assertFalse(lease.isVisible(6000))
    }
}

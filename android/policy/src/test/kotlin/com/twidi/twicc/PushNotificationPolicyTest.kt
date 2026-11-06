package com.twidi.twicc

import org.junit.Assert.*
import org.junit.Test

class PushNotificationPolicyTest {
    @Test fun notificationsOfDifferentSessionsAndInstancesKeepSeparateIdentities() {
        assertEquals("twicc-push:one:user_turn:a", PushNotificationPolicy.tag("one", "user_turn", "a"))
        assertNotEquals(PushNotificationPolicy.tag("one", "user_turn", "a"), PushNotificationPolicy.tag("one", "user_turn", "b"))
        assertNotEquals(PushNotificationPolicy.tag("one", "user_turn", "a"), PushNotificationPolicy.tag("two", "user_turn", "a"))
        assertNotEquals(PushNotificationPolicy.tag("one", "user_turn", "a"), PushNotificationPolicy.tag("one", "pending_request", "a"))
        assertEquals("twicc-push:one:peer", PushNotificationPolicy.tag("one", "peer", null))
    }

    @Test fun onlyTheDisplayedSessionIsSuppressedUntilItsOwnerLeaves() {
        val policy = PushNotificationPolicy(); val page = Any()
        assertFalse(policy.suppresses("one", "a"))
        policy.show(page, "one", "a")
        assertTrue(policy.suppresses("one", "a"))
        assertFalse(policy.suppresses("one", "b"))
        assertFalse(policy.suppresses("two", "a"))
        assertFalse(policy.suppresses("one", null))
        policy.show(page, "one", "b")
        assertFalse(policy.suppresses("one", "a"))
        assertTrue(policy.suppresses("one", "b"))
        policy.hide(page)
        assertFalse(policy.suppresses("one", "b"))
    }

    @Test fun lateActivityDisposalCannotHideItsSuccessor() {
        val policy = PushNotificationPolicy(); val old = Any(); val next = Any()
        policy.show(old, "one", "a")
        policy.show(next, "two", "a")
        policy.hide(old)
        assertTrue(policy.suppresses("two", "a"))
        policy.show(next, "two", null)
        assertFalse(policy.suppresses("two", "a"))
    }

    @Test fun nativeLocationsRecognizeAliasesAndSessionTabsWithoutRelaxingPushDestinations() {
        for (url in listOf("https://twicc.test/project/p/session/a", "https://twicc.test/projects/p/session/a/files/root/file?workspace=w",
            "https://twicc.test/project/p/session/a/orchestration", "https://twicc.test/project/p/session/a/subagent/worker")) {
            assertEquals(url, "a", SessionLink.fromLocation(url)?.sessionId)
        }
        for (url in listOf("https://twicc.test/login", "https://twicc.test/projects", "https://twicc.test/project/p",
            "https://twicc.test/project/p/session/a-not/../a", "not a URL", "https://twicc.test/project/p/session/%61")) {
            assertNull(url, SessionLink.fromLocation(url))
        }
        assertNull(SessionLink.parse("/project/p/session/a/files/root/file"))
    }
}

package com.twidi.twicc

import org.junit.Assert.*
import org.junit.Test

class NotificationNavigationTest {
    @Test fun dialogActionsSurviveEvenWhenTheSessionIsAlreadyOpen() {
        val navigation = NotificationNavigation()
        navigation.open("tap", "/project/p/session/s", "https://twicc.example/project/p/session/s", "peerInbox", "42")
        assertEquals("peerInbox", navigation.pending?.action)
        assertEquals("42", navigation.pending?.detail)
        navigation.open("home", null, "https://twicc.example/", "home")
        assertEquals("home", navigation.pending?.action)
    }
    @Test fun onlySessionPathsInsideTheSelectedInstanceAreAccepted() {
        assertEquals("one", SessionLink.parse("/project/project-one/session/one")!!.sessionId)
        assertEquals("project-one", SessionLink.parse("/projects/project-one/session/one")!!.projectId)
        for (path in listOf("https://other.example/project/a/session/b", "//other.example/project/a/session/b",
            "/login", "/logout", "/project/a/session/b?redirect=evil", "/project/a/session/b#fragment",
            "/project/a/session/b/files", "/project/../session/b", "/project/%2f/session/b", "")) {
            assertNull(path, SessionLink.parse(path))
        }
    }

    @Test fun sameSessionKeepsItsCurrentTabAndWorkspaceWithoutNavigation() {
        val navigation = NotificationNavigation()
        navigation.open("tap", "/project/p/session/s", "https://twicc.example/projects/p/session/s/files/root/file?workspace=w")
        assertNull(navigation.pending)
        navigation.open("other-project", "/project/actual/session/s", "https://twicc.example/projects/filter/session/s/files/root/file?workspace=w")
        assertNull("The session ID identifies the open session independently of the current project filter", navigation.pending)
        navigation.open("instance-only", null, "https://twicc.example/project/p")
        assertNull(navigation.pending)
    }

    @Test fun onlyTheLatestUnconsumedNavigationSurvivesAndStaleAcknowledgementCannotRemoveIt() {
        val navigation = NotificationNavigation()
        navigation.open("first", "/project/p/session/one", "https://twicc.example/")
        navigation.open("second", "/project/p/session/two", "https://twicc.example/")
        navigation.acknowledge("first")
        assertEquals("second", navigation.pending!!.id)
        assertEquals("/project/p/session/two", navigation.pending!!.path)
        navigation.acknowledge("second")
        assertNull(navigation.pending)
    }
}

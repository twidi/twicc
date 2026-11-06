package com.twidi.twicc

import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test

class AndroidPushBridgeTest {
    @Test fun existingMenuHeartbeatDeliversThePendingSessionWithoutAnExtraPollingLoop() {
        val bridge = AndroidAppBridge({ JSONObject().put("navigation", JSONObject().put("id", "tap").put("path", "/project/p/session/s")) },
            { _, _ -> }, { _, _ -> null })
        val reply = JSONObject(bridge.receive("""{"protocol":1,"id":"heartbeat","command":"menuVisibility","owner":"menu","visible":true}"""))
        assertEquals("/project/p/session/s", reply.getJSONObject("state").getJSONObject("navigation").getString("path"))
    }

    @Test fun onlyExplicitPushCommandsReachThePushHandler() {
        val received = mutableListOf<String>()
        val bridge = AndroidAppBridge({ JSONObject() }, { _, _ -> }, { _, _ -> null }, androidPush = { command, request ->
            received += command
            assertEquals("key", request.getString("vapid"))
            JSONObject().put("status", "registering")
        })
        for (command in listOf("push.configure", "push.state", "push.preferences", "push.permission", "push.remove", "push.unknown")) {
            val result = JSONObject(bridge.receive(JSONObject().put("protocol", 1).put("id", "request")
                .put("command", command).put("vapid", "key").toString()))
            assertEquals(command != "push.unknown", result.getBoolean("ok"))
            if (result.getBoolean("ok")) assertEquals("registering", result.getJSONObject("state").getString("status"))
        }
        assertEquals(listOf("push.configure", "push.state", "push.preferences", "push.permission", "push.remove"), received)
    }

    @Test fun probeCannotUseTheLargeSpeechFrameAllowance() {
        val request = JSONObject().put("protocol", 1).put("id", "request").put("command", "push.configure")
            .put("padding", "x".repeat(17 * 1024))
        assertNotNull(AndroidAppBridge.frameError(request.toString()))
    }
}

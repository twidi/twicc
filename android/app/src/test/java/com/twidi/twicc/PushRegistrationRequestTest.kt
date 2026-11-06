package com.twidi.twicc

import com.twidi.twicc.push.PushRegistrationRequest
import okio.Buffer
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test

class PushRegistrationRequestTest {
    @Test fun renewalSendsOnlyItsOwnGrantAndChoicesWhileRemovalRetainsNoSubscription() {
        val choices = JSONObject().put("user_turn", true).put("pending_request", false).put("extra_usage", false).put("peer", false)
        val state = JSONObject().put("address", "https://twicc.example:8443").put("token", "grant")
            .put("enabled", false).put("preferences", choices).put("subscription", JSONObject().put("endpoint", "fcm"))
        val request = PushRegistrationRequest.build("device", state)
        assertEquals("https://twicc.example:8443/api/auth/android-push/", request.url.toString())
        assertEquals("Bearer grant", request.header("Authorization"))
        assertNull(request.header("Cookie"))
        assertEquals("PUT", request.method)
        val buffer = Buffer()
        request.body!!.writeTo(buffer)
        val body = JSONObject(buffer.readUtf8())
        assertEquals("device", body.getString("device_id"))
        assertFalse(body.getBoolean("enabled"))
        assertTrue(body.getJSONObject("preferences").getBoolean("user_turn"))
        val deleted = PushRegistrationRequest.build("device", state.put("deleted", true))
        assertEquals("DELETE", deleted.method)
        deleted.body!!.writeTo(buffer)
        assertEquals(setOf("device_id"), JSONObject(buffer.readUtf8()).keys().asSequence().toSet())
    }
}

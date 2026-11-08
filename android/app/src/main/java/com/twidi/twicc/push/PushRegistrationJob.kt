package com.twidi.twicc.push

import android.app.job.JobParameters
import android.app.job.JobService
import android.os.Handler
import android.os.Looper
import okhttp3.Call
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import okhttp3.MediaType.Companion.toMediaType
import org.json.JSONObject
import java.util.concurrent.TimeUnit

/** Finite network work. Android retries endpoint changes without an open browser or a foreground service. */
class PushRegistrationJob : JobService() {
    private class Run { @Volatile var stopped = false; @Volatile var call: Call? = null }
    private var current: Run? = null
    override fun onStartJob(params: JobParameters): Boolean {
        val run = Run()
        current = run
        Thread({
            var retry = false
            for ((id, snapshot) in AndroidPush.pending(this)) {
                if (run.stopped) break
                try {
                    if (!snapshot.optBoolean("deleted") && (snapshot.optBoolean("needsEndpoint") || !snapshot.has("subscription"))) {
                        retry = AndroidPush.register(this, id, snapshot.getString("vapid")) || retry
                        continue
                    }
                    val request = PushRegistrationRequest.build(id, snapshot)
                    val call = client.newCall(request)
                    run.call = call
                    if (run.stopped) { call.cancel(); break }
                    call.execute().use { response ->
                        val again = response.code == 429 || response.code >= 500
                        retry = retry || again
                        val status = when { response.isSuccessful -> "ready"; response.code == 401 -> "login"; again -> "retrying"; else -> "error" }
                        // Old-server success ends retries without confirming payload compatibility.
                        val payloadVersion = if (response.isSuccessful && !snapshot.optBoolean("deleted"))
                            PushRegistrationRequest.confirmedPayloadVersion(response.body?.source()?.let { source ->
                                if (source.request(8193)) null else source.readUtf8()
                            }) else null
                        AndroidPush.complete(this, id, snapshot, status, again, payloadVersion)
                    }
                } catch (_: Exception) {
                    retry = true
                    if (!run.stopped) AndroidPush.complete(this, id, snapshot, "retrying", true)
                }
            }
            Handler(Looper.getMainLooper()).post {
                if (current === run && !run.stopped) {
                    current = null
                    jobFinished(params, retry || AndroidPush.pending(this).isNotEmpty())
                }
            }
        }, "TwiCC push registration").start()
        return true
    }
    override fun onStopJob(params: JobParameters): Boolean {
        current?.let { it.stopped = true; it.call?.cancel() }
        current = null
        return true
    }
    companion object {
        private val client = OkHttpClient.Builder().followRedirects(false).followSslRedirects(false)
            .connectTimeout(15, TimeUnit.SECONDS).readTimeout(15, TimeUnit.SECONDS).callTimeout(30, TimeUnit.SECONDS).build()
    }
}

internal object PushRegistrationRequest {
    fun confirmedPayloadVersion(body: String?): Int? = runCatching {
        val version = JSONObject(body ?: return@runCatching null).opt("notification_payload_version")
        if (version is Int && version == 2) 2 else null
    }.getOrNull()

    fun build(id: String, value: JSONObject): Request {
        val address = com.twidi.twicc.InstanceUrl.parse(value.getString("address")).origin
        val body = JSONObject().put("device_id", id)
        val deleted = value.optBoolean("deleted")
        if (!deleted) body.put("subscription", value.getJSONObject("subscription"))
            .put("enabled", value.getBoolean("enabled")).put("preferences", value.getJSONObject("preferences"))
            .put("notification_payload_version", 2)
        return Request.Builder().url("$address/api/auth/android-push/")
            .header("Authorization", "Bearer ${value.getString("token")}")
            .method(if (deleted) "DELETE" else "PUT", body.toString().toRequestBody("application/json".toMediaType())).build()
    }
}

package com.twidi.twicc.push

import android.Manifest
import android.app.Activity
import android.app.KeyguardManager
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.job.JobInfo
import android.app.job.JobScheduler
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import android.os.PowerManager
import android.provider.Settings
import android.util.Base64
import com.twidi.twicc.Instance
import com.twidi.twicc.PushNotificationPolicy
import com.twidi.twicc.SessionLink
import org.json.JSONObject
import org.unifiedpush.android.connector.UnifiedPush
import java.util.UUID

/** Private registration state survives suspended pages and process restarts. Never expose the grant to page reads. */
object AndroidPush {
    const val ACTION_OPEN = "com.twidi.twicc.push.OPEN"
    const val EXTRA_PATH = "pushSessionPath"
    const val EXTRA_NAVIGATION_ID = "pushNavigationId"
    const val EXTRA_ACTION = "pushAction"
    const val EXTRA_DETAIL = "pushDetail"
    private const val JOB_ID = 820
    private val visibleSession = PushNotificationPolicy()

    private fun screenAvailable(context: Context): Boolean =
        context.getSystemService(PowerManager::class.java).isInteractive &&
            !context.getSystemService(KeyguardManager::class.java).isKeyguardLocked

    /** Location and lifecycle callbacks share the same lock as posting, so a visible session cannot race a push. */
    @Synchronized fun showSession(context: Context, owner: Any, instance: String, url: String) {
        if (!screenAvailable(context)) { visibleSession.hide(owner); return }
        val sessionId = SessionLink.fromLocation(url)?.sessionId
        visibleSession.show(owner, instance, sessionId)
        if (sessionId != null) manager(context).let { notifications ->
            PushPayload.kinds.forEach { notifications.cancel(PushNotificationPolicy.tag(instance, it, sessionId), 1) }
        }
    }

    @Synchronized fun hideSession(owner: Any) { visibleSession.hide(owner) }

    @Synchronized fun postNotification(context: Context, instance: String, payload: PushPayload, notification: Notification) {
        if (screenAvailable(context) && visibleSession.suppresses(instance, payload.sessionId)) return
        manager(context).notify(PushNotificationPolicy.tag(instance, payload.kind, payload.sessionId), 1, notification)
    }
    private fun preferences(context: Context) = context.getSharedPreferences("android-push", Context.MODE_PRIVATE)
    @Synchronized fun read(context: Context, id: String): JSONObject = runCatching {
        JSONObject(preferences(context).getString(id, "{}")!!)
    }.getOrElse { JSONObject() }
    @Synchronized fun update(context: Context, id: String, change: (JSONObject) -> Unit): JSONObject {
        val value = read(context, id)
        change(value)
        value.put("revision", UUID.randomUUID().toString())
        preferences(context).edit().putString(id, value.toString()).commit()
        return value
    }
    @Synchronized fun pending(context: Context): Map<String, JSONObject> = preferences(context).all.keys
        .associateWith { read(context, it) }.filterValues { it.optBoolean("dirty") && it.optString("token").isNotEmpty() }
    @Synchronized fun complete(context: Context, id: String, snapshot: JSONObject, status: String, retry: Boolean) {
        if (read(context, id).optString("revision") != snapshot.optString("revision")) return
        if (snapshot.optBoolean("deleted") && !retry) preferences(context).edit().remove(id).commit()
        else update(context, id) { it.put("dirty", retry).put("status", status) }
    }
    fun schedule(context: Context, delayMillis: Long = 0) {
        context.getSystemService(JobScheduler::class.java).schedule(JobInfo.Builder(JOB_ID,
            ComponentName(context, PushRegistrationJob::class.java)).setRequiredNetworkType(JobInfo.NETWORK_TYPE_ANY)
            .setBackoffCriteria(30_000, JobInfo.BACKOFF_POLICY_EXPONENTIAL).setMinimumLatency(delayMillis).setPersisted(true).build())
    }
    fun channel(kind: String) = "twicc-$kind"
    fun manager(context: Context): NotificationManager = context.getSystemService(NotificationManager::class.java).apply {
        mapOf("user_turn" to "Finished work", "pending_request" to "Questions and approvals",
            "extra_usage" to "Extra usage", "peer" to "Peer messages and requests").forEach { (kind, label) ->
            createNotificationChannel(NotificationChannel(channel(kind), label, NotificationManager.IMPORTANCE_DEFAULT))
        }
        // Remove the temporary channel from installations that ran the transport probe.
        deleteNotificationChannel("push-probe")
        val legacy = context.getSharedPreferences("push-probe", Context.MODE_PRIVATE)
        if (legacy.all.isNotEmpty()) legacy.edit().clear().commit()
    }
    fun allowed(context: Context): Boolean = manager(context).areNotificationsEnabled() &&
        (Build.VERSION.SDK_INT < 33 || context.checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) == PackageManager.PERMISSION_GRANTED)
    private fun permission(context: Context): String = when {
        allowed(context) -> "granted"
        Build.VERSION.SDK_INT >= 33 && !preferences(context).getBoolean("permissionAsked", false) -> "default"
        else -> "denied"
    }
    fun state(context: Context, instance: Instance): JSONObject {
        val enabled = allowed(context)
        val value = read(context, instance.id)
        if (value.has("token") && !value.optBoolean("deleted") && value.optBoolean("enabled") != enabled) {
            update(context, instance.id) { it.put("enabled", enabled).put("dirty", true) }
            schedule(context)
        }
        return JSONObject().put("device_id", instance.id).put("name", "${Build.MODEL} · ${instance.name}".take(160))
            .put("permission", permission(context)).put("status", value.optString("status", "idle"))
    }
    fun choices(value: JSONObject): JSONObject {
        require(value.keys().asSequence().toSet() == PushPayload.kinds && value.keys().asSequence().toSet().all { value.opt(it) is Boolean })
        return JSONObject(value.toString())
    }
    fun register(context: Context, id: String, vapid: String): Boolean {
        val value = read(context, id)
        if (value.optBoolean("deleted") || !value.has("token") || com.twidi.twicc.InstanceStore(context).find(id) == null) return false
        if (context.packageName !in UnifiedPush.getDistributors(context)) {
            update(context, id) { it.put("status", "unavailable").put("dirty", false) }
            return false
        }
        UnifiedPush.saveDistributor(context, context.packageName)
        UnifiedPush.register(context, instance = id, messageForDistributor = "TwiCC notifications", vapid = vapid)
        return true
    }
    fun remove(context: Context, instance: String) {
        val value = update(context, instance) { it.put("deleted", true).put("dirty", it.has("token")).remove("preferences") }
        UnifiedPush.unregister(context, instance)
        manager(context).let { notifications -> notifications.activeNotifications
            .filter { it.tag?.startsWith("twicc-push:$instance:") == true }
            .forEach { notifications.cancel(it.tag, it.id) } }
        if (value.has("token")) schedule(context) else preferences(context).edit().remove(instance).commit()
    }
    fun receive(activity: Activity, instance: Instance, command: String, request: JSONObject): JSONObject {
        when (command) {
            "push.state" -> Unit
            "push.permission" -> {
                if (permission(activity) == "default" && Build.VERSION.SDK_INT >= 33) {
                    preferences(activity).edit().putBoolean("permissionAsked", true).commit()
                    activity.requestPermissions(arrayOf(Manifest.permission.POST_NOTIFICATIONS), 810)
                } else if (!allowed(activity)) {
                    activity.startActivity(Intent(Settings.ACTION_APP_NOTIFICATION_SETTINGS).putExtra(Settings.EXTRA_APP_PACKAGE, activity.packageName))
                }
            }
            "push.preferences" -> {
                val choices = choices(request.getJSONObject("preferences"))
                val value = update(activity, instance.id) { it.put("preferences", choices).put("enabled", allowed(activity))
                    if (it.has("token") && !it.optBoolean("deleted")) it.put("dirty", true) }
                if (value.optBoolean("dirty")) schedule(activity)
            }
            "push.configure" -> {
                val vapid = request.getString("vapid")
                val key = Base64.decode(vapid, Base64.URL_SAFE or Base64.NO_PADDING or Base64.NO_WRAP)
                require(Regex("[A-Za-z0-9_-]{87}").matches(vapid) && key.size == 65 && key[0] == 4.toByte())
                val token = request.getString("token").also { require(it.matches(Regex("[A-Za-z0-9_-]{40,100}"))) }
                val choices = choices(request.getJSONObject("preferences"))
                val old = read(activity, instance.id)
                update(activity, instance.id) {
                    it.put("address", instance.address).put("token", token).put("vapid", vapid).put("preferences", choices)
                        .put("enabled", allowed(activity)).put("deleted", false).put("status", "registering").put("needsEndpoint", true)
                    if (old.optString("vapid") != vapid || old.optBoolean("deleted")) it.remove("subscription")
                    it.put("dirty", true)
                }
                if (register(activity, instance.id, vapid)) schedule(activity, 30_000)

            }
            "push.remove" -> remove(activity, instance.id)
        }
        return state(activity, instance)
    }
}

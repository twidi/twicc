package com.twidi.twicc.push

import android.Manifest
import android.app.Activity
import android.app.Application
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
import android.media.AudioAttributes
import android.os.Build
import android.os.Bundle
import android.os.PowerManager
import android.provider.Settings
import android.util.Base64
import com.twidi.twicc.Instance
import com.twidi.twicc.PushNotificationPolicy
import com.twidi.twicc.SessionLink
import com.twidi.twicc.i18n.NativeMessages
import com.twidi.twicc.i18n.UiMessage
import org.json.JSONObject
import org.unifiedpush.android.connector.UnifiedPush
import java.util.UUID
import java.time.Instant

/** Private registration state survives suspended pages and process restarts. Never expose the grant to page reads. */
object AndroidPush {
    const val ACTION_OPEN = "com.twidi.twicc.push.OPEN"
    const val EXTRA_PATH = "pushSessionPath"
    const val EXTRA_NAVIGATION_ID = "pushNavigationId"
    const val EXTRA_ACTION = "pushAction"
    const val EXTRA_DETAIL = "pushDetail"
    private const val JOB_ID = 820
    private const val SENT_AT = "twicc.push.sentAt"
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
        val cutoffs = dismissalPreferences(context)
        if (payload.sessionId != null && payload.sentAt.toEpochMilli() <=
            cutoffs.getLong("$instance:${payload.sessionId}", Long.MIN_VALUE)) return
        notification.extras.putLong(SENT_AT, payload.sentAt.toEpochMilli())
        manager(context).notify(PushNotificationPolicy.tag(instance, payload.kind, payload.sessionId), 1, notification)
    }

    private fun dismissalPreferences(context: Context) =
        context.getSharedPreferences("android-push-dismissals", Context.MODE_PRIVATE).also { preferences ->
            // Keep only the push validity window. This is not a session or notification history.
            val oldest = Instant.now().minusSeconds(600).toEpochMilli()
            val expired = preferences.all.filterValues { it !is Long || it < oldest }.keys
            if (expired.isNotEmpty()) preferences.edit().apply { expired.forEach { remove(it) } }.commit()
        }

    @Synchronized fun dismissSessionNotifications(context: Context, instance: String, payload: PushSessionDismissal) {
        val preferences = dismissalPreferences(context)
        val cutoffs = payload.sessionIds.associateWith { session ->
            maxOf(payload.sentAt.toEpochMilli(), preferences.getLong("$instance:$session", Long.MIN_VALUE))
        }
        preferences.edit().apply { cutoffs.forEach { (session, cutoff) -> putLong("$instance:$session", cutoff) } }.commit()
        val tags = cutoffs.flatMap { (session, cutoff) ->
            PushPayload.kinds.map { kind -> PushNotificationPolicy.tag(instance, kind, session) to cutoff }
        }.toMap()
        manager(context).let { notifications -> notifications.activeNotifications.forEach { active ->
            val cutoff = tags[active.tag] ?: return@forEach
            // A delayed dismissal must not erase newer work after a session becomes visible again.
            if (active.notification.extras.getLong(SENT_AT, Long.MIN_VALUE) <= cutoff) notifications.cancel(active.tag, active.id)
        } }
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
    @Synchronized fun complete(context: Context, id: String, snapshot: JSONObject, status: String, retry: Boolean,
        payloadVersion: Int? = null) {
        if (read(context, id).optString("revision") != snapshot.optString("revision")) return
        if (snapshot.optBoolean("deleted") && !retry) preferences(context).edit().remove(id).commit()
        else update(context, id) {
            it.put("dirty", retry).put("status", status)
            if (status == "ready" && payloadVersion == 2) it.put("notification_payload_version", 2)
        }
    }

    private val reconciled = mutableSetOf<String>()
    private var observingForeground = false

    /** Each foreground session makes one finite attempt for every unconfirmed saved grant. */
    @Synchronized fun reconcilePayloadSupport(context: Context) {
        if (!observingForeground) {
            observingForeground = true
            (context.applicationContext as Application).registerActivityLifecycleCallbacks(object : Application.ActivityLifecycleCallbacks {
                private val started = mutableSetOf<Activity>()
                override fun onActivityStarted(activity: Activity) { started.add(activity) }
                override fun onActivityStopped(activity: Activity) {
                    started.remove(activity)
                    if (started.isEmpty() && !activity.isChangingConfigurations) synchronized(AndroidPush) { reconciled.clear() }
                }
                override fun onActivityCreated(activity: Activity, state: Bundle?) {}
                override fun onActivityResumed(activity: Activity) {}
                override fun onActivityPaused(activity: Activity) {}
                override fun onActivitySaveInstanceState(activity: Activity, state: Bundle) {}
                override fun onActivityDestroyed(activity: Activity) {}
            })
        }
        val store = com.twidi.twicc.InstanceStore(context)
        var pending = false
        for (id in preferences(context).all.keys) {
            val value = read(context, id)
            if (value.optString("token").isEmpty() || value.optBoolean("deleted") ||
                value.optString("status") == "login" || value.optInt("notification_payload_version") == 2 || store.find(id) == null) continue
            if (!reconciled.add(id)) continue
            if (!value.optBoolean("dirty")) update(context, id) { it.put("dirty", true).put("enabled", allowed(context)) }
            pending = true
        }
        if (pending) schedule(context, 30_000)
    }
    fun schedule(context: Context, delayMillis: Long = 0) {
        context.getSystemService(JobScheduler::class.java).schedule(JobInfo.Builder(JOB_ID,
            ComponentName(context, PushRegistrationJob::class.java)).setRequiredNetworkType(JobInfo.NETWORK_TYPE_ANY)
            .setBackoffCriteria(30_000, JobInfo.BACKOFF_POLICY_EXPONENTIAL).setMinimumLatency(delayMillis).setPersisted(true).build())
    }
    fun channel(kind: String) = "twicc-$kind"
    private val categories = mapOf(
        "user_turn" to ("android.push.channels.finished" to "chime"),
        "pending_request" to ("android.push.channels.attention" to "signal"),
        "extra_usage" to ("settings.notifications.targets.extraUsage" to "sweep"),
        "peer" to ("android.push.channels.peer" to "ripple"),
    )
    /** Build defaults only for a category that does not exist yet. Never persist over an existing channel. */
    internal fun newNotificationChannel(context: Context, kind: String): NotificationChannel {
        val (key, sound) = categories.getValue(kind)
        return NotificationChannel(channel(kind), NativeMessages.text(context, UiMessage(key)),
            NotificationManager.IMPORTANCE_DEFAULT).apply {
            setSound(NotificationSounds.uri(context, sound), AudioAttributes.Builder()
                .setUsage(AudioAttributes.USAGE_NOTIFICATION).build())
        }
    }
    fun manager(context: Context): NotificationManager = context.getSystemService(NotificationManager::class.java).apply {
        categories.forEach { (kind, definition) ->
            val existing = getNotificationChannel(channel(kind))
            if (existing != null) {
                // Reuse the channel object so its importance and user settings stay intact.
                existing.name = NativeMessages.text(context, UiMessage(definition.first))
                createNotificationChannel(existing)
            } else {
                createNotificationChannel(newNotificationChannel(context, kind))
            }
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
        val status = value.optString("status", "idle").let {
            if (it == "ready" && value.optInt("notification_payload_version") != 2) "update_server" else it
        }
        return JSONObject().put("payloadVersion", 2).put("device_id", instance.id).put("name", "${Build.MODEL} · ${instance.name}".take(160))
            .put("permission", permission(context)).put("status", status).put("soundLibrary", NotificationSounds.state())
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
        UnifiedPush.register(context, instance = id, messageForDistributor = NativeMessages.text(context, UiMessage("android.push.distributorLabel")), vapid = vapid)
        return true
    }
    fun remove(context: Context, instance: String) {
        val value = update(context, instance) { it.put("deleted", true).put("dirty", it.has("token")).remove("preferences") }
        UnifiedPush.unregister(context, instance)
        manager(context).let { notifications -> notifications.activeNotifications
            .filter { it.tag?.startsWith("twicc-push:$instance:") == true }
            .forEach { notifications.cancel(it.tag, it.id) } }
        dismissalPreferences(context).let { preferences -> preferences.edit().apply {
            preferences.all.keys.filter { it.startsWith("$instance:") }.forEach { remove(it) }
        }.commit() }
        if (value.has("token")) schedule(context) else preferences(context).edit().remove(instance).commit()
    }
    fun receive(activity: Activity, instance: Instance, command: String, request: JSONObject): JSONObject {
        when (command) {
            "push.state" -> Unit
            "push.soundSettings" -> NotificationSettingsLauncher.open(activity, request.getString("kind"))
            "push.prepareSounds" -> NotificationSounds.publishAsync(activity)
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
                val payloadVersion = request.opt("payloadVersion")
                require(payloadVersion == null || payloadVersion is Int && payloadVersion == 2)
                val old = read(activity, instance.id)
                update(activity, instance.id) {
                    it.put("address", instance.address).put("token", token).put("vapid", vapid).put("preferences", choices)
                        .put("enabled", allowed(activity)).put("deleted", false).put("status", "registering").put("needsEndpoint", true)
                    if (payloadVersion == 2) it.put("notification_payload_version", 2)
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

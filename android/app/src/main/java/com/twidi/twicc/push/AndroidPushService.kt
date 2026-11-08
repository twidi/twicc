package com.twidi.twicc.push

import android.app.Notification
import android.app.PendingIntent
import android.content.Intent
import android.net.Uri
import android.util.Log
import com.twidi.twicc.InstanceStore
import com.twidi.twicc.BrowserActivity
import com.twidi.twicc.R
import com.twidi.twicc.i18n.NativeMessages
import com.twidi.twicc.i18n.UiMessage
import org.json.JSONObject
import org.unifiedpush.android.connector.FailedReason
import org.unifiedpush.android.connector.PushService
import org.unifiedpush.android.connector.data.PushEndpoint
import org.unifiedpush.android.connector.data.PushMessage

class AndroidPushService : PushService() {
    override fun onNewEndpoint(endpoint: PushEndpoint, instance: String) {
        if (InstanceStore(this).find(instance) == null) return
        val keys = endpoint.pubKeySet ?: return onRegistrationFailed(FailedReason.INTERNAL_ERROR, instance)
        AndroidPush.update(this, instance) { value ->
            if (value.optBoolean("deleted") || !value.has("token")) return@update
            value.put("subscription", JSONObject().put("endpoint", endpoint.url).put("keys",
                JSONObject().put("p256dh", keys.pubKey).put("auth", keys.auth)))
                .put("dirty", true).put("needsEndpoint", false).put("status", "registering")
        }
        AndroidPush.schedule(this)
    }
    override fun onRegistrationFailed(reason: FailedReason, instance: String) {
        if (InstanceStore(this).find(instance) == null) return
        Log.w("TwiCC.Push", "Registration failed: $reason")
        val pending = AndroidPush.update(this, instance) {
            if (!it.optBoolean("deleted") && it.has("token")) it.put("status", "retrying").put("dirty", true).put("needsEndpoint", true)
        }
        if (pending.optBoolean("dirty")) AndroidPush.schedule(this, 30_000)
    }
    override fun onUnregistered(instance: String) { /* Removal owns the state; late callbacks must not erase a new registration. */ }

    override fun onMessage(message: PushMessage, instance: String) {
        val saved = InstanceStore(this).find(instance) ?: return
        if (!message.decrypted) return
        val config = AndroidPush.read(this, instance)
        if (config.optBoolean("deleted")) return
        PushSessionDismissal.parse(message.content)?.let {
            AndroidPush.dismissSessionNotifications(this, instance, it)
            return
        }
        val payload = PushPayload.parse(message.content) ?: return
        if (!config.optJSONObject("preferences").let { it?.optBoolean(payload.kind) == true } || !AndroidPush.allowed(this)) return
        val manager = AndroidPush.manager(this)
        if (manager.getNotificationChannel(AndroidPush.channel(payload.kind)).importance == android.app.NotificationManager.IMPORTANCE_NONE) return
        val open = Intent(this, BrowserActivity::class.java).apply {
            action = AndroidPush.ACTION_OPEN
            data = Uri.parse("twicc-push://open/$instance/${payload.id}")
            putExtra("instance", instance)
            putExtra(AndroidPush.EXTRA_NAVIGATION_ID, payload.id)
            payload.path?.let { putExtra(AndroidPush.EXTRA_PATH, it) }
            payload.action?.let { putExtra(AndroidPush.EXTRA_ACTION, it) }
            payload.detail?.let { putExtra(AndroidPush.EXTRA_DETAIL, it) }
            flags = Intent.FLAG_ACTIVITY_CLEAR_TOP or Intent.FLAG_ACTIVITY_SINGLE_TOP
        }
        val pending = PendingIntent.getActivity(this, 0, open, PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE)
        val renderedTitle = NativeMessages.render(this, payload.title)
        val renderedBody = NativeMessages.render(this, payload.body)
        val generic = renderedTitle == null || renderedBody == null
        val title = if (generic) NativeMessages.text(this, UiMessage("android.push.genericTitle")) else renderedTitle!!
        val body = if (generic) NativeMessages.text(this, UiMessage("android.push.genericBody")) else renderedBody!!
        val notification = Notification.Builder(this, AndroidPush.channel(payload.kind))
            .setSmallIcon(R.drawable.ic_notification)
            .setContentTitle(NativeMessages.text(this, UiMessage("android.push.titleWithInstance",
                mapOf("title" to title, "instance" to saved.name))))
            .setContentText(body).setStyle(Notification.BigTextStyle().bigText(body))
            .setContentIntent(pending).setAutoCancel(true).build()
        try { AndroidPush.postNotification(this, instance, payload, notification) }
        catch (_: SecurityException) { Log.w("TwiCC.Push", "Notifications are blocked") }
    }
}

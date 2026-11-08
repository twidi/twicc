package com.twidi.twicc.tts

import android.app.Activity
import android.app.AppOpsManager
import android.app.AlertDialog
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.os.PowerManager
import android.provider.Settings
import com.twidi.twicc.BrowserActivity
import com.twidi.twicc.R
import com.twidi.twicc.i18n.NativeMessages
import com.twidi.twicc.i18n.UiMessage
import com.twidi.twicc.i18n.UiMessageJson
import org.json.JSONObject

/** Control URIs identify the session and listening activation. Extras do not distinguish PendingIntents. */
class TtsNotification(private val context: Context) {
    private val manager = context.getSystemService(NotificationManager::class.java)
    init { refreshChannel() }
    private fun refreshChannel() {
        val channel = manager.getNotificationChannel(CHANNEL)
            ?: NotificationChannel(CHANNEL, NativeMessages.text(context, UiMessage("android.speech.channel")), NotificationManager.IMPORTANCE_LOW)
        // Reuse the channel and update only its label. Keep Android user settings.
        channel.name = NativeMessages.text(context, UiMessage("android.speech.channel"))
        manager.createNotificationChannel(channel)
    }
    fun error(): TtsActionError? {
        val permission = Build.VERSION.SDK_INT < 33 || context.checkSelfPermission(android.Manifest.permission.POST_NOTIFICATIONS) == PackageManager.PERMISSION_GRANTED
        // Android's public permissionToOp omits this notification operation on API 35.
        val appOp = context.getSystemService(AppOpsManager::class.java).unsafeCheckOpNoThrow(
            "android:post_notification", android.os.Process.myUid(), context.packageName)
        return if (!permission || appOp !in setOf(AppOpsManager.MODE_ALLOWED, AppOpsManager.MODE_DEFAULT) || !manager.areNotificationsEnabled() || manager.getNotificationChannel(CHANNEL)?.importance == NotificationManager.IMPORTANCE_NONE)
            TtsActionError("notifications_blocked", "Enable speech notifications to use background Skip and Stop all controls. Then start speech again.", "notification_settings", ttsUiMessage("android.speech.notificationsRequired")) else null
    }
    fun build(snapshot: NativeTtsSnapshot, epoch: String, name: String): Notification {
        refreshChannel()
        val status = when {
            snapshot.sessionStatus == "reconnecting" -> "android.speech.reconnecting"
            snapshot.playback.phase in setOf("playing", "settling") -> "android.speech.reading"
            snapshot.playback.readingId != null || snapshot.sessionStatus == "starting" -> "android.speech.preparing"
            snapshot.sessionStatus == "stopped" -> "android.speech.stopping"
            snapshot.listening?.paused == true -> "android.speech.timelinePaused"
            else -> "android.speech.waiting"
        }
        val reopen = Intent(context, BrowserActivity::class.java).setAction(ACTION_REOPEN)
            .setData(Uri.parse("twicc-tts://reopen/$epoch"))
            .putExtra(EXTRA_EPOCH, epoch).putExtra("instance", snapshot.instanceId)
            .addFlags(Intent.FLAG_ACTIVITY_CLEAR_TOP or Intent.FLAG_ACTIVITY_SINGLE_TOP)
        return Notification.Builder(context, CHANNEL)
            .setSmallIcon(R.drawable.ic_notification)
            .setContentTitle(NativeMessages.text(context, UiMessage("android.speech.notificationTitle", mapOf("name" to name))))
            .setContentText(NativeMessages.text(context, UiMessage(status)))
            .setContentIntent(PendingIntent.getActivity(context, 0, reopen, PendingIntent.FLAG_IMMUTABLE))
            .setCategory(Notification.CATEGORY_SERVICE).setOngoing(true).setOnlyAlertOnce(true)
            .setVisibility(Notification.VISIBILITY_PRIVATE)
            .apply {
                if (snapshot.playback.readingId != null) addAction(Notification.Action.Builder(null,
                    NativeMessages.text(context, UiMessage("android.speech.skip")), command(ACTION_SKIP, epoch)).build())
                snapshot.listening?.let { listening ->
                    addAction(Notification.Action.Builder(null,
                        NativeMessages.text(context, UiMessage(if (listening.paused) "android.speech.resumeTimeline" else "android.speech.pauseTimeline")),
                        command(if (listening.paused) ACTION_RESUME_TIMELINE else ACTION_PAUSE_TIMELINE,
                            epoch, listening.activationId)).build())
                }
                addAction(Notification.Action.Builder(null, NativeMessages.text(context, UiMessage("android.speech.stopAll")), command(ACTION_STOP_ALL, epoch)).build())
                if (Build.VERSION.SDK_INT >= 31) setForegroundServiceBehavior(Notification.FOREGROUND_SERVICE_IMMEDIATE)
            }.build()
    }
    fun update(snapshot: NativeTtsSnapshot, epoch: String, name: String) = manager.notify(ID, build(snapshot, epoch, name))
    private fun command(action: String, epoch: String, activationId: String? = null): PendingIntent = PendingIntent.getService(context, 0,
        Intent(context, TtsService::class.java).setAction(action)
            .setData(Uri.parse("twicc-tts://control/$epoch/${action.substringAfterLast('.')}/${activationId.orEmpty()}"))
            .putExtra(EXTRA_EPOCH, epoch).putExtra(EXTRA_ACTIVATION, activationId), PendingIntent.FLAG_IMMUTABLE)

    companion object {
        const val CHANNEL = "tts_playback"
        const val ID = 1001
        const val ACTION_SKIP = "com.twidi.twicc.tts.SKIP"
        const val ACTION_STOP_ALL = "com.twidi.twicc.tts.STOP_ALL"
        const val ACTION_PAUSE_TIMELINE = "com.twidi.twicc.tts.PAUSE_TIMELINE"
        const val ACTION_RESUME_TIMELINE = "com.twidi.twicc.tts.RESUME_TIMELINE"
        const val ACTION_REOPEN = "com.twidi.twicc.tts.REOPEN"
        const val EXTRA_EPOCH = "tts_session_epoch"
        const val EXTRA_ACTIVATION = "tts_activation"
        const val EXTRA_INSTANCE = "tts_instance"
        const val PERMISSION_REQUEST = 8601

        /** A denial never resumes queued activation. The next attempt requires a fresh visible user action. */
        fun visiblePreflight(activity: Activity,
            onSettingsRequired: (TtsActionError) -> Unit = { showSettings(activity, it) }): TtsActionError? {
            if (activity.isFinishing || !activity.hasWindowFocus())
                return TtsActionError("not_visible", "Open TwiCC before starting speech.", uiMessage = ttsUiMessage("android.speech.errors.visibleRequired"))
            if (Build.VERSION.SDK_INT >= 33 && activity.checkSelfPermission(android.Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) {
                val preferences = activity.getSharedPreferences("tts_platform", Context.MODE_PRIVATE)
                if (!preferences.getBoolean("notification_requested", false)) {
                    preferences.edit().putBoolean("notification_requested", true).apply()
                    activity.requestPermissions(arrayOf(android.Manifest.permission.POST_NOTIFICATIONS), PERMISSION_REQUEST)
                    return TtsActionError("permission_required", "Enable speech notifications to use background Skip and Stop all controls. Then start speech again.", "notification_settings", ttsUiMessage("android.speech.notificationsRequired"))
                }
            }
            TtsNotification(activity).error()?.let { onSettingsRequired(it); return it }
            return null
        }

        fun batteryAdviceNeeded(activity: Activity): Boolean =
            !activity.getSystemService(PowerManager::class.java).isIgnoringBatteryOptimizations(activity.packageName) &&
                !activity.getSharedPreferences("tts_platform", Context.MODE_PRIVATE).getBoolean("battery_advice_acknowledged", false)

        /** Only Continue acknowledges the advice. Opening Settings never queues an activation. */
        fun showBatteryAdvice(activity: Activity, onContinue: () -> Unit): AlertDialog =
            AlertDialog.Builder(activity).setTitle(NativeMessages.text(activity, UiMessage("android.speech.batteryTitle")))
                .setMessage(NativeMessages.text(activity, UiMessage("android.speech.batteryAdvice")))
                .setNegativeButton(NativeMessages.text(activity, UiMessage("android.speech.continueWithoutChanges"))) { _, _ ->
                    activity.getSharedPreferences("tts_platform", Context.MODE_PRIVATE).edit()
                        .putBoolean("battery_advice_acknowledged", true).apply()
                    onContinue()
                }
                .setPositiveButton(NativeMessages.text(activity, UiMessage("android.speech.openSettings"))) { _, _ -> openBatterySettings(activity) }
                .show()

        fun refreshBatteryAdvice(activity: Activity, dialog: AlertDialog) {
            dialog.setTitle(NativeMessages.text(activity, UiMessage("android.speech.batteryTitle")))
            dialog.setMessage(NativeMessages.text(activity, UiMessage("android.speech.batteryAdvice")))
            dialog.getButton(AlertDialog.BUTTON_NEGATIVE)?.text = NativeMessages.text(activity, UiMessage("android.speech.continueWithoutChanges"))
            dialog.getButton(AlertDialog.BUTTON_POSITIVE)?.text = NativeMessages.text(activity, UiMessage("android.speech.openSettings"))
        }

        private fun openBatterySettings(activity: Activity) {
            runCatching { activity.startActivity(Intent(Settings.ACTION_IGNORE_BATTERY_OPTIMIZATION_SETTINGS)) }.onFailure {
                activity.startActivity(Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS, Uri.parse("package:${activity.packageName}")))
            }
        }
        fun showSettings(activity: Activity, error: TtsActionError): AlertDialog? {
            if (activity.isFinishing) return null
            return AlertDialog.Builder(activity).setTitle(NativeMessages.text(activity, UiMessage("android.speech.settingsTitle")))
                .setMessage(error.uiMessage?.let { UiMessageJson.decode(JSONObject(it)) }?.let { NativeMessages.text(activity, it) } ?: error.message)
                .setNegativeButton(NativeMessages.text(activity, UiMessage("common.actions.cancel")), null)
                .setPositiveButton(NativeMessages.text(activity, UiMessage("android.speech.openSettings"))) { _, _ ->
                    val intent = Intent(Settings.ACTION_CHANNEL_NOTIFICATION_SETTINGS).putExtra(Settings.EXTRA_APP_PACKAGE, activity.packageName)
                        .putExtra(Settings.EXTRA_CHANNEL_ID, CHANNEL)
                    runCatching { activity.startActivity(intent) }.onFailure {
                        activity.startActivity(Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS, Uri.parse("package:${activity.packageName}")))
                    }
                }.show()
        }

        fun refreshSettings(activity: Activity, dialog: AlertDialog, error: TtsActionError) {
            dialog.setTitle(NativeMessages.text(activity, UiMessage("android.speech.settingsTitle")))
            dialog.setMessage(error.uiMessage?.let { UiMessageJson.decode(JSONObject(it)) }
                ?.let { NativeMessages.text(activity, it) } ?: error.message)
            dialog.getButton(AlertDialog.BUTTON_NEGATIVE)?.text = NativeMessages.text(activity, UiMessage("common.actions.cancel"))
            dialog.getButton(AlertDialog.BUTTON_POSITIVE)?.text = NativeMessages.text(activity, UiMessage("android.speech.openSettings"))
        }
    }
}

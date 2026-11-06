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
import com.twidi.twicc.MainActivity
import com.twidi.twicc.R

/** Control URIs identify the session and listening activation. Extras do not distinguish PendingIntents. */
class TtsNotification(private val context: Context) {
    private val manager = context.getSystemService(NotificationManager::class.java)
    init {
        manager.createNotificationChannel(NotificationChannel(CHANNEL, context.getString(R.string.tts_channel), NotificationManager.IMPORTANCE_LOW))
    }
    fun error(): TtsActionError? {
        val permission = Build.VERSION.SDK_INT < 33 || context.checkSelfPermission(android.Manifest.permission.POST_NOTIFICATIONS) == PackageManager.PERMISSION_GRANTED
        // Android's public permissionToOp omits this notification operation on API 35.
        val appOp = context.getSystemService(AppOpsManager::class.java).unsafeCheckOpNoThrow(
            "android:post_notification", android.os.Process.myUid(), context.packageName)
        return if (!permission || appOp !in setOf(AppOpsManager.MODE_ALLOWED, AppOpsManager.MODE_DEFAULT) || !manager.areNotificationsEnabled() || manager.getNotificationChannel(CHANNEL)?.importance == NotificationManager.IMPORTANCE_NONE)
            TtsActionError("notifications_blocked", context.getString(R.string.tts_notifications_required), "notification_settings") else null
    }
    fun build(snapshot: NativeTtsSnapshot, epoch: String, name: String): Notification {
        val status = when {
            snapshot.sessionStatus == "reconnecting" -> R.string.tts_reconnecting
            snapshot.playback.phase in setOf("playing", "settling") -> R.string.tts_reading
            snapshot.playback.readingId != null || snapshot.sessionStatus == "starting" -> R.string.tts_preparing
            snapshot.sessionStatus == "stopped" -> R.string.tts_stopping
            snapshot.listening?.paused == true -> R.string.tts_timeline_paused
            else -> R.string.tts_waiting
        }
        val reopen = Intent(context, MainActivity::class.java).setAction(ACTION_REOPEN)
            .setData(Uri.parse("twicc-tts://reopen/$epoch"))
            .putExtra(EXTRA_EPOCH, epoch).putExtra(EXTRA_INSTANCE, snapshot.instanceId)
            .addFlags(Intent.FLAG_ACTIVITY_CLEAR_TOP or Intent.FLAG_ACTIVITY_SINGLE_TOP)
        return Notification.Builder(context, CHANNEL)
            .setSmallIcon(R.drawable.ic_notification)
            .setContentTitle(context.getString(R.string.tts_notification_title, name))
            .setContentText(context.getString(status))
            .setContentIntent(PendingIntent.getActivity(context, 0, reopen, PendingIntent.FLAG_IMMUTABLE))
            .setCategory(Notification.CATEGORY_SERVICE).setOngoing(true).setOnlyAlertOnce(true)
            .setVisibility(Notification.VISIBILITY_PRIVATE)
            .apply {
                if (snapshot.playback.readingId != null) addAction(Notification.Action.Builder(null,
                    context.getString(R.string.tts_skip), command(ACTION_SKIP, epoch)).build())
                snapshot.listening?.let { listening ->
                    addAction(Notification.Action.Builder(null,
                        context.getString(if (listening.paused) R.string.tts_resume_timeline else R.string.tts_pause_timeline),
                        command(if (listening.paused) ACTION_RESUME_TIMELINE else ACTION_PAUSE_TIMELINE,
                            epoch, listening.activationId)).build())
                }
                addAction(Notification.Action.Builder(null, context.getString(R.string.tts_stop_all), command(ACTION_STOP_ALL, epoch)).build())
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
        fun visiblePreflight(activity: Activity): TtsActionError? {
            if (activity.isFinishing || !activity.hasWindowFocus())
                return TtsActionError("not_visible", "Open TwiCC before starting speech.")
            if (Build.VERSION.SDK_INT >= 33 && activity.checkSelfPermission(android.Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) {
                val preferences = activity.getSharedPreferences("tts_platform", Context.MODE_PRIVATE)
                if (!preferences.getBoolean("notification_requested", false)) {
                    preferences.edit().putBoolean("notification_requested", true).apply()
                    activity.requestPermissions(arrayOf(android.Manifest.permission.POST_NOTIFICATIONS), PERMISSION_REQUEST)
                    return TtsActionError("permission_required", activity.getString(R.string.tts_notifications_required), "notification_settings")
                }
            }
            TtsNotification(activity).error()?.let { showSettings(activity, it); return it }
            return null
        }

        fun batteryAdviceNeeded(activity: Activity): Boolean =
            !activity.getSystemService(PowerManager::class.java).isIgnoringBatteryOptimizations(activity.packageName) &&
                !activity.getSharedPreferences("tts_platform", Context.MODE_PRIVATE).getBoolean("battery_advice_acknowledged", false)

        /** Only Continue acknowledges the advice. Opening Settings never queues an activation. */
        fun showBatteryAdvice(activity: Activity, onContinue: () -> Unit): AlertDialog =
            AlertDialog.Builder(activity).setTitle(R.string.tts_battery_title).setMessage(R.string.tts_battery_advice)
                .setNegativeButton(R.string.tts_continue_without_changes) { _, _ ->
                    activity.getSharedPreferences("tts_platform", Context.MODE_PRIVATE).edit()
                        .putBoolean("battery_advice_acknowledged", true).apply()
                    onContinue()
                }
                .setPositiveButton(R.string.tts_open_settings) { _, _ -> openBatterySettings(activity) }
                .show()

        private fun openBatterySettings(activity: Activity) {
            runCatching { activity.startActivity(Intent(Settings.ACTION_IGNORE_BATTERY_OPTIMIZATION_SETTINGS)) }.onFailure {
                activity.startActivity(Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS, Uri.parse("package:${activity.packageName}")))
            }
        }
        fun showSettings(activity: Activity, error: TtsActionError) {
            if (activity.isFinishing) return
            AlertDialog.Builder(activity).setTitle(R.string.tts_settings_title).setMessage(error.message)
                .setNegativeButton(android.R.string.cancel, null)
                .setPositiveButton(R.string.tts_open_settings) { _, _ ->
                    val intent = Intent(Settings.ACTION_CHANNEL_NOTIFICATION_SETTINGS).putExtra(Settings.EXTRA_APP_PACKAGE, activity.packageName)
                        .putExtra(Settings.EXTRA_CHANNEL_ID, CHANNEL)
                    runCatching { activity.startActivity(intent) }.onFailure {
                        activity.startActivity(Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS, Uri.parse("package:${activity.packageName}")))
                    }
                }.show()
        }
    }
}

package com.twidi.twicc.push

import android.app.Activity
import android.content.ActivityNotFoundException
import android.content.Intent
import android.provider.Settings

/** Open only the four event categories. Settings never requests permission or changes event preferences. */
object NotificationSettingsLauncher {
    fun open(activity: Activity, kind: String) {
        require(kind in setOf("user_turn", "pending_request", "extra_usage", "peer"))
        // The category must exist before Settings resolves its destination, including on first use.
        AndroidPush.manager(activity)
        val category = Intent(Settings.ACTION_CHANNEL_NOTIFICATION_SETTINGS)
            .putExtra(Settings.EXTRA_APP_PACKAGE, activity.packageName)
            .putExtra(Settings.EXTRA_CHANNEL_ID, AndroidPush.channel(kind))
        try {
            activity.startActivity(category)
        } catch (_: ActivityNotFoundException) {
            openAppSettings(activity)
        } catch (_: SecurityException) {
            openAppSettings(activity)
        }
    }

    // Propagate fallback failure. AndroidAppBridge returns the existing localized actionFailed message.
    private fun openAppSettings(activity: Activity) {
        activity.startActivity(Intent(Settings.ACTION_APP_NOTIFICATION_SETTINGS)
            .putExtra(Settings.EXTRA_APP_PACKAGE, activity.packageName))
    }
}

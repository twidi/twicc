package com.twidi.twicc.platform

import android.app.Activity
import android.content.ActivityNotFoundException
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.widget.Toast
import com.twidi.twicc.InstanceUrl

object ExternalLinks {
    fun open(activity: Activity, url: String) {
        if (!InstanceUrl.canOpenExternally(url)) {
            message(activity, "This link type is not supported.")
            return
        }
        try {
            val target = Intent(Intent.ACTION_VIEW, Uri.parse(url)).addCategory(Intent.CATEGORY_BROWSABLE)
            if (Uri.parse(url).scheme?.lowercase() in listOf("http", "https")) {
                // Resolve browsers against an unrelated reserved host, not the destination.
                // This avoids verified app links sending a GitHub/YouTube link into its app.
                val probe = Intent(Intent.ACTION_VIEW, Uri.parse("https://browser-selection.invalid/"))
                    .addCategory(Intent.CATEGORY_BROWSABLE)
                val browsers = activity.packageManager.queryIntentActivities(probe, PackageManager.MATCH_DEFAULT_ONLY)
                    .map { it.activityInfo.packageName }.distinct()
                val preferred = probe.resolveActivity(activity.packageManager)?.packageName
                when {
                    preferred in browsers -> activity.startActivity(target.setPackage(preferred))
                    browsers.size == 1 -> activity.startActivity(target.setPackage(browsers.first()))
                    browsers.isNotEmpty() -> {
                        val choices = browsers.map { Intent(target).setPackage(it) }
                        activity.startActivity(Intent.createChooser(choices.first(), "Open in browser")
                            .putExtra(Intent.EXTRA_INITIAL_INTENTS, choices.drop(1).toTypedArray()))
                    }
                    else -> message(activity, "No browser is available to open this link.")
                }
            } else activity.startActivity(target)
        } catch (_: ActivityNotFoundException) {
            message(activity, "No application can open this link.")
        }
    }

    private fun message(activity: Activity, text: String) = Toast.makeText(activity, text, Toast.LENGTH_LONG).show()
}

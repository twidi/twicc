package com.twidi.twicc.engines.gecko

import android.app.Activity
import android.content.Context
import com.twidi.twicc.BuildConfig
import com.twidi.twicc.Instance
import com.twidi.twicc.browser.*
import com.twidi.twicc.platform.NativeRequests
import org.mozilla.geckoview.GeckoResult
import org.mozilla.geckoview.WebExtension
import org.mozilla.geckoview.GeckoRuntime
import org.mozilla.geckoview.GeckoRuntimeSettings
import java.io.File

/** Gecko permits one runtime per process. Activities own sessions, not the runtime. */
internal object GeckoEngine : BrowserEngine {
    override val displayName = "GeckoView 159"
    private var runtime: GeckoRuntime? = null
    private var appExtension: GeckoResult<WebExtension>? = null
    fun appExtension(context: Context): GeckoResult<WebExtension> = appExtension ?: runtime(context)
        .webExtensionController.ensureBuiltIn("resource://android/assets/android-app-bridge/", "twicc-android-bridge@twidi.com")
        .also { appExtension = it }
    fun runtime(context: Context): GeckoRuntime {
        runtime?.let { return it }
        File(context.cacheDir, "gecko-uploads").deleteRecursively()
        return GeckoRuntime.create(context.applicationContext,
            GeckoRuntimeSettings.Builder().remoteDebuggingEnabled(BuildConfig.DEBUG).build()).also { runtime = it }
    }
    override fun createSession(activity: Activity, instance: Instance, events: BrowserEvents, requests: NativeRequests): BrowserSession =
        GeckoBrowserSession(activity, instance, events, requests)

    override fun requestStorageDeletion(context: Context, storageId: String): Boolean = runCatching {
        // This API submits deletion asynchronously; Gecko exposes no completion result.
        runtime(context).storageController.clearDataForSessionContext(storageId)
        true
    }.getOrDefault(false)
}

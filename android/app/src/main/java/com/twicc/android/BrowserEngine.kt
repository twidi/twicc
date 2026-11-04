package com.twicc.android

import android.content.Context
import org.mozilla.geckoview.GeckoRuntime
import org.mozilla.geckoview.GeckoRuntimeSettings
import java.io.File

/** Gecko permits one runtime per process. Activities own sessions, not the runtime. */
object BrowserEngine {
    private var runtime: GeckoRuntime? = null

    fun get(context: Context): GeckoRuntime {
        runtime?.let { return it }
        // Recover temporary uploads left by a killed process, before any new session opens.
        File(context.cacheDir, "gecko-uploads").deleteRecursively()
        return GeckoRuntime.create(
            context.applicationContext,
            GeckoRuntimeSettings.Builder().remoteDebuggingEnabled(BuildConfig.DEBUG).build(),
        ).also { runtime = it }
    }
}

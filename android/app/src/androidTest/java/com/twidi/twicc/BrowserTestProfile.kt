package com.twidi.twicc

import android.content.Context
import androidx.test.platform.app.InstrumentationRegistry
import com.twidi.twicc.browser.EngineProvider
import java.io.File

/** Gecko gets an isolated test profile containing ONLY the fixture CA. Ordinary APK TLS is unchanged. */
internal fun prepareBrowserTestProfile() {
    if (!EngineProvider.engine.displayName.startsWith("Gecko")) return
    val instrumentation = InstrumentationRegistry.getInstrumentation()
    val profile = File(instrumentation.targetContext.cacheDir, "tts-boundary-gecko-profile").apply { mkdirs() }
    instrumentation.runOnMainSync {
        val engine = Class.forName("com.twidi.twicc.engines.gecko.GeckoEngine")
        val field = engine.getDeclaredField("runtime").apply { isAccessible = true }
        if (field.get(null) == null) {
            instrumentation.context.assets.open("tts-ca-profile/cert9.db").use { input ->
                File(profile, "cert9.db").outputStream().use { input.copyTo(it) }
            }
            val builder = Class.forName("org.mozilla.geckoview.GeckoRuntimeSettings\$Builder").getConstructor().newInstance()
            builder.javaClass.getMethod("arguments", Array<String>::class.java)
                .invoke(builder, arrayOf("--profile", profile.absolutePath))
            val settings = builder.javaClass.getMethod("build").invoke(builder)
            val runtime = Class.forName("org.mozilla.geckoview.GeckoRuntime").getMethod("create", Context::class.java, settings.javaClass)
                .invoke(null, instrumentation.targetContext, settings)
            field.set(null, runtime)
        }
    }
}

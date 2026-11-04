package com.twidi.twicc.engines.webview

import android.app.Activity
import android.content.Context
import androidx.webkit.ProfileStore
import androidx.webkit.WebViewFeature
import com.twidi.twicc.Instance
import com.twidi.twicc.browser.*
import com.twidi.twicc.platform.NativeRequests

internal object WebViewEngine : BrowserEngine {
    override val displayName = "Android System WebView"
    override fun createSession(activity: Activity, instance: Instance, events: BrowserEvents, requests: NativeRequests): BrowserSession {
        check(WebViewFeature.isFeatureSupported(WebViewFeature.MULTI_PROFILE)) {
            "Update Android System WebView or Chrome to use separate instances."
        }
        return WebViewBrowserSession(activity, instance, events, requests)
    }
    override fun requestStorageDeletion(context: Context, storageId: String): Boolean = runCatching {
        if (!WebViewFeature.isFeatureSupported(WebViewFeature.MULTI_PROFILE)) return false
        // Profiles loaded in this process cannot be deleted, even after WebView.destroy().
        ProfileStore.getInstance().deleteProfile(storageId)
        true
    }.getOrDefault(false)
}

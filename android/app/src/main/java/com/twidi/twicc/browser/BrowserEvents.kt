package com.twidi.twicc.browser

import com.twidi.twicc.NavigationDecision
import com.twidi.twicc.NavigationRequest
import com.twidi.twicc.platform.DownloadSource

/** Events carry application data, never engine-specific objects or callbacks. */
interface BrowserEvents {
    fun onNavigationRequest(request: NavigationRequest): NavigationDecision
    fun onPageStarted(url: String)
    fun onLocationChanged(url: String)
    fun onProgressChanged(progress: Int)
    fun onPageFinished()
    fun onHistoryChanged(canGoBack: Boolean)
    fun onFailure(message: String)
    fun onRendererStopped()
    fun onDownload(source: DownloadSource)
}

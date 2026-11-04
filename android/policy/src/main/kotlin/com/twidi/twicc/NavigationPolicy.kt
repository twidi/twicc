package com.twidi.twicc

data class NavigationRequest(
    val url: String,
    val mainFrame: Boolean = true,
    val newWindow: Boolean = false,
    val userGesture: Boolean = false,
)

enum class NavigationDecision { ALLOW, OPEN_EXTERNAL, BLOCK }

object NavigationPolicy {
    fun decide(instance: InstanceUrl, request: NavigationRequest): NavigationDecision = when {
        !request.mainFrame -> NavigationDecision.ALLOW
        request.newWindow && !request.userGesture -> NavigationDecision.BLOCK
        !request.newWindow && instance.allowsNavigation(request.url) -> NavigationDecision.ALLOW
        InstanceUrl.canOpenExternally(request.url) -> NavigationDecision.OPEN_EXTERNAL
        else -> NavigationDecision.BLOCK
    }
}

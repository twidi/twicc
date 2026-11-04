package com.twidi.twicc

import java.net.URI
import java.util.Locale

/** A server origin, never a login URL containing credentials or a session token. */
class InstanceUrl private constructor(val origin: String) {
    fun allowsNavigation(url: String): Boolean = runCatching {
        val uri = URI(url)
        uri.rawUserInfo == null && originOf(uri) == origin
    }.getOrDefault(false)

    companion object {
        fun parse(input: String): InstanceUrl {
            val uri = try {
                URI(input.trim())
            } catch (_: Exception) {
                throw IllegalArgumentException("Enter a valid HTTPS address.")
            }
            require(uri.scheme.equals("https", ignoreCase = true) && !uri.host.isNullOrBlank()) {
                "Use an HTTPS address, such as https://twicc.example.com."
            }
            require(uri.rawUserInfo == null && uri.rawQuery == null && uri.rawFragment == null) {
                "Use the server address without credentials, a query, or a fragment."
            }
            require(uri.rawPath.isNullOrEmpty() || uri.rawPath == "/") {
                "Use the server root address, without a page path."
            }
            require(uri.port == -1 || uri.port in 1..65535) { "Enter a valid port number." }
            require(!uri.rawAuthority.endsWith(":")) { "Enter a valid port number." }
            return InstanceUrl(originOf(uri)!!)
        }

        private fun originOf(uri: URI): String? {
            if (!uri.scheme.equals("https", ignoreCase = true) || uri.host.isNullOrBlank()) return null
            if (uri.port != -1 && uri.port !in 1..65535) return null
            val host = uri.host.lowercase(Locale.ROOT)
            val port = if (uri.port == -1 || uri.port == 443) "" else ":${uri.port}"
            return "https://$host$port"
        }

        fun canOpenExternally(url: String): Boolean = runCatching {
            val uri = URI(url)
            when (uri.scheme?.lowercase(Locale.ROOT)) {
                "https", "http" -> !uri.host.isNullOrBlank() && uri.rawUserInfo == null
                "mailto", "tel" -> !uri.rawSchemeSpecificPart.isNullOrBlank()
                else -> false
            }
        }.getOrDefault(false)
    }
}

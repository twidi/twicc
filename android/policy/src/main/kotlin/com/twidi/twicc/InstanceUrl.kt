package com.twidi.twicc

import java.net.URI
import java.util.Locale

enum class InstanceValidationCode {
    INVALID_ADDRESS, HTTPS_REQUIRED, ORIGIN_REQUIRED, ROOT_REQUIRED, INVALID_PORT, DUPLICATE,
}

class InstanceValidationException(val code: InstanceValidationCode) : IllegalArgumentException(code.name)

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
                throw InstanceValidationException(InstanceValidationCode.INVALID_ADDRESS)
            }
            if (!uri.scheme.equals("https", ignoreCase = true) || uri.host.isNullOrBlank())
                throw InstanceValidationException(InstanceValidationCode.HTTPS_REQUIRED)
            if (uri.rawUserInfo != null || uri.rawQuery != null || uri.rawFragment != null)
                throw InstanceValidationException(InstanceValidationCode.ORIGIN_REQUIRED)
            if (!uri.rawPath.isNullOrEmpty() && uri.rawPath != "/")
                throw InstanceValidationException(InstanceValidationCode.ROOT_REQUIRED)
            if ((uri.port != -1 && uri.port !in 1..65535) || uri.rawAuthority.endsWith(":"))
                throw InstanceValidationException(InstanceValidationCode.INVALID_PORT)
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

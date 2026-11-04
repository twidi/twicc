package com.twidi.twicc

/** Use server and document-provider names only as names, never as paths. */
object FileNames {
    fun safe(value: String): String {
        val cleaned = value.replace(Regex("[\\\\/\\p{Cntrl}]"), "_").trim()
        // Android filesystems limit names in bytes. Keep complete Unicode code points.
        var bytes = 0
        var end = 0
        while (end < cleaned.length && end < 120) {
            val point = cleaned.codePointAt(end)
            val length = String(Character.toChars(point)).toByteArray(Charsets.UTF_8).size
            if (bytes + length > 240) break
            bytes += length
            end += Character.charCount(point)
        }
        return cleaned.substring(0, end).takeUnless { it.isEmpty() || it == "." || it == ".." } ?: "download"
    }
}

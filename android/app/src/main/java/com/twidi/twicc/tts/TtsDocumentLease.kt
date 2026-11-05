package com.twidi.twicc.tts

/** Navigation epochs guard asynchronous injection. Retired documents cannot register again. */
class TtsDocumentLease {
    var epoch = 0L
        private set
    var token: String? = null
        private set
    private val retired = linkedSetOf<String>()
    fun begin(): Long {
        token?.let { retired.add(it) }
        token = null
        return ++epoch
    }
    fun commit(expectedEpoch: Long, candidate: String): Boolean {
        if (expectedEpoch != epoch || candidate.isBlank() || candidate in retired || token != null && token != candidate) return false
        token = candidate
        return true
    }
    fun matches(candidate: String?) = candidate != null && candidate == token
}

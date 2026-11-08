package com.twidi.twicc.i18n

/** Installation language and its persisted compare-and-set revision. */
data class AppLanguage(val locale: String, val revision: Long) {
    init {
        require(locale in SUPPORTED_LOCALES)
        require(revision in 0..MAX_REVISION)
    }

    /** Decide a selection without persistence or platform state. */
    fun select(locale: String, expectedRevision: Long): LanguageSelection {
        val error = when {
            locale !in SUPPORTED_LOCALES -> "android.language.errors.unsupported"
            expectedRevision !in 0..MAX_REVISION -> "android.language.errors.invalidRevision"
            expectedRevision != revision -> "android.language.errors.changed"
            locale == this.locale -> return LanguageSelection(this, true)
            revision == MAX_REVISION -> "android.language.errors.revisionExhausted"
            else -> return LanguageSelection(AppLanguage(locale, revision + 1), true)
        }
        return LanguageSelection(this, false, UiMessage(error))
    }

    companion object {
        val SUPPORTED_LOCALES: List<String> = listOf("en", "de", "es", "fr", "pt-BR")
        const val MAX_REVISION: Long = 9_007_199_254_740_991L
        val INITIAL = AppLanguage("en", 0)

        /** Missing, partial, malformed, or out-of-range preferences reset the whole pair. */
        fun fromStored(locale: Any?, revision: Any?): AppLanguage =
            if (locale is String && locale in SUPPORTED_LOCALES && revision is Long && revision in 0..MAX_REVISION) {
                AppLanguage(locale, revision)
            } else INITIAL
    }
}

data class LanguageSelection(val state: AppLanguage, val accepted: Boolean, val error: UiMessage? = null)

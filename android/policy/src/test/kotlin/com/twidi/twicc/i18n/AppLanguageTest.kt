package com.twidi.twicc.i18n

import org.junit.Assert.*
import org.junit.Test

class AppLanguageTest {
    @Test fun rejectsStaleRevisionAndUnknownLocale() {
        val initial = AppLanguage("en", 0)
        assertEquals(LanguageSelection(AppLanguage("fr", 1), true), initial.select("fr", 0))
        val current = AppLanguage("fr", 1)
        assertEquals(LanguageSelection(current, false, UiMessage("android.language.errors.changed")),
            current.select("de", 0))
        for (locale in listOf("it", "pt", "FR", "", "fr-FR")) {
            assertEquals(LanguageSelection(current, false, UiMessage("android.language.errors.unsupported")),
                current.select(locale, 1))
        }
        for (locale in listOf("en", "de", "es", "fr", "pt-BR")) {
            assertTrue(initial.select(locale, 0).accepted)
        }
    }

    @Test fun sameLocaleIsNoOp() {
        val current = AppLanguage("fr", 17)
        assertEquals(LanguageSelection(current, true), current.select("fr", 17))
        assertFalse(current.select("fr", 16).accepted)
    }

    @Test fun boundsRevisionsWithoutOverflow() {
        val current = AppLanguage("en", 0)
        for (revision in listOf(-1L, 9_007_199_254_740_992L, Long.MAX_VALUE)) {
            assertEquals(LanguageSelection(current, false, UiMessage("android.language.errors.invalidRevision")),
                current.select("fr", revision))
        }
        val last = AppLanguage("fr", 9_007_199_254_740_991L)
        assertEquals(LanguageSelection(last, true), last.select("fr", last.revision))
        assertEquals(LanguageSelection(last, false, UiMessage("android.language.errors.revisionExhausted")),
            last.select("en", last.revision))
        assertEquals(last, AppLanguage("en", last.revision - 1).select("fr", last.revision - 1).state)
    }

    @Test fun invalidStoredStateResetsTheWholePair() {
        assertEquals(AppLanguage("pt-BR", 42), AppLanguage.fromStored("pt-BR", 42L))
        for ((locale, revision) in listOf(
            null to null, "fr" to null, null to 1L, "it" to 1L,
            "fr" to -1L, "fr" to 9_007_199_254_740_992L, "fr" to "1", "fr" to 1,
        )) {
            assertEquals(AppLanguage("en", 0), AppLanguage.fromStored(locale, revision))
        }
    }
}

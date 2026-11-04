package com.twidi.twicc

import org.junit.Assert.*
import org.junit.Test

class FileNamesTest {
    @Test fun namesCannotBecomePaths() {
        assertEquals(".._.._private.txt", FileNames.safe("../../private.txt"))
        assertEquals("a_b_c_d.txt", FileNames.safe("a\\b\nc\u0000d.txt"))
        listOf("", "  ", ".", "..").forEach { assertEquals("download", FileNames.safe(it)) }
    }

    @Test fun preservesNormalNamesAndLimitsLength() {
        assertEquals("report 2026.pdf", FileNames.safe(" report 2026.pdf "))
        assertEquals("résumé.txt", FileNames.safe("résumé.txt"))
        assertEquals(120, FileNames.safe("a".repeat(300)).length)
        assertTrue(FileNames.safe("文".repeat(120)).toByteArray(Charsets.UTF_8).size <= 240)
        assertEquals("🚀".repeat(60), FileNames.safe("🚀".repeat(120)))
    }
}

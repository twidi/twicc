package com.twidi.twicc.push

import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test
import java.security.MessageDigest

class NotificationSoundManifestTest {
    private fun source() = checkNotNull(javaClass.getResource("/notification-sounds/manifest.json")).readText()

    @Test fun allowlistMatchesPackagedManifestAndEveryWav() {
        val manifest = NotificationSounds.parseManifest(source())
        assertEquals(listOf("beep", "chime", "ping", "pop", "bloop", "tink", "knock", "sweep", "signal", "ripple"),
            NotificationSounds.ids)
        assertEquals(NotificationSounds.ids, manifest.sounds.map { it.id })
        for (sound in manifest.sounds) {
            val wav = checkNotNull(javaClass.getResource("/notification-sounds/${sound.id}.wav")).readBytes()
            assertEquals(wav.size.toLong(), sound.size)
            assertEquals(sound.sha256, MessageDigest.getInstance("SHA-256").digest(wav)
                .joinToString("") { "%02x".format(it.toInt() and 0xff) })
        }
    }

    @Test fun rejectsUnknownIdsAssetTraversalAndInvalidHashes() {
        for ((key, value) in listOf("id" to "unknown", "file" to "../private.wav", "sha256" to "invalid", "frames" to 0)) {
            val source = JSONObject(source())
            source.getJSONArray("entries").getJSONObject(0).put(key, value)
            assertThrows(IllegalArgumentException::class.java) { NotificationSounds.parseManifest(source.toString()) }
        }
    }
}

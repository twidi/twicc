import java.security.MessageDigest
import java.nio.ByteBuffer
import java.nio.ByteOrder
import groovy.json.JsonSlurper

plugins {
    id("com.android.application")
}

val targetProfile = providers.gradleProperty("targetProfile").orElse("64").get()
val targetAbis = when (targetProfile) {
    "64" -> listOf("arm64-v8a")
    "32" -> listOf("armeabi-v7a")
    "emulator" -> listOf("x86_64")
    "universal" -> listOf("arm64-v8a", "armeabi-v7a", "x86_64")
    else -> throw GradleException("Unknown targetProfile '$targetProfile'. Choose 64, 32, emulator, or universal.")
}

android {
    namespace = "com.twidi.twicc"
    compileSdk {
        version = release(37) { minorApiLevel = 2 }
    }

    defaultConfig {
        applicationId = "com.twidi.twicc"
        minSdk = 29
        targetSdk = 35
        ndk { abiFilters += targetAbis }
        versionCode = 43
        versionName = "0.5.32"
        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"
    }
    flavorDimensions += "engine"
    productFlavors {
        create("webview") { dimension = "engine" }
        create("gecko") { dimension = "engine" }
    }
    // Test CA trust is opt-in and never enters normal debug or release APKs.
    if (providers.gradleProperty("ttsBoundaryTests").orNull == "true") {
        sourceSets.getByName("debug") {
            manifest.srcFile("src/ttsBoundary/AndroidManifest.xml")
            res.srcDir("src/ttsBoundary/res")
        }
    }
    sourceSets.getByName("androidTest").assets.srcDir("../../tests/fixtures/native_tts_audio")
    sourceSets.getByName("test").resources.srcDir("src/main/assets")
    buildFeatures { buildConfig = true }
    androidResources { noCompress += "wav" }
    buildTypes {
        release {
            isDebuggable = false
            // Preserve local APK update compatibility across release/debug and both engines.
            signingConfig = signingConfigs.getByName("debug")
            isMinifyEnabled = false
        }
    }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
}

kotlin { compilerOptions { jvmTarget.set(org.jetbrains.kotlin.gradle.dsl.JvmTarget.JVM_17) } }

dependencies {
    "geckoImplementation"("org.mozilla.geckoview:geckoview:159.0.20261102144711")
    "webviewImplementation"("androidx.webkit:webkit:1.14.0")
    testImplementation("junit:junit:4.13.2")
    testImplementation("org.json:json:20240303")
    testImplementation("com.squareup.okhttp3:mockwebserver:4.12.0")
    testImplementation("com.squareup.okhttp3:okhttp-tls:4.12.0")
    implementation(project(":policy"))
    implementation("com.squareup.okhttp3:okhttp:4.12.0")
    implementation("androidx.media3:media3-exoplayer:1.9.0")
    // Encrypted Web Push through the embedded FCM distributor.
    implementation("org.unifiedpush.android:connector:3.3.5")
    implementation("org.unifiedpush.android:embedded-fcm-distributor:3.1.0")
    androidTestImplementation("com.squareup.okhttp3:mockwebserver:4.12.0")
    androidTestImplementation("com.squareup.okhttp3:okhttp-tls:4.12.0")
    androidTestImplementation("androidx.test:runner:1.6.2")
    androidTestImplementation("androidx.test:core:1.6.1")
    androidTestImplementation("androidx.test.ext:junit:1.2.1")
}

// Java-only verification: standalone APK builds consume committed generated files.
// Never install or invoke Node/npm from Gradle or the Android build entry point.
val i18nRoot = rootProject.projectDir.parentFile
val i18nFingerprints = i18nRoot.resolve("android/app/src/main/assets/i18n/sources.sha256")
val verifyAndroidI18n = tasks.register("verifyAndroidI18n") {
    group = "verification"
    description = "Reject stale generated Android catalogs and browser test fixtures."
    // Deliberately always verify. Missing/changed inputs must not pass an up-to-date cache.
    doLast {
        val seen = mutableSetOf<String>()
        val catalogs = mutableMapOf<String, Map<*, *>>()
        val entries = i18nFingerprints.readLines()
        if (entries.isEmpty()) throw GradleException("Empty Android i18n fingerprints")
        for (line in entries) {
            val match = Regex("^([a-f0-9]{64})  ([A-Za-z0-9_./#-]+)$").matchEntire(line)
                ?: throw GradleException("Invalid Android i18n fingerprint: $line")
            val (expected, path) = match.destructured
            if (path.startsWith("/") || ".." in path.split("/") || !seen.add(path)) {
                throw GradleException("Invalid Android i18n fingerprint path: $path")
            }
            val parts = path.split('#')
            val source = i18nRoot.resolve(parts[0])
            val bytes = if (!source.isFile) null else if (parts.size == 1) source.readBytes() else {
                if (parts.size != 2 || !Regex("^frontend/src/i18n/locales/[A-Za-z0-9_-]+\\.json$").matches(parts[0]) || parts[1].isEmpty()) {
                    throw GradleException("Invalid Android translation fingerprint: $path")
                }
                val catalog = catalogs.getOrPut(parts[0]) {
                    JsonSlurper().parse(source) as? Map<*, *>
                        ?: throw GradleException("Invalid Android translation catalog: ${parts[0]}")
                }
                (catalog[parts[1]] as? String)?.toByteArray(Charsets.UTF_8)
            }
            val actual = bytes?.let { MessageDigest.getInstance("SHA-256")
                .digest(it).joinToString("") { byte -> "%02x".format(byte.toInt() and 0xff) } }
            if (actual != expected) throw GradleException(
                "Stale Android i18n input/output: $path. Run cd frontend && npm run i18n:android:generate, then commit all outputs."
            )
        }
    }
}
tasks.matching { it.name == "preBuild" }.configureEach { dependsOn(verifyAndroidI18n) }

val notificationSoundAssets = projectDir.resolve("src/main/assets/notification-sounds")
val notificationSoundsFixture = providers.gradleProperty("notificationSoundsFixtureDir")
val verifyAndroidNotificationSounds = tasks.register("verifyAndroidNotificationSounds") {
    group = "verification"
    description = "Reject stale or modified packaged notification sounds without Node or Chromium."
    doLast {
        fun sha256(bytes: ByteArray) = MessageDigest.getInstance("SHA-256")
            .digest(bytes).joinToString("") { "%02x".format(it.toInt() and 0xff) }
        val sourcePaths = listOf(
            "frontend/scripts/generate-android-notification-sounds.mjs",
            "frontend/src/utils/notificationSoundDefinitions.js",
        )
        val sources = sourcePaths.associateWith { path -> sha256(i18nRoot.resolve(path).readBytes()) }
        val formatFingerprint = "format=1;sampleRate=48000;channels=1;bitsPerSample=16;endMargin=0.05\n"
        val fingerprint = sha256((formatFingerprint + sourcePaths.joinToString("") { "${sources[it]}  $it\n" }).toByteArray(Charsets.UTF_8))
        val ids = listOf("beep", "chime", "ping", "pop", "bloop", "tink", "knock", "sweep", "signal", "ripple")
        // The fixture is additional: a property can never bypass committed-asset verification.
        val directories = listOf(notificationSoundAssets) + listOfNotNull(notificationSoundsFixture.orNull?.let { file(it) })
        for (directory in directories) {
            fun invalid(reason: String): Nothing = throw GradleException(
                "Invalid Android notification sounds in $directory: $reason. Run cd frontend && npm run sounds:android:generate -- --scratch-dir <scratch>, then commit all outputs."
            )
            val expectedFiles = (ids.map { "$it.wav" } + "manifest.json").toSet()
            if (directory.listFiles()?.map { it.name }?.toSet() != expectedFiles || directory.listFiles()?.any { !it.isFile } != false) {
                invalid("missing or unexpected files")
            }
            val manifest = JsonSlurper().parse(directory.resolve("manifest.json")) as? Map<*, *> ?: invalid("manifest object")
            if (manifest["format"] != 1 || manifest["sampleRate"] != 48000 || manifest["channels"] != 1 || manifest["bitsPerSample"] != 16) {
                invalid("manifest format")
            }
            if (manifest["sources"] != sources || manifest["sourceFingerprint"] != fingerprint) invalid("stale source fingerprint")
            val entries = manifest["entries"] as? List<*> ?: invalid("manifest entries")
            if (entries.size != ids.size) invalid("sound count")
            for ((index, id) in ids.withIndex()) {
                val entry = entries[index] as? Map<*, *> ?: invalid("entry $id")
                val frames = (entry["frames"] as? Number)?.toLong() ?: invalid("frames $id")
                if (entry["id"] != id || entry["file"] != "$id.wav" || frames <= 0) invalid("sound ID/file/frames $id")
                val wav = directory.resolve("$id.wav").readBytes()
                if (entry["sha256"] != sha256(wav)) invalid("modified WAV $id")
                if (wav.size.toLong() != 44 + frames * 2) invalid("WAV length $id")
                val header = ByteBuffer.wrap(wav).order(ByteOrder.LITTLE_ENDIAN)
                fun text(start: Int, end: Int) = String(wav, start, end - start, Charsets.US_ASCII)
                if (text(0, 4) != "RIFF" || header.getInt(4) != wav.size - 8 || text(8, 16) != "WAVEfmt " ||
                    header.getInt(16) != 16 || header.getShort(20).toInt() != 1 || header.getShort(22).toInt() != 1 ||
                    header.getInt(24) != 48000 || header.getInt(28) != 96000 || header.getShort(32).toInt() != 2 ||
                    header.getShort(34).toInt() != 16 || text(36, 40) != "data" || header.getInt(40).toLong() != frames * 2) {
                    invalid("PCM16 WAV structure $id")
                }
            }
        }
    }
}
tasks.matching { it.name == "preBuild" }.configureEach { dependsOn(verifyAndroidNotificationSounds) }

// Generated self-contained adapters live only in the instrumentation APK.
val nativeTtsFixtureAssets by tasks.registering(Sync::class) {
    dependsOn(verifyAndroidI18n)
    from("src/androidTest/generated") { include("androidAppClient.js", "nativeTtsClient.js", "androidLocale.js") }
    from("../../frontend") { include("index.html") }
    into(layout.buildDirectory.dir("generated/nativeTtsFixtureAssets"))
}
android.sourceSets.getByName("androidTest").assets.srcDir("build/generated/nativeTtsFixtureAssets")
tasks.matching { it.name.startsWith("merge") && it.name.endsWith("AndroidTestAssets") }.configureEach {
    dependsOn(nativeTtsFixtureAssets)
}

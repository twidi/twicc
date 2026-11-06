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
        versionCode = 22
        versionName = "0.5.11"
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
    buildFeatures { buildConfig = true }
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

// Package the actual page adapters only in the instrumentation APK, without a copied test implementation.
val nativeTtsFixtureAssets by tasks.registering(Sync::class) {
    from("../../frontend/src/utils") { include("androidAppClient.js", "nativeTtsClient.js") }
    from("../../frontend") { include("index.html") }
    into(layout.buildDirectory.dir("generated/nativeTtsFixtureAssets"))
}
android.sourceSets.getByName("androidTest").assets.srcDir("build/generated/nativeTtsFixtureAssets")
tasks.matching { it.name.startsWith("merge") && it.name.endsWith("AndroidTestAssets") }.configureEach {
    dependsOn(nativeTtsFixtureAssets)
}

plugins {
    id("com.android.application")
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
        versionCode = 10
        versionName = "0.4.1"
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
    splits {
        abi {
            isEnable = true
            reset()
            include("arm64-v8a", "armeabi-v7a", "x86_64")
            isUniversalApk = true
        }
    }
    buildTypes {
        release {
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
    androidTestImplementation("com.squareup.okhttp3:mockwebserver:4.12.0")
    androidTestImplementation("com.squareup.okhttp3:okhttp-tls:4.12.0")
    androidTestImplementation("androidx.test:runner:1.6.2")
    androidTestImplementation("androidx.test:core:1.6.1")
    androidTestImplementation("androidx.test.ext:junit:1.2.1")
}

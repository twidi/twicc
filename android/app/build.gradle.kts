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
    }
    flavorDimensions += "engine"
    productFlavors {
        create("webview") { dimension = "engine" }
        create("gecko") { dimension = "engine" }
    }
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
    implementation(project(":policy"))
}

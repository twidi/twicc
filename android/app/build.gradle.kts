plugins {
    id("com.android.application")
}

android {
    namespace = "com.twicc.android"
    compileSdk {
        version = release(37) { minorApiLevel = 2 }
    }

    defaultConfig {
        applicationId = "com.twicc.android"
        minSdk = 29
        targetSdk = 35
        versionCode = 7
        versionName = "0.2.0"
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
    implementation("org.mozilla.geckoview:geckoview:159.0.20261102144711")
    implementation(project(":policy"))
}

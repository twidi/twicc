plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

android {
    namespace = "com.twicc.android"
    compileSdk = 36

    defaultConfig {
        applicationId = "com.twicc.android"
        minSdk = 29
        targetSdk = 35
        versionCode = 6
        versionName = "0.1.5"
    }
    buildFeatures { buildConfig = true }
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
    implementation("androidx.webkit:webkit:1.14.0")
    implementation(project(":policy"))
}

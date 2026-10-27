plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("org.jetbrains.kotlin.plugin.compose")
}

android {
    namespace = "com.sidebarstudy.wear"
    compileSdk = 36
    defaultConfig {
        applicationId = "com.sidebarstudy.wear"
        minSdk = 30          // Wear OS 3+ (Galaxy Watch 4 and newer)
        targetSdk = 36
        versionCode = 1
        versionName = "1.0"
    }
    buildFeatures { compose = true; buildConfig = true }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlin { compilerOptions { jvmTarget.set(org.jetbrains.kotlin.gradle.dsl.JvmTarget.JVM_17) } }
    androidResources { noCompress += listOf("json", "js") }
}

dependencies {
    val composeBom = platform("androidx.compose:compose-bom:2025.08.00")
    implementation(composeBom)
    implementation("androidx.activity:activity-compose:1.11.0")
    implementation("androidx.lifecycle:lifecycle-viewmodel-compose:2.9.2")
    implementation("androidx.compose.ui:ui")
    implementation("androidx.compose.foundation:foundation")
    implementation("androidx.wear.compose:compose-material:1.5.0")
    implementation("androidx.wear.compose:compose-foundation:1.5.0")
    implementation("androidx.wear:wear:1.3.0")
    implementation("androidx.wear:wear-input:1.2.0")
    implementation("androidx.wear.compose:compose-navigation:1.5.0")
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.10.2")
    implementation("androidx.security:security-crypto:1.1.0")
    // runs the phone app's grading code (grading.js + pinyin-pro) on the watch
    implementation("wang.harlon.quickjs:wrapper-android:3.2.0")

    testImplementation("junit:junit:4.13.2")
    testImplementation("org.json:json:20250517")  // the real org.json (Android's is a stub in unit tests)
}

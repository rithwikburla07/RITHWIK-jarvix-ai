plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}
android {
    namespace = "ai.jarvix.companion"
    compileSdk = 35
    defaultConfig {
        applicationId = "ai.jarvix.companion"
        minSdk = 24
        targetSdk = 35
        versionCode = 117
        versionName = "1.1.7"
    }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions { jvmTarget = "17" }
}
dependencies {
    implementation("com.journeyapps:zxing-android-embedded:4.3.0")
    testImplementation("junit:junit:4.13.2")
}

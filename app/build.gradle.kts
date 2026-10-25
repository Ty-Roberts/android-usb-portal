import java.util.Properties

plugins { id("com.android.application") }
val releaseVersion = Properties().apply { rootProject.file("version.properties").inputStream().use { load(it) } }
android {
    namespace = "dev.pocketpull"
    compileSdk = 36
    defaultConfig {
        applicationId = "dev.pocketpull"
        minSdk = 26
        targetSdk = 36
        versionCode = releaseVersion.getProperty("android").toInt()
        versionName = releaseVersion.getProperty("name")
        testInstrumentationRunner = "dev.pocketpull.StorageChecks"
    }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
}

dependencies { implementation(project(":usb-android")) }

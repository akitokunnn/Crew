plugins {
    alias(libs.plugins.android.library)
}

android {
    namespace = "local.crew.android.voice"
    compileSdk = 36
    buildToolsVersion = "36.0.0"

    defaultConfig {
        minSdk = 26
        ndk {
            abiFilters += listOf("arm64-v8a", "x86_64")
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    androidResources {
        noCompress += listOf("onnx", "fst", "txt")
    }
}

androidComponents {
    beforeVariants(selector().withBuildType("release")) { variant ->
        variant.enable = false
    }
}

// AGP cannot embed a local AAR in another AAR. The app consumes :voice as a project
// and packages the sherpa AAR directly from its runtime dependency graph.
tasks.configureEach {
    if (name == "bundleDebugAar") {
        enabled = false
    }
}

dependencies {
    implementation(project(":core"))
    implementation(files("libs/sherpa-onnx-2.0.3.aar"))
    implementation(libs.coroutines.android)
    implementation(libs.okhttp.client)
    implementation(libs.okhttp.sse)
    implementation(libs.serialization.json)

    testImplementation(libs.junit)
    testImplementation(libs.okhttp.mockwebserver)
    testImplementation(libs.coroutines.test)
    testImplementation(libs.robolectric)
}

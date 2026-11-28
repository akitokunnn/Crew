# Crew Android

用户说明见 [docs/ANDROID.md](../docs/ANDROID.md)。

1. 下载并校验资源：`python android/scripts/fetch_assets.py`（Mac/Linux 用 `python3`，需要 Python 3.11+）。按 `assets.lock.json` 的字节数和 SHA-256 下载 sherpa-onnx AAR 和语音模型，不进 git。
2. 设置环境（只在当前终端）：`JAVA_HOME` 指向 JDK 21（Android Studio 自带的 `jbr` 即可），`ANDROID_HOME` 指向装好 Android 36 平台和 Build Tools 36.0.0 的 SDK。
3. 构建：`android\gradlew.bat -p android assembleDebug`（Mac/Linux：`sh android/gradlew -p android assembleDebug`），产物 `android/app/build/outputs/apk/debug/app-debug.apk`。
4. 测试：`android\gradlew.bat -p android :core:test :voice:testDebugUnitTest :app:testDebugUnitTest`。
5. 模拟器验收：`app/src/androidTest/` 里的探针带 intent extra `crew.debug.wav` 打开语音页（只在 debug 构建存在；多个 wav 用 `;` 分隔，`路径@wait` 表示等同名 `.ready` 文件出现再播），用来代替麦克风喂测试音频。电脑端隔离实例的局域网访问可绑 `127.0.0.1`，再用 `adb reverse` 让模拟器连进来（debug 构建允许回环地址配对）。

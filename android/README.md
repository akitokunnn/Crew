# Crew Android

1. 下载并校验资源：`python android/scripts/fetch_assets.py`（Mac/Linux 用 `python3`，需要 Python 3.11+）。
2. 设置环境：Windows PowerShell 执行 `. C:\Tools\android-work\env.ps1`。Mac/Linux 把 `JAVA_HOME` 设为本机 JDK 21，把 `ANDROID_HOME` 设为已经装好 Android 36 平台和 Build Tools 36.0.0 的 SDK 路径。
3. 构建：`android\gradlew.bat -p android assembleDebug`（Mac/Linux：`sh android/gradlew -p android assembleDebug`）。

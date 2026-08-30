@echo off
setlocal
set "JAVA_HOME="
for /d %%D in ("%ProgramFiles%\Microsoft\jdk-17*") do (
  if exist "%%~fD\bin\java.exe" set "JAVA_HOME=%%~fD"
)

if not defined JAVA_HOME (
  echo Java 17 tidak ditemukan.
  echo Install Microsoft OpenJDK 17 lalu jalankan skrip ini lagi.
  pause
  exit /b 1
)

set "ANDROID_SDK_ROOT=%LOCALAPPDATA%\Android\Sdk"
if not exist "%ANDROID_SDK_ROOT%\platforms\android-36\android.jar" (
  echo Android SDK Platform 36 tidak ditemukan di:
  echo %ANDROID_SDK_ROOT%
  echo Install Android CLI dan paket platforms/android-36 terlebih dahulu.
  pause
  exit /b 1
)

set "ANDROID_HOME=%ANDROID_SDK_ROOT%"
set "PATH=%JAVA_HOME%\bin;%ANDROID_SDK_ROOT%\platform-tools;%PATH%"
cd /d "%~dp0android"

call gradlew.bat :app:assembleDebug --no-daemon
if errorlevel 1 (
  echo.
  echo Build APK gagal. Periksa pesan Gradle di atas.
  pause
  exit /b 1
)

echo.
echo APK siap: %~dp0android\app\build\outputs\apk\debug\app-debug.apk
pause

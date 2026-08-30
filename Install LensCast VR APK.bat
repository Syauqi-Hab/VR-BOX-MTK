@echo off
setlocal EnableExtensions
cd /d "%~dp0"
set "APK=%~dp0android\app\build\outputs\apk\debug\app-debug.apk"
set "ADB="

if not exist "%APK%" (
  echo APK belum dibuat. Jalankan "Build LensCast VR APK.bat" terlebih dahulu.
  pause
  exit /b 1
)

for /f "delims=" %%A in ('where adb 2^>nul') do if not defined ADB set "ADB=%%A"
if not defined ADB if exist "%LOCALAPPDATA%\Android\Sdk\platform-tools\adb.exe" set "ADB=%LOCALAPPDATA%\Android\Sdk\platform-tools\adb.exe"

if not defined ADB (
  echo ADB tidak ditemukan di PATH.
  echo Aktifkan USB debugging dan pasang Android Platform Tools terlebih dahulu.
  pause
  exit /b 1
)

"%ADB%" start-server
"%ADB%" devices
"%ADB%" install -r -t "%APK%"
if errorlevel 1 (
  echo Instalasi APK gagal. Pastikan prompt USB debugging di HP sudah diterima.
  pause
  exit /b 1
)

"%ADB%" reverse tcp:8264 tcp:8264
if errorlevel 1 (
  echo USB tunnel gagal. Pastikan USB debugging sudah diizinkan di HP.
  pause
  exit /b 1
)

echo.
echo LensCast VR sudah terpasang. Jalankan aplikasi di HP setelah LensCast PC aktif.
pause

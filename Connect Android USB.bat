@echo off
setlocal EnableExtensions
set "ADB="
for /f "delims=" %%A in ('where adb 2^>nul') do if not defined ADB set "ADB=%%A"
if not defined ADB if exist "%LOCALAPPDATA%\Android\Sdk\platform-tools\adb.exe" set "ADB=%LOCALAPPDATA%\Android\Sdk\platform-tools\adb.exe"

if not defined ADB (
  echo ADB tidak ditemukan di PATH.
  echo Install Android Platform Tools, lalu coba lagi.
  pause
  exit /b 1
)

echo.
echo Pastikan LensCast sudah berjalan di port 8264.
echo Hubungkan Android melalui USB-C dan aktifkan USB debugging.
echo.
"%ADB%" start-server
"%ADB%" devices
echo.
"%ADB%" reverse tcp:8264 tcp:8264
if errorlevel 1 (
  echo.
  echo ADB reverse gagal. Izinkan prompt USB debugging di HP lalu jalankan ulang.
  pause
  exit /b 1
)

echo.
echo USB tunnel siap.
echo Buka http://127.0.0.1:8264/phone di browser HP.
pause

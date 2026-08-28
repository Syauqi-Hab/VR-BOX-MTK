@echo off
setlocal
where adb >nul 2>&1
if errorlevel 1 (
  echo ADB tidak ditemukan di PATH.
  echo Install Android Platform Tools, lalu coba lagi.
  pause
  exit /b 1
)

echo.
echo Pastikan LensCast sudah berjalan di port 8264.
echo Hubungkan Android melalui USB-C dan aktifkan USB debugging.
echo.
adb start-server
adb devices
echo.
adb reverse tcp:8264 tcp:8264
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

@echo off
setlocal
cd /d "%~dp0"

py -3.12 -m PyInstaller --version >nul 2>&1
if errorlevel 1 (
  echo.
  echo PyInstaller belum tersedia. Mengunduh paket build sekali saja...
  py -3.12 -m pip install pyinstaller
  if errorlevel 1 (
    echo Gagal memasang PyInstaller.
    pause
    exit /b 1
  )
)

py -3.12 -m PyInstaller --noconfirm --clean --onedir --windowed --name "LensCast PC" --add-data "static;static" lenscast_pc.py
if errorlevel 1 (
  echo.
  echo Build EXE gagal. Periksa pesan di atas.
  pause
  exit /b 1
)

echo.
echo EXE siap: %~dp0dist\LensCast PC\LensCast PC.exe
pause

@echo off
setlocal
cd /d "%~dp0"

set "OUTPUT_DIR=%~dp0dist\LensCast PC"
set "BACKUP_DIR=%~dp0build\lenscast-settings-backup"
set "HAS_SETTINGS=0"
set "HAS_PROFILES=0"

if not exist "%BACKUP_DIR%" mkdir "%BACKUP_DIR%"
if exist "%OUTPUT_DIR%\lenscast-settings.json" (
  copy /y "%OUTPUT_DIR%\lenscast-settings.json" "%BACKUP_DIR%\lenscast-settings.json" >nul
  set "HAS_SETTINGS=1"
)
if exist "%OUTPUT_DIR%\lenscast-lens-profiles.json" (
  copy /y "%OUTPUT_DIR%\lenscast-lens-profiles.json" "%BACKUP_DIR%\lenscast-lens-profiles.json" >nul
  set "HAS_PROFILES=1"
)

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

py -3.12 -m PyInstaller --noconfirm --clean --onedir --windowed --name "LensCast PC" --add-data=static:static lenscast_pc.py
if errorlevel 1 (
  echo.
  echo Build EXE gagal. Periksa pesan di atas.
  pause
  exit /b 1
)

if "%HAS_SETTINGS%"=="1" copy /y "%BACKUP_DIR%\lenscast-settings.json" "%OUTPUT_DIR%\lenscast-settings.json" >nul
if "%HAS_PROFILES%"=="1" copy /y "%BACKUP_DIR%\lenscast-lens-profiles.json" "%OUTPUT_DIR%\lenscast-lens-profiles.json" >nul

echo.
echo EXE siap: %~dp0dist\LensCast PC\LensCast PC.exe
pause

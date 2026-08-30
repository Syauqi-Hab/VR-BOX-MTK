@echo off
setlocal
cd /d "%~dp0"

py -3.12 -c "import cv2, dxcam, PIL" >nul 2>&1
if errorlevel 1 (
  echo.
  echo LensCast membutuhkan Python 3.12 dengan Pillow, dxcam, dan OpenCV.
  echo Jalankan: py -3.12 -m pip install -r requirements.txt
  echo.
  pause
  exit /b 1
)

py -3.12 app.py

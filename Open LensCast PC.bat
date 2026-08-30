@echo off
setlocal
cd /d "%~dp0"

py -3.12 -c "import cv2, dxcam, PIL, tkinter" >nul 2>&1
if errorlevel 1 (
  echo.
  echo LensCast PC membutuhkan Python 3.12 dengan Pillow, dxcam, OpenCV, dan Tkinter.
  echo Jalankan: py -3.12 -m pip install -r requirements.txt
  echo.
  pause
  exit /b 1
)

py -3.12 lenscast_pc.py

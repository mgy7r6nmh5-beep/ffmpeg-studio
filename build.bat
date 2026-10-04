@echo off
REM ============================================================
REM  FFmpeg Studio - build single-file exe (PyInstaller)
REM  Output: dist\FFmpegStudio.exe
REM ============================================================
setlocal
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
  echo [ERROR] python not found in PATH.
  exit /b 1
)

echo [1/2] Installing dependencies...
python -m pip install -r requirements.txt pyinstaller
if errorlevel 1 exit /b 1

echo.
echo [2/2] Building...
python -m PyInstaller --noconfirm --clean FFmpegStudio.spec
if errorlevel 1 exit /b 1

echo.
echo Done: dist\FFmpegStudio.exe
pause

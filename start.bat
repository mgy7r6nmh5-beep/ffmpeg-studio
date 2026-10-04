@echo off
title FFmpeg Studio
echo Starting FFmpeg Studio...
echo Browser will open at http://localhost:8787
echo Close this window to stop the server.
echo.
start http://localhost:8787
"%~dp0FFmpegStudio.exe"
pause
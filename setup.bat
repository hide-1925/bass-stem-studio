@echo off
rem Bass Stem Studio setup. Add -Gpu for an NVIDIA GPU build of torch:  setup.bat -Gpu
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup.ps1" %*
pause

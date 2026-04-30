@echo off
REM Ensure exit code propagation: this script exits with the underlying command's exit code so it can be used with && in cmd (e.g. script1.bat && script2.bat)
pushd %~dp0\.. || exit /b 1
python -m "scripts.update_dependencies" %*
set "EXIT_CODE=%ERRORLEVEL%"
popd
exit /b %EXIT_CODE%

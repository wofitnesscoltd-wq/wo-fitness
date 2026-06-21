@echo off
REM Double-click this. No typing. It runs the full self-validation.
cd /d "%~dp0"
set PY=
where py >nul 2>nul && set PY=py
if "%PY%"=="" ( where python >nul 2>nul && set PY=python )
if "%PY%"=="" ( where python3 >nul 2>nul && set PY=python3 )
if "%PY%"=="" (
  echo.
  echo [X] Python not found. Install from https://www.python.org/downloads/
  echo     During install, tick "Add python.exe to PATH".
  echo.
  pause
  exit /b 1
)
echo ============================================
echo   Self-Validation ^(Monte Carlo, no input^)
echo   Running... takes about 10-15 seconds.
echo ============================================
echo.
%PY% backtest_montecarlo.py
echo.
echo ============================================
echo   Done. Read the verdict above.
echo ============================================
pause

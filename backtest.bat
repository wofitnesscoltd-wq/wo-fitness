@echo off
cd /d "%~dp0"
set PY=python
where python >nul 2>nul || set PY=py
echo ==========================================
echo   Confluence Signal Backtest
echo ==========================================
echo.
echo Press Enter for BTCUSDT, or type a symbol.
echo Examples: ETHUSDT  SOLUSDT  QCOMUSDT  RKLBUSDT
echo.
set "SYM="
set /p "SYM=Symbol: "
if "%SYM%"=="" set "SYM=BTCUSDT"
set "SRC="
set /p "SRC=Source binance or bitget (Enter=binance): "
if "%SRC%"=="" set "SRC=binance"
echo.
echo Running %SYM% %SRC% daily backtest...
echo.
%PY% backtest_signals.py --crypto %SYM% --source %SRC% --interval 1d --limit 1000
echo.
pause

@echo off
chcp 65001 >nul
cd /d "%~dp0"
where python >nul 2>nul && (set PY=python) || (set PY=py)
echo ============================================
echo  共振進出場 回測 - BTCUSDT 日線(預設)
echo ============================================
%PY% backtest_signals.py --crypto BTCUSDT --interval 1d --limit 1000
echo.
echo 要測其他標的，把指令裡的 BTCUSDT 換掉，例如:
echo   %PY% backtest_signals.py --crypto ETHUSDT --interval 1d --limit 1000
echo   %PY% backtest_signals.py --crypto QCOMUSDT --source bitget --interval 1d --limit 800
echo   美股(要先開橋接): %PY% backtest_signals.py --bridge http://127.0.0.1:8888 --token 你的TOKEN --code US.NVDA --ktype day --num 800
echo.
pause

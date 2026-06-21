@echo off
chcp 65001 >nul
cd /d "%~dp0"
where python >nul 2>nul && (set PY=python) || (set PY=py)
:menu
cls
echo ===================================================
echo            共振進出場  歷史回測
echo ===================================================
echo.
echo  直接按 Enter = 測 BTCUSDT(幣安)
echo  或輸入幣種，例如 ETHUSDT、SOLUSDT
echo  你的 tokenized 美股永續(COHRUSDT/QCOMUSDT/RKLBUSDT)請打代號，來源那關打 bitget
echo.
set "SYM="
set /p SYM=幣種: 
if "%SYM%"=="" set SYM=BTCUSDT
set "SRC="
set /p SRC=來源(binance 或 bitget，直接 Enter=binance): 
if "%SRC%"=="" set SRC=binance
echo.
echo --- 跑 %SYM% (%SRC%) 日線回測中，稍等幾秒 ---
echo.
%PY% backtest_signals.py --crypto %SYM% --source %SRC% --interval 1d --limit 1000
echo.
echo ===================================================
set "AGAIN="
set /p AGAIN=再測一個按 Enter，結束打 q 再 Enter: 
if /i not "%AGAIN%"=="q" goto menu

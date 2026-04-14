@echo off
REM V11 Auto-Restart Wrapper with Gateway Management
REM
REM This script keeps V11 running 24/5 with full Gateway lifecycle:
REM   - Before starting V11: ensures Gateway is running (via IBC if needed)
REM   - If V11 exits with error code: restarts Gateway + V11
REM   - IBC handles login dialogs, 2FA, and weekly re-authentication
REM
REM Exit code 0 = clean shutdown (Ctrl+C), don't restart.
REM Exit code 1 = crash or emergency, restart.
REM
REM Prerequisites:
REM   1. Install IBC from https://github.com/IbcAlpha/IBC/releases
REM   2. Configure C:\IBC\config.ini with IBKR credentials
REM   3. Run: python -m v11.live.gateway_manager  (to verify setup)
REM
REM Usage: start_v11.bat [--dry-run] [--live]

:loop
echo [%date% %time%] Ensuring Gateway is running...
python -m v11.live.gateway_manager --check
if %errorlevel% NEQ 0 (
    echo [%date% %time%] Gateway not running. Starting via IBC...
    start "" /B python -m v11.live.gateway_manager
    echo [%date% %time%] Waiting 120s for Gateway to start and complete login/2FA...
    timeout /t 120
)

echo [%date% %time%] Starting V11...
python -m v11.live.run_live %*
set EXIT_CODE=%errorlevel%
echo [%date% %time%] V11 exited with code %EXIT_CODE%

if %EXIT_CODE% EQU 0 (
    echo V11 stopped cleanly. Not restarting.
    goto end
)

echo V11 crashed or emergency shutdown.
echo Checking if Gateway is still running...
python -m v11.live.gateway_manager --check
if %errorlevel% NEQ 0 (
    echo Gateway is down. Restarting Gateway + V11 in 60 seconds...
    timeout /t 60
) else (
    echo Gateway is still running. Restarting V11 in 30 seconds...
    timeout /t 30
)
goto loop

:end
echo V11 wrapper stopped.

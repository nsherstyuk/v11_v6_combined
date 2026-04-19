@echo off
REM V11 Full-Stop Script
REM
REM Kills: V11 main process, GatewayManager watchdog, IBC launcher, IB Gateway
REM Use this instead of just Ctrl+C on start_v11.bat, because the
REM GatewayManager watchdog runs in its own minimized window and
REM survives Ctrl+C on the main V11 terminal — meaning Gateway
REM will keep auto-restarting until you kill the watchdog too.
REM
REM Usage:  v11\live\stop_v11.bat

echo Stopping V11 stack...
echo.

REM 1. GatewayManager watchdog (the thing that auto-restarts Gateway)
for /f "tokens=2" %%p in ('tasklist /fi "WINDOWTITLE eq GatewayManager" /fo table /nh 2^>nul ^| findstr python') do (
    echo   Killing GatewayManager watchdog PID %%p
    taskkill /F /PID %%p >nul 2>&1
)
REM Fallback: kill any python process running gateway_manager
for /f "tokens=2 delims==" %%p in ('wmic process where "CommandLine like '%%gateway_manager%%'" get ProcessId /format:value 2^>nul ^| findstr /r "[0-9]"') do (
    echo   Killing stray gateway_manager PID %%p
    taskkill /F /PID %%p >nul 2>&1
)

REM 2. V11 main (run_live)
for /f "tokens=2 delims==" %%p in ('wmic process where "CommandLine like '%%run_live%%'" get ProcessId /format:value 2^>nul ^| findstr /r "[0-9]"') do (
    echo   Killing V11 main PID %%p
    taskkill /F /PID %%p >nul 2>&1
)

REM 3. IBC launcher (the cmd window that ran DisplayBannerAndLaunch.bat)
for /f "tokens=2 delims==" %%p in ('wmic process where "CommandLine like '%%DisplayBannerAndLaunch%%'" get ProcessId /format:value 2^>nul ^| findstr /r "[0-9]"') do (
    echo   Killing IBC launcher PID %%p
    taskkill /F /PID %%p >nul 2>&1
)

REM 4. IB Gateway Java process (contains IBC automation)
for /f "tokens=2 delims==" %%p in ('wmic process where "CommandLine like '%%ibgateway%%'" get ProcessId /format:value 2^>nul ^| findstr /r "[0-9]"') do (
    echo   Killing IB Gateway PID %%p
    taskkill /F /PID %%p >nul 2>&1
)

REM 5. Delete the lock file (so a clean restart won't complain)
del /Q "%~dp0.gateway_manager.lock" 2>nul

echo.
echo V11 stack fully stopped. Port 4002 will be free in ~30s.
echo To restart, run: v11\live\start_v11.bat --live --no-llm

@echo off
title Restaurant Management System
cd /d "%~dp0"

echo =======================================================================
echo  Starting Restaurant Management System
echo  Desktop Local Access: http://127.0.0.1:8000/
echo  Checking port 8000 and freeing stale background processes...
echo =======================================================================

:: Free port 8000 if occupied by any stale background process
for /f "tokens=5" %%a in ('netstat -aon ^| findstr ":8000" ^| findstr "LISTENING" 2^>nul') do (
    taskkill /F /PID %%a >nul 2>&1
)

:: Activate Python virtual environment
call venv\Scripts\activate.bat

:: Launch ASGI / Daphne Server on all network interfaces (0.0.0.0:8000)
:: Prefer C:\python314\python.exe if available (already authorized in Windows Firewall)
if exist "C:\python314\python.exe" (
    C:\python314\python.exe manage.py runserver
) else (
    python manage.py runserver
)
pause

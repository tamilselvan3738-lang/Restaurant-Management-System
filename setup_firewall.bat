@echo off
title Allow Port 8000 in Windows Firewall
echo =======================================================================
echo  Adding Windows Firewall Rule for Port 8000 and Python
echo =======================================================================

netsh advfirewall firewall add rule name="RMS Port 8000" dir=in action=allow protocol=TCP localport=8000 profile=any >nul 2>&1
if %errorlevel% neq 0 (
    echo Requesting Administrator privileges to add firewall rule...
    powershell -Command "Start-Process cmd -ArgumentList '/c netsh advfirewall firewall add rule name=\"RMS Port 8000\" dir=in action=allow protocol=TCP localport=8000 profile=any && netsh advfirewall firewall add rule name=\"RMS Python Venv\" dir=in action=allow program=\"%~dp0venv\Scripts\python.exe\" profile=any' -Verb RunAs"
) else (
    netsh advfirewall firewall add rule name="RMS Python Venv" dir=in action=allow program="%~dp0venv\Scripts\python.exe" profile=any >nul 2>&1
    echo Success: Windows Firewall rule added!
)

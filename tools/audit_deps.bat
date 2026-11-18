@echo off
rem Checks the installed packages against the public vulnerability database. Needs internet once.
cd /d "%~dp0.."
venv\Scripts\python -m pip install --quiet pip-audit
venv\Scripts\python -m pip_audit -r requirements.txt
pause

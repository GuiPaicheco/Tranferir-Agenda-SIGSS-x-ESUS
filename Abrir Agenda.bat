@echo off
cd /d "%~dp0"
if exist ".venv\agenda-transferencia-pronta" goto open
if exist ".venv\Scripts\python.exe" goto dependencies
where py >nul 2>&1 || goto missing
py -m venv .venv || goto failed
:dependencies
".venv\Scripts\python.exe" -c "import playwright.sync_api, google_auth_oauthlib, googleapiclient" >nul 2>&1 && goto ready
".venv\Scripts\python.exe" -m pip install playwright google-auth-oauthlib google-api-python-client || goto failed
:ready
type nul > ".venv\agenda-transferencia-pronta"
goto open
:missing
echo Instale o Python para Windows e abra este arquivo novamente.
pause
exit /b 1
:failed
echo A instalacao falhou. Confira a conexao e tente novamente.
pause
exit /b 1
:open
start "" ".venv\Scripts\pythonw.exe" app.py

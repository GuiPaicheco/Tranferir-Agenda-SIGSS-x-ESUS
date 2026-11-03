@echo off
cd /d "%~dp0"
if exist ".venv\agenda-pronta" goto open
where py >nul 2>&1 || goto missing
py -m venv .venv || goto failed
".venv\Scripts\python.exe" -m pip install playwright || goto failed
".venv\Scripts\python.exe" -m playwright install chromium || goto failed
type nul > ".venv\agenda-pronta"
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

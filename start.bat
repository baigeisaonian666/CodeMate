@echo off
cd /d "%~dp0"
if not exist ".tmp" mkdir .tmp
set "TEMP=%~dp0.tmp"
set "TMP=%TEMP%"
if not exist ".venv\Scripts\python.exe" (
  python -m venv .venv
)
".venv\Scripts\python.exe" -c "import fastapi,langgraph,langchain_openai,mcp" >nul 2>nul
if errorlevel 1 (
  ".venv\Scripts\python.exe" -m ensurepip
  ".venv\Scripts\python.exe" -m pip install --no-cache-dir -r requirements.txt
  if errorlevel 1 (
    pause
    exit /b 1
  )
)
echo Open http://127.0.0.1:8000
".venv\Scripts\python.exe" -m uvicorn app.main:app --host 127.0.0.1 --port 8000
pause

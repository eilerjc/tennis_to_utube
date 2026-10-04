@echo off
rem Start Tennis to YouTube. First run creates .venv and installs what it needs.
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
    echo Setting up Python environment, this takes a minute...
    py -3 -m venv .venv || python -m venv .venv || goto :nopython
    .venv\Scripts\python -m pip install --upgrade pip
    .venv\Scripts\python -m pip install -e .[gui] || goto :failed
)
.venv\Scripts\python -m tennis_to_utube %*
if errorlevel 1 pause
goto :eof
:nopython
echo Python 3.11 or newer is needed: https://www.python.org/downloads/
pause
goto :eof
:failed
echo Installing failed; see the messages above.
pause

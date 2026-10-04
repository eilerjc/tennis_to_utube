@echo off
rem Trim tool: drop a .match.json file on this (or: trim.bat file.match.json [--cuts]).
rem Makes "<match> trimmed.mp4" (lossless) and its chapters and links next to the match.
rem Settings: %APPDATA%\tennis_to_utube\trim.toml (see docs\trim.example.toml).
rem Run run.bat once first to set up Python.
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
    echo Run run.bat once first to set up Python.
    pause
    goto :eof
)
.venv\Scripts\python -m tennis_to_utube.trimtool %*
pause

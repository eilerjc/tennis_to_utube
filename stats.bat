@echo off
rem Match statistics: drop a .match.json file on this (or: stats.bat file.match.json).
rem Writes "<match> stats.csv" next to it. Run run.bat once first to set up Python.
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
    echo Run run.bat once first to set up Python.
    pause
    goto :eof
)
.venv\Scripts\python -m tennis_to_utube.stats %*
pause

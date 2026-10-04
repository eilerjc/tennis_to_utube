@echo off
rem Overlay tool: drop a .match.json file on this (or: overlay.bat file.match.json [--full]
rem [--preview 1:02:30] [--png 1:02:30]). Burns a scoreboard into "<match> trimmed.mp4"
rem (made by the Trim tool) and writes "<match> trimmed overlay.mp4" next to the match.
rem Settings: %APPDATA%\tennis_to_utube\overlay.toml (see docs\overlay.example.toml).
rem Run run.bat once first to set up Python.
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
    echo Run run.bat once first to set up Python.
    pause
    goto :eof
)
.venv\Scripts\python -c "import PIL" 2>nul || .venv\Scripts\python -m pip install -e .[overlay]
.venv\Scripts\python -m tennis_to_utube.overlay %*
pause

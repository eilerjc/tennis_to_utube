@echo off
rem Cut only: drop one or more videos on this (or: cut.bat a.mp4 b.mp4). They open joined
rem in order; mark the parts to remove (I ... O) and make the video (lossless). Nothing tennis,
rem nothing saved but the video. Without videos it opens on Files: tick them, "Cut only".
rem Run run.bat once first to set up Python.
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
    echo Run run.bat once first to set up Python.
    pause
    goto :eof
)
.venv\Scripts\python -m tennis_to_utube --cut %*
if errorlevel 1 pause

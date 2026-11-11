@echo off
rem Upload tool: drop one or more videos on this to upload them to YouTube, or:
rem   upload.bat --connect                                   sign in to YouTube (once)
rem   upload.bat h1.mp4 h2.mp4 --game "Hawks" "Owls" --kids no
rem   upload.bat --run                                       carry on with the queue
rem   upload.bat --status
rem Uploads resume from their last checkpoint after any interruption or restart.
rem Settings: %APPDATA%\tennis_to_utube\upload.toml (see docs\upload.example.toml and
rem docs\UPLOAD_SETUP.md). The app's Upload step does the same with a dashboard.
rem Run run.bat once first to set up Python.
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
    echo Run run.bat once first to set up Python.
    pause
    goto :eof
)
.venv\Scripts\python -m tennis_to_utube.uploadtool %*
pause

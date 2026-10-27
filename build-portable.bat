@echo off
rem Builds SidebarStudy-portable.exe: one file with the decks, speech models and icon inside.
rem Copy it to any Windows PC and run it; progress, settings and your YoYo sign-in are saved
rem in the same folder as the exe. It holds your YoYo decks, so keep it private (it's gitignored).
cd /d "%~dp0"
.venv\Scripts\pyinstaller --noconfirm --clean --windowed --onefile --name SidebarStudy-portable --icon "%~dp0icon.ico" --add-data "%~dp0flashcards;flashcards" --add-data "%~dp0models;models" --add-data "%~dp0icon.ico;." --collect-all vosk --collect-all pypinyin --collect-binaries sounddevice --collect-data _sounddevice_data --hidden-import comtypes.client --distpath "%~dp0." --workpath "%TEMP%\sidebarstudy_portable" --specpath "%TEMP%\sidebarstudy_portable" app.py
pause

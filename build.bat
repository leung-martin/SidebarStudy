@echo off
rem Rebuilds SidebarStudy\SidebarStudy.exe from app.py. Close the app first.
cd /d "%~dp0"
.venv\Scripts\pyinstaller --noconfirm --clean --windowed --onedir --name SidebarStudy --icon "%~dp0icon.ico" --collect-all vosk --collect-all pypinyin --collect-binaries sounddevice --collect-data _sounddevice_data --hidden-import comtypes.client --distpath "%~dp0." --workpath "%TEMP%\sidebarstudy_build" --specpath "%TEMP%\sidebarstudy_build" app.py
pause


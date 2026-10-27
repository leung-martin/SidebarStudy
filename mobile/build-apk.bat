@echo off
rem Builds the Android app and copies it to ..\SidebarStudy.apk. If the phone is plugged in
rem with USB debugging on, it is installed straight away.
cd /d "%~dp0"
set "JAVA_HOME=C:\Program Files\Eclipse Adoptium\jdk-21.0.12.101-hotspot"
set "ANDROID_HOME=%LOCALAPPDATA%\Android\Sdk"
call npx vite build || goto :fail
call npx cap sync android || goto :fail
pushd android
call gradlew.bat assembleDebug --no-daemon -q || (popd & goto :fail)
popd
copy /y android\app\build\outputs\apk\debug\app-debug.apk ..\SidebarStudy.apk >nul
echo Built ..\SidebarStudy.apk
"%ANDROID_HOME%\platform-tools\adb.exe" get-state >nul 2>&1 && "%ANDROID_HOME%\platform-tools\adb.exe" install -r ..\SidebarStudy.apk
pause
exit /b 0
:fail
echo Build failed.
pause
exit /b 1

@echo off
rem Builds ..\SidebarStudy-wear.apk for a Wear OS watch (Galaxy Watch 4 or newer).
rem Needs Node.js (for the grading bundle), JDK 21 and the Android SDK, plus your decks in ..\flashcards.
cd /d "%~dp0"
if not defined JAVA_HOME set "JAVA_HOME=C:\Program Files\Eclipse Adoptium\jdk-21.0.12.101-hotspot"

rem 1. the phone app's grading code, bundled for the watch's JavaScript engine
pushd ..\mobile
call npm run build:watch || goto :fail
popd

rem 2. your YoYo decks (not in the repo)
if not exist app\src\main\assets\decks mkdir app\src\main\assets\decks
copy /y ..\flashcards\*.json app\src\main\assets\decks\ >nul || goto :fail

rem 3. the app
call "%~dp0gradlew.bat" :app:assembleDebug || goto :fail
copy /y app\build\outputs\apk\debug\app-debug.apk ..\SidebarStudy-wear.apk >nul
echo Built ..\SidebarStudy-wear.apk
exit /b 0

:fail
echo Build failed.
exit /b 1

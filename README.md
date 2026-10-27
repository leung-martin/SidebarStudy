# Sidebar Study

Mandarin flash cards you answer out loud, synced with your [YoYo Chinese](https://yoyochinese.com)
flashcard mastery. Two apps share the same grading rules and YoYo sync:

- **Desktop (Windows)** — an always-on-top window to keep beside a game. Offline speech
  recognition (Vosk), global hotkeys, mic auto-sleep, a mastery explorer page.
- **Android** (`mobile/`) — a Capacitor app for the desk at work. Google speech recognition,
  tap the card to talk.

Both send your answers to YoYo (Easy +2 / Not Bad +1 / Forgot → 0) and study YoYo's due
reviews first. YoYo is the source of truth; every send is checked so cards you didn't answer
can't lose level.

## Your flash cards

The card decks are YoYo Chinese's course material and are **not** in this repo. Put your own
exported decks (`yoyochinese_*_flashcards.json`) in `flashcards/`, and copy them to
`mobile/public/decks/` for the Android app.

## Desktop setup

```powershell
py -3.12 -m venv .venv
.venv\Scripts\pip install -r requirements.txt pyinstaller comtypes
# speech models (≈80 MB), unzipped into models/
#   https://alphacephei.com/vosk/models/vosk-model-small-cn-0.22.zip
#   https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip
.venv\Scripts\python app.py      # or build.bat for SidebarStudy\SidebarStudy.exe
```

Sign in to YoYo from ⚙ → YoYo sync → Sign in… (paste your yoyochinese.com session cookie).
Hotkeys: Ctrl+Alt+M mic on/off · Ctrl+Alt+H hear it · Ctrl+Alt+G got it.

## Android setup

Needs Node.js, JDK 21 and the Android SDK.

```powershell
cd mobile
npm install
build-apk.bat        # builds ..\SidebarStudy.apk and installs it if a phone is connected
```

Sign in from ⚙ with your YoYo email and password (set one with YoYo's "Forgot your password?"
if you use Google sign-in). `mobile/plugins/speech-recognition` is a patched copy of
`@capacitor-community/speech-recognition` (see the `patched:` comments) that keeps listening
reliably on Android.

## Tests

```powershell
cd mobile
..\.venv\Scripts\python test\make_cases.py   # needs your decks: compares the JS grading with the desktop app
node test\grading.test.mjs
node test\yoyo.test.mjs                      # fake YoYo; the last part reads your real account (read-only)
```

# Jarvix setup and use

## Update the existing source

Exit Jarvix from its tray menu or console. Extract the release ZIP into a separate folder and open PowerShell there. The first command verifies the update. The second backs up and replaces source files.

~~~powershell
$JarvixProject = 'C:\Users\Rithwik\Jarvix'
& "$JarvixProject\.venv\Scripts\python.exe" .\scripts\apply_update.py --project $JarvixProject --check
& "$JarvixProject\.venv\Scripts\python.exe" .\scripts\apply_update.py --project $JarvixProject
& "$JarvixProject\.venv\Scripts\python.exe" "$JarvixProject\main.py"
~~~

Add **--console** to the last command for text-only mode. Run as a standard user. The app uses **%LOCALAPPDATA%\Jarvix** for saved tasks, reminders, and settings. The desktop and console share the profile.

## Install the Windows release per user

From the extracted folder, use your existing Python 3.12 interpreter. The installer verifies archive files and creates a virtual environment inside the per-user installation. A Python package index is needed for missing dependencies.

~~~powershell
py -3.12 .\scripts\install.py --check
py -3.12 .\scripts\install.py
& "$env:LOCALAPPDATA\Programs\Jarvix\Jarvix.exe"
~~~

Add **--optional** on a fresh install to include voice, Gmail, and Android listener dependencies. The same packages can be installed individually into the original project virtual environment from **requirements-optional.txt**.

## Desktop controls

| Page | What you can do |
| --- | --- |
| Home | Enter commands, review transcripts, see system metrics, start workflows, and pause Jarvix |
| Tasks | Pick a date, add or reschedule items, mark them done, and preview deletion |
| Workflows | Create and rename routines, add or reorder steps, save, run, and stop |
| Apps | Search approved locations, open a selected location, approve eligible Start Menu apps, and save aliases |
| Music | Choose a local music folder, play supported tracks, or choose a YouTube search for a missing track |
| Diagnostics | Inspect status, run an explicit internet check, and copy or save a redacted report |

To use workflow dialogue, enter **Create workflow Deep Work**, then **add open_app Notepad**, then **add start_timer 25 Study**, and then **save workflow**.

Downloads cleanup lists regular .exe and .msi files older than 90 days. Applying the plan requires typing **yes** after the preview and the modern Windows Recycle Bin dependency.

## Optional features

- **Voice** — Select Google recognition or Vosk in Settings. Google receives the audio clip when requested. Vosk needs the optional package and a compatible extracted model. The downloader checks the SHA-256 of small official models. Review or edit transcripts before sending commands. Speech output needs pyttsx3 and a Windows voice.
- **Languages** — Select one of eight packs in Settings. Common labels, commands, digits, and date phrases are localized. English is the fallback for untranslated details. Machine-translated text needs native review.
- **Gmail** — Enable the Gmail API in your Google Cloud project and create a Desktop OAuth client. Select its JSON on the Gmail page. Read-only authorization uses the system browser, a temporary loopback callback, and Windows Credential Manager. Disconnect removes the stored connection. Manage the original grant in your Google Account. Send composed email from Gmail.
- **WhatsApp** — Save an international number per contact. Busy Mode prepares drafts for approved contacts. Start the call or send the message inside WhatsApp.
- **Brain** — Start a compatible local service and enter a loopback address and installed model name. Examples include **http://127.0.0.1:11434** for Ollama or a loopback llama.cpp chat completions endpoint. Type **Ask Jarvix explain binary search** or use the Settings test.
- **Android** — Install the optional Windows TLS and QR packages. Enter the PC's private Wi-Fi IPv4 address on the Android page, start the listener, and create a pairing invitation. Scan or paste it in the companion. Keep the companion open to receive actions. Approve file offers on the phone and save through Android's document picker.

## Build and test

~~~powershell
.\.venv\Scripts\python.exe -m pip install -r .\requirements-dev.txt
.\.venv\Scripts\python.exe -B -m pytest -q -rs -p no:cacheprovider
.\scripts\Build-Windows.ps1 -OutputDirectory .\dist\windows
~~~

Use JDK 17, Gradle 8.9, and Android SDK Platform 35 to build the companion. Set **ANDROID_HOME** to the approved SDK location and run **.\scripts\Build-Android.ps1**. It runs the Kotlin unit tests and, on success, produces **android\app\build\outputs\apk\debug\app-debug.apk**.

## Maintenance

Exit Jarvix before running maintenance. From Command Prompt, preview removing the installed application with:

~~~cmd
"%LOCALAPPDATA%\Programs\Jarvix\jarvix-maintenance.exe" uninstall --dry-run
~~~

Ordinary **uninstall** preserves the profile. To also remove the listed application-owned data:

~~~cmd
"%LOCALAPPDATA%\Programs\Jarvix\jarvix-maintenance.exe" uninstall --purge
~~~

Review the inventory and type **yes**. Changed, unknown, and locked items may be kept. The maintenance launcher can remove its own verified executable after exit. Remove the companion separately in Android Settings.

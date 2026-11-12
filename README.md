# Jarvix Personal AI 1.1.7

Your PC. Your Commands. Your Intelligence.

Jarvix is a Windows desktop and console assistant. This release continues the existing Checkpoint 5a source and adds the dashboard, workflow editor, voice controls, integrations, and Android companion source.

## Update your existing project

Exit Jarvix. Extract the release ZIP and open PowerShell in the extracted folder.

~~~powershell
$JarvixProject = 'C:\Users\Rithwik\Jarvix'
& "$JarvixProject\.venv\Scripts\python.exe" .\scripts\apply_update.py --project $JarvixProject
& "$JarvixProject\.venv\Scripts\python.exe" "$JarvixProject\main.py"
~~~

The updater checks hashes and backs up replaced source files. The existing database and virtual environment remain in place.

Try **What's my battery?**, **Remind me to study tomorrow at 7 PM**, or **Run Study workflow**. Use **Settings** to configure the study page, appearance, language, floating orb, and voice.

See [SETUP_MANUAL.md](SETUP_MANUAL.md) for the per-user installer, optional features, Android, and maintenance. See [BUILD_STATUS.md](BUILD_STATUS.md) for verified results and open items.

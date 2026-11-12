# Jarvix 1.1.7 build status

The baseline was the existing Windows Checkpoint 5a project and reliability repair. The original project denied writes in this session, so the continuation was built and tested in a writable copy.

| Checkpoint | Status |
| --- | --- |
| 0–5a | Existing core, tasks, scheduler, and Windows action packages preserved. Baseline test run recorded 195 passes and 3 skips |
| 3b | App discovery and aliases, approved-folder search, and a typed-only Downloads cleanup plan |
| 4b | Persistent workflow editor and dialogue, configurable Study page, cancellable waits, step progress and stop-on-error |
| 5b | Native Qt dashboard smoke passed with actual hardware readings, a disposable saved task, Pause and Resume, 13 pages, and a clean shutdown |
| 6 | Capture, editable transcripts, Vosk and Google adapters, wake word, model download and Windows speech controls. Live audio and model installation remain unverified |
| 7 | Local music, web handoffs, Gmail OAuth and compose, WhatsApp drafts, and a loopback Brain. Live account, audio and model verification remain open |
| 8 | Eight resources with tested essential commands, digits, and date phrases. Full translation coverage and native review remain open |
| 9 | Confirmed Windows uninstall and deferred launcher removal passed with a disposable inventory. A fresh dependency install remains unverified |
| 10 | Windows launchers, source, installer, update tools, and setup guide |
| 11 | Kotlin/Gradle source and desktop TLS listener. Python protocol fixtures cover pairing, replay, revocation, approval, retries, and transfer limits. Android APK and handset verification remain open |

Windows 11 x64 26H2 build 26300.9974 and Python 3.12.10 were used. The Windows 10 target has no live verification. The two opt-in Recycle Bin tests were skipped. The sandbox denied creation of the disposable junction fixture. The cleanup preview still works when the modern recycling dependency is absent.

The optional package index returned no distributions in this session. The Android build script stopped at its JDK 17 check. Gradle and Android SDK Platform 35 were also unavailable. Actual account authorization, live file transfers, and real user data were outside the test fixtures.

## Verification for this package

- Pytest on Windows x64 reported 228 passed and 3 skipped.
- Native Windows Qt smoke saved a disposable task, read battery and system metrics, rendered all 13 screens, exercised Pause and Resume, and exited normally.
- Python syntax verification parsed 91 files. The Android manifest parsed as XML.
- Both Windows launchers compiled as x64 executables.
- Archive and updater verification covered 127 payload files and 92 changes. The updater created a backup and was idempotent.
- Both launcher preflight checks passed. The maintenance executable previewed and removed a disposable Windows installation, preserved unknown files, and removed its own locked executable after exit.

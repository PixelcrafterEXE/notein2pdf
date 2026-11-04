# Getting a note into Notein from outside

**Verified 2026-11-04 on a Lenovo TB330FU, Notein 2.1.37.** Notein offers no API and ignores notes written straight
into Google Drive (see CLOUD_SYNC.md). What *does* work is the app's own PDF import, driven from a PC over USB debugging.
Nothing is installed on the tablet; the only artefact is one temporary file in `/sdcard/Download`, removed afterwards.

```bash
adb push "My Document.pdf" /sdcard/Download/
ID=$(adb shell 'content query --uri content://media/external/file --projection _id \
    --where "_display_name='"'"'My Document.pdf'"'"'"' | sed -E 's/.*_id=([0-9]+).*/\1/')
adb shell am start -a android.intent.action.VIEW -t application/pdf \
    -d content://media/external/file/$ID --grant-read-uri-permission \
    -n com.orion.notein.global/com.orion.notein.MainActivity
adb shell rm "/sdcard/Download/My Document.pdf"
```

What happens (all observed in the app log and on screen):
- The note appears in the library at once: **no dialog, no taps**. Title = file name without `.pdf`; one note page per PDF page.
- It lands in **the folder that is currently open** in the app (root if the library root is shown). To target a folder,
  navigate the app there first (or import and use the note's "Move" menu).
- The app then syncs it by itself within about a second: `LocalModified → RequestPush → PACKAGING → UPLOADING`, writes
  `<title>.in` to Drive and registers the note with Notein's server, so other devices on the account receive it normally.
  The new note id is in the log line `NoteCreationUseCase … NotePlaceHolder(id=<uuid>, parentId=…, title=…)`.
- The tablet must be **unlocked** (the intent is delivered to the running instance).

Pitfalls:
- A `file:///sdcard/...` URI is accepted but **silently does nothing** (the app cannot read it under scoped storage; the log stops
  after `duplicatedPdfToPrivateTempDir`). Use the `content://media/...` URI as above.
- Accepted types (intent filters, read-only `dumpsys package`): images, PDF, Word, PowerPoint. There is no filter for native `.in`.
- Notein is a premium app: `VipFeatureUseCase.doesAllowToAddMoreNotes` is evaluated on import.

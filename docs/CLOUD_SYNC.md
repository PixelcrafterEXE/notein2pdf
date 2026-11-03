# Notein Google Drive sync — what it does and what goes wrong

Notein syncs each note as one file in `My Drive/NoteInDataSync/`, named `<title>_<note id>`, **no extension**,
MIME type `application/vnd.notein-apps.dbfile`. Each file is the native zip described in [FORMAT.md](FORMAT.md).
Folders are small zip records in the same directory (hierarchy via `parentId` in each note's metadata).

## Getting the files out
- Fastest: in the Drive web UI, download the `NoteInDataSync` folder as a zip (one request), then
  `notein2pdf-batch <unzipped NoteInDataSync> <out>`.
- With `rclone`: use **your own OAuth client id**. rclone's shared default client shares one Google API quota
  between all users, which returns `403 rateLimitExceeded` and slows every file to seconds (≈ 1 MB/s for ~1000 files).
  Use a `drive.readonly` scope; files can also be fetched by id with `rclone backend copyid`.

## Sync problems seen in the app log (`Android/data/com.orion.notein.global/files/Documents/logs/`)
- **"another device is currently syncing, try again later"** = HTTP 409 `LockBusy` from Notein's own sync server:
  one lock (5-minute lease) per Drive account. It clears by itself; it is not a Drive error.
- **One note never uploads, retried forever**: Drive rejects the upload with `403 propertyLengthLimitExceeded`
  (properties ≤ 124 bytes incl. key). A **note title of 165 bytes** triggered it; all uploaded notes had titles ≤ 80 bytes.
  Fix: shorten the title in Notein (stay well below ~100 characters).
- `UnknownHostException` bursts = the tablet briefly lost DNS (Wi-Fi); the app retries.
- The app lists sync "Issues" with a "Manual sync" button per note; "Synced N of M" counts files in Drive.

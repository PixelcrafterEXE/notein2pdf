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

## How Notein discovers notes (corrected after testing — 2026-11-04)
A note exists for the app only if **both** are true: the server knows its metadata *and* a matching file exists in Drive.
- During a sync the app (1) reads a **Drive change feed** of file snapshots (`GoogleDriveBackend.fetchFileSnapshots`, cursor-based, e.g.
  "1027 changes -> 1026 updated"), (2) fetches note metadata from **Notein's server** (`GET /cloudsync/v1/drives/<account>/notes/incremental?cursor=N&limit=100`)
  and (3) joins them by note id ("fetched 1026 drive files, 1023 cloud metas, total 1023 unique metaIds").
- After uploading a note it registers the metadata: `PUT /cloudsync/v1/drives/<account>/notes/<id>/meta`, under the per-account lock.
- **Experiment:** a correctly built note file (same properties as Notein's own: `title, noteId, parentId, inTrashBin, favorite, la,
  mimeType, dbVersion=31, UOP, lastModificationTime`) was created in `NoteInDataSync` with a separate OAuth client. After "Sync Now" the app
  reported "Synced 1221 of 1221" (Drive held 1222) and never showed the note: nothing in the log mentions it. Not established: whether the
  file was missing from the Drive feed (Notein may hold only the `drive.file` scope, which hides files created by other apps) or was dropped
  for lacking server metadata. Either way, direct Drive injection does not work.
- Two Notein clients on one account sync via exactly this server+Drive pair, which is why phone <-> tablet works. A *separate* Google account
  (here: the tablet uses `noteinexporter@…`) is a separate sync universe.
- **What works:** driving a Notein client — see IMPORT_INTO_NOTEIN.md.

## Upload protocol (observed in the app log, 2026-11-04; request/response *bodies* are not logged)
Creating or changing a note is this sequence, all under the per-account lock (`X-Lock-Token` header, `device_info` header with brand/device_id/platform/product):

1. `POST /cloudsync/v1/locks` -> lock token (lease ~5 min)
2. `POST /cloudsync/v1/drives/<account>/notes/<noteId>/preupload` -> announces the content tag (sha256, quickXor, lastModified)
3. Upload the `.in` zip to Drive (resumable upload, `properties` as listed above, name `<title>_<noteId>`; the app then renames it to
   `<title>_<driveFileId>`; `originalFilename` keeps the first name). Drive returns the file id ("driveObjectKey").
4. `POST /cloudsync/v1/drives/<account>/notes/<noteId>/commit` -> tells the server which Drive object holds this version; the server
   creates `version=1` and advances its change-feed cursor
5. `DELETE /cloudsync/v1/locks/<account>/<token>`

Metadata-only changes (title, folder, favourite, trash) use `PUT .../notes/<noteId>/meta`. Other devices then see the note through the
server's `.../notes/incremental?cursor=N` feed plus the Drive change feed.

Findings that bound what an outside client could do:
- There is **no Drive-side flag**: an app-created file has exactly the `properties` set documented above and no `appProperties`.
  The missing piece for a note written straight to Drive is steps 1, 2 and 4 on Notein's server.
- The server authenticates with a bearer JWT (HS256) whose claims are only `exp`, `iat`, `jwtUserId`; **its lifetime is 365 days**.
  Any client that replays this API therefore acts as the logged-in app and would need that credential.

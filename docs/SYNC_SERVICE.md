# notein2pdf-sync: one-way Notein -> Nextcloud sync service

Keeps a folder in Nextcloud automatically mirroring (a chosen part of) your Notein library as PDFs,
converted with `notein2pdf`. **One-way only**: it reads Notein's own Google Drive backup (read-only)
and writes to Nextcloud. It never writes anything to Notein, your Drive account, or the tablet/phone.
See `docs/CLOUD_SYNC.md` for why the other direction (writing *into* Notein from a PC) isn't built.

## How it works, each cycle
1. `rclone sync <drive-remote>:NoteInDataSync <local cache> --update` (mirror; `sync` rather than `copy` so renamed Drive files don't leave stale copies behind) - only new/changed files are
   actually downloaded (rclone compares size+modtime first); typically well under a second when
   nothing changed, which is what lets this poll frequently without hammering Drive.
2. Every note's Notein folder path is resolved from the folder records in that same cache (Notein
   stores folders as tiny records alongside notes - see `docs/FORMAT.md`), and matched against your
   configured `mappings`. Notes outside every mapping, and notes in Notein's trash, are ignored
   completely - never downloaded content, never touched on Nextcloud.
3. For everything in scope, a plan is computed against the last-known state
   (`~/.local/state/notein2pdf/sync-state.json`):

   | Notein-side change | Nextcloud action |
   |---|---|
   | New note | convert, upload |
   | Content changed (`lastModificationTime`) | re-convert, re-upload (replaces old file if the path also changed) |
   | Renamed / moved to another mapped folder, content unchanged | **server-side move**, no re-conversion/upload |
   | Moved to trash, or moved outside every mapping | delete from Nextcloud (goes to **Nextcloud's own trash**, so it's recoverable there) |
   | Unchanged | nothing |
   | Note vanished from this cycle's listing entirely | **left untouched**, just logged as a warning - treated as a possibly-transient listing gap rather than a deletion, to avoid destructive guesses |

## Install
```bash
cd ~/Projects/Notein && .venv/bin/pip install -e .
.venv/bin/notein2pdf-sync --init          # writes ~/.config/notein2pdf/sync.json
# edit that file: mappings (Notein folder path -> Nextcloud path), rclone remote names
.venv/bin/notein2pdf-sync --dry-run       # review what it WOULD do; makes no changes on Nextcloud
.venv/bin/notein2pdf-sync --once          # do it for real, once

mkdir -p ~/.config/systemd/user
cp systemd/notein2pdf-sync.{service,timer} ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now notein2pdf-sync.timer
loginctl enable-linger "$USER"            # so it still runs when you're not logged in
journalctl --user -u notein2pdf-sync -f   # watch it
```

## Config (`~/.config/notein2pdf/sync.json`)
```json
{
  "drive_remote": "notein-drive",
  "drive_root": "NoteInDataSync",
  "nextcloud_remote": "MSCloud",
  "mappings": [{"notein_path": "Mec@TUHH/Archive", "nextcloud_path": "Documents/Mec@TUHH/Archive"}],
  "embed_source": true
}
```
- `drive_remote` / `nextcloud_remote`: rclone remote names (`rclone listremotes`). `drive_remote` needs
  only `drive.readonly` scope. **Use your own Google OAuth client id for `drive_remote`** - rclone's
  shared default client is rate-limited and makes every poll slow (see `docs/CLOUD_SYNC.md`).
- `mappings`: a list; the first prefix match wins. Matching ignores case and collapses whitespace
  (Notein folder titles sometimes carry stray spaces, e.g. the real folder here is `"Archive "`).
  Add more entries to mirror additional parts of your library.
- `embed_source`: keep `true` so each PDF can be losslessly restored to the original note with
  `pdf2notein` later (see README's "Back to Notein" section) - it costs some extra PDF size.

## Safety properties
- Read-only towards Notein/Drive: Drive is only ever the *source* of an `rclone sync` into the local cache (download); nothing is
  written to the Drive remote.
- Nextcloud deletes use `rclone deletefile`, which on a Nextcloud WebDAV backend moves the file to
  Nextcloud's own trash (recoverable in the web UI), not a hard delete.
- **Overwrites rely on Nextcloud's version history by default.** A note's target filename can already
  exist in Nextcloud before it's synced by this tool, and the mapped folders aren't exclusively owned by
  this service. Optionally set `backup_dir` (e.g. `"notein2pdf-backups"`) and every real cycle that
  overwrites a file first copies the about-to-be-replaced version into
  `<nextcloud_remote>:<backup_dir>/<run timestamp>/<same relative path>` via rclone's `--backup-dir`.
  Off by default (`null`).
- `--dry-run` runs the full plan and prints every action without touching Nextcloud or writing state,
  so you can preview before the first real run or after changing `mappings`.
- State is one small JSON file; delete it to force a full re-sync (re-converts and re-uploads
  everything currently in scope - cheap but not free for a large library).

## Known limitations
- One-way. A file moved/renamed/deleted *in Nextcloud* is not reflected back to Notein; it's not
  even read.
- `rclone deletefile` with `check=False`: a delete that fails (already gone, network blip) is logged
  as a non-fatal `rclone` error but doesn't stop the cycle.
- No push notifications: this is poll-based (the timer's `OnUnitActiveSec`), matched to Drive's cheap
  delta-listing rather than a webhook.

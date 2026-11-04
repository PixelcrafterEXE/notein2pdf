"""One-way sync: Notein -> PDF -> Nextcloud.

Reads the Google Drive folder Notein mirrors its notes to (read-only: this never writes to Notein
or to Drive), converts changed/new notes under configured folder prefixes to PDF, and uploads them
to Nextcloud via rclone's WebDAV support, mirroring Notein's own folder names. Notes outside the
configured prefixes, and notes in Notein's trash, are left alone. See docs/SYNC_SERVICE.md.

usage: notein2pdf-sync [--config PATH] [--once] [--dry-run]

Default config: ~/.config/notein2pdf/sync.json (created with --init if missing).
"""
import json
import os
import subprocess
import sys
import time
import traceback

from . import converter
from .notein_tree import norm, raw_folder_path, safe, scan_dir

DEFAULT_CONFIG_PATH = os.path.expanduser("~/.config/notein2pdf/sync.json")

DEFAULT_CONFIG = {
    "drive_remote": "notein-drive",        # rclone remote, read-only scope, pointing at the Drive root
    "drive_root": "NoteInDataSync",        # the folder Notein syncs notes into
    "cache_dir": "~/.cache/notein2pdf/drive-cache",
    "state_file": "~/.local/state/notein2pdf/sync-state.json",
    "nextcloud_remote": "MSCloud",         # rclone remote for the Nextcloud WebDAV
    "mappings": [
        {"notein_path": "Mec@TUHH/Archive", "nextcloud_path": "Documents/Mec@TUHH/Archive"},
    ],
    "poll_seconds": 60,
    "embed_source": True,                 # attach the original .in to each PDF (see provenance.py)
    "notify_progress_seconds": 15,        # desktop notification update interval during a cycle (0 = summary only)
    "backup_dir": None,                   # e.g. "notein2pdf-backups": pre-overwrite file versions kept there, dated per run
                                           # (set to null/empty to disable, relying only on Nextcloud's own
                                           # version history instead)
}


# --------------------------------------------------------------------- pure logic (no I/O)
def map_note_path(folder_titles, title, mappings):
    """folder_titles: raw (unsanitised) folder titles from root to the note's parent, top-down.
    Returns the Nextcloud-relative PDF path, or None if the note is outside every configured mapping.
    Matching is on normalised (whitespace-collapsed, casefolded) titles; the mapping's own
    `nextcloud_path` casing is used for the matched prefix, the note's own titles for the rest."""
    note_norm = [norm(t) for t in folder_titles]
    for m in mappings:
        prefix = [p for p in m["notein_path"].split("/") if p]
        prefix_norm = [norm(p) for p in prefix]
        if note_norm[: len(prefix_norm)] != prefix_norm:
            continue
        rest = folder_titles[len(prefix_norm):]
        parts = [safe(p) for p in rest] + [safe(title) + ".pdf"]
        return "/".join([m["nextcloud_path"].rstrip("/")] + parts)
    return None


class Action:
    SKIP, CREATE, UPDATE, MOVE, DELETE, UNKNOWN_VANISHED, UNRESOLVED = range(7)


def unique_targets(entries):
    """entries: [(nid, meta, nc_path)]. Notes sharing a title in one folder would overwrite each other's PDF.
    The oldest (creationTime, then id) keeps the plain name; the rest get ' (<id[:4]>)' appended.
    Returns ({nid: final_nc_path}, set_of_nids_in_a_collision_group)."""
    groups = {}
    for nid, meta, nc in entries:
        groups.setdefault(nc.casefold(), []).append((meta.get("creationTime") or 0, nid, nc))
    final, shared = {}, set()
    for members in groups.values():
        members.sort()
        for rank, (_, nid, nc) in enumerate(members):
            final[nid] = nc if rank == 0 else "%s (%s).pdf" % (nc[:-4] if nc.lower().endswith(".pdf") else nc, nid[:4])
            if len(members) > 1:
                shared.add(nid)
    return final, shared


def plan_cycle(metas, folders, state, mappings):
    """metas: {local_path: note_meta}. Returns a list of (note_id, action, info) tuples.
    Pure function: no filesystem/network access beyond what's already in `metas`/`folders`."""
    plan = []
    seen_ids = set()
    live = []  # in-scope, not trashed
    newest = {}
    for path, meta in metas.items():  # defensive: several files for one note id -> newest wins
        cur = newest.get(meta["id"])
        if cur is None or (meta.get("lastModificationTime") or 0) >= (cur[1].get("lastModificationTime") or 0):
            newest[meta["id"]] = (path, meta)
    metas = {path: meta for path, meta in newest.values()}
    for path, meta in metas.items():
        nid = meta["id"]
        seen_ids.add(nid)
        title = meta.get("title") or nid
        mapped = map_note_path(raw_folder_path(folders, meta.get("parentId")), title, mappings)
        prev = state.get(nid)
        if mapped is None or meta.get("inTrashBin"):
            if prev:
                plan.append((nid, Action.DELETE, {"nc_path": prev["nc_path"], "reason": "trashed" if meta.get("inTrashBin") else "out of scope"}))
            continue
        pid = meta.get("parentId")
        if pid and pid not in folders:
            # parent folder record missing/unreadable: its real path is unknown, so don't guess (root) - skip
            plan.append((nid, Action.UNRESOLVED, {"title": title, "parent": pid}))
            continue
        live.append((path, nid, meta, mapped))
    final, shared = unique_targets([(nid, meta, m) for _, nid, meta, m in live])

    for path, nid, meta, _ in live:
        mapped, prev = final[nid], state.get(nid)
        if prev is None:
            plan.append((nid, Action.CREATE, {"path": path, "meta": meta, "nc_path": mapped, "dedup": nid in shared}))
        elif prev.get("mod") != meta.get("lastModificationTime") or (nid in shared and not prev.get("dedup")):
            # (a note in a name-collision group is re-converted once: the file at its old path may be another note's)
            old = prev["nc_path"] if prev.get("nc_path") == mapped or nid not in shared else mapped
            plan.append((nid, Action.UPDATE, {"path": path, "meta": meta, "nc_path": mapped, "old_nc_path": old, "dedup": nid in shared}))
        elif prev.get("nc_path") != mapped:
            plan.append((nid, Action.MOVE, {"old_nc_path": prev["nc_path"], "nc_path": mapped}))
        else:
            plan.append((nid, Action.SKIP, {}))

    # notes that were synced before but are entirely absent this cycle: don't touch Nextcloud (could be
    # a transient listing problem), just flag it so the caller can log a warning.
    for nid, prev in state.items():
        if nid not in seen_ids:
            plan.append((nid, Action.UNKNOWN_VANISHED, {"nc_path": prev["nc_path"]}))
    return plan


# --------------------------------------------------------------------- I/O
def load_config(path=None):
    path = path or DEFAULT_CONFIG_PATH
    cfg = dict(DEFAULT_CONFIG)
    if os.path.exists(path):
        cfg.update(json.load(open(path)))
    for k in ("cache_dir", "state_file"):
        cfg[k] = os.path.expanduser(cfg[k])
    return cfg


def rclone(*args, check=True, live=False):
    """live=True: let rclone's own progress output go straight to our stdout/stderr (e.g. journalctl -f),
    instead of only surfacing it on failure. Used for the (potentially large, first-run) initial pull."""
    r = subprocess.run(["rclone", *args], capture_output=not live, text=True)
    if check and r.returncode != 0:
        detail = r.stderr.strip()[-500:] if r.stderr else "(see output above)"
        raise RuntimeError("rclone %s failed: %s" % (" ".join(args[:2]), detail))
    return r


def apply_drive_properties(metas, folders, props):
    """Notein keeps title / parent folder / trash flag current in the Drive file's *properties* (and on its own
    server); the copy inside the zip (note_meta.json) is only rewritten when the note content is. So a note that
    was trashed, renamed or moved in the app, without being edited, still looks live and in its old place inside
    the zip. props: {note_or_folder_id: drive_properties_dict}. Overlays title, parentId and inTrashBin."""
    def fix(rec, pr):
        if "title" in pr:
            rec["title"] = pr["title"]
        if "parentId" in pr:
            rec["parentId"] = None if pr["parentId"] in ("null", "", None) else pr["parentId"]
        if "inTrashBin" in pr:
            rec["inTrashBin"] = str(pr["inTrashBin"]).lower() == "true"
    for rec in list(metas.values()) + list(folders.values()):
        pr = props.get(rec.get("id"))
        if pr:
            fix(rec, pr)


def fetch_drive_properties(config):
    """One recursive listing of the Drive folder with per-file properties -> {noteId/folderId: properties}."""
    r = rclone("lsjson", "-R", "--files-only", "--metadata", f"{config['drive_remote']}:{config['drive_root']}")
    props = {}
    for f in json.loads(r.stdout):
        md = f.get("Metadata") or {}
        key = md.get("noteId") or md.get("folderId")
        if key:
            props[key] = md
    return props


def save_state(path, state):
    """Atomic write (temp file + rename): a crash or a concurrent reader never sees a truncated state file."""
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f, indent=1)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def run_cycle(config, dry_run=False, progress=None):
    """progress(done, total, text): optional callback, called before each action (used for live notifications)."""
    os.makedirs(config["cache_dir"], exist_ok=True)
    os.makedirs(os.path.dirname(config["state_file"]), exist_ok=True)
    state = json.load(open(config["state_file"])) if os.path.exists(config["state_file"]) else {}
    backup_dir = config.get("backup_dir")
    backup_args = []
    if backup_dir and not dry_run:
        run_stamp = time.strftime("%Y%m%d-%H%M%S")
        backup_args = ["--backup-dir", f"{config['nextcloud_remote']}:{backup_dir}/{run_stamp}"]

    # 1. mirror Drive into the local cache. `sync` (not `copy`): when the app renames a note it renames the Drive
    #    file (`<title>_<id>`); with `copy` the old-named file would stay in the cache and be converted as a phantom
    #    second note. rclone does the name matching itself (incl. control-character encoding). Only the local cache
    #    is modified; Drive is only read. --max-delete is a safety net against a bad/empty listing.
    rclone("sync", f"{config['drive_remote']}:{config['drive_root']}", config["cache_dir"], "--update",
           "--exclude", ".sync_tmp_*", "--max-delete", "50",
           "--stats-one-line", "--stats=30s", "-v", live=True)

    # 2. resolve every note's folder path and build the sync plan
    metas, folders = scan_dir(config["cache_dir"])
    apply_drive_properties(metas, folders, fetch_drive_properties(config))
    plan = plan_cycle(metas, folders, state, config["mappings"])

    todo = sum(1 for _, a, _ in plan if a not in (Action.SKIP, Action.UNKNOWN_VANISHED, Action.UNRESOLVED))
    done = 0
    report = {"created": 0, "updated": 0, "moved": 0, "deleted": 0, "skipped": 0, "failed": 0, "vanished": []}
    for nid, action, info in plan:
        if action not in (Action.SKIP, Action.UNKNOWN_VANISHED, Action.UNRESOLVED):
            done += 1
        prog = f"[{done}/{todo}] "
        if progress and action not in (Action.SKIP, Action.UNKNOWN_VANISHED):
            progress(done, todo, (info.get("meta") or {}).get("title") or info.get("nc_path", "").rsplit("/", 1)[-1])
        try:
            if action == Action.SKIP:
                report["skipped"] += 1
            elif action == Action.UNKNOWN_VANISHED:
                report["vanished"].append(nid)
            elif action == Action.UNRESOLVED:
                report.setdefault("unresolved", []).append(nid)
                print(f"warning: skipped '{info['title']}': parent folder {info['parent']} not found in Drive listing", flush=True)
            elif action == Action.DELETE:
                if not dry_run:
                    rclone("deletefile", f"{config['nextcloud_remote']}:{info['nc_path']}", check=False)
                state.pop(nid, None)
                report["deleted"] += 1
                print(f"{prog}delete  {info['nc_path']}  ({info['reason']})", flush=True)
            elif action == Action.MOVE:
                if not dry_run:
                    rclone("moveto", f"{config['nextcloud_remote']}:{info['old_nc_path']}", f"{config['nextcloud_remote']}:{info['nc_path']}")
                state[nid]["nc_path"] = info["nc_path"]
                report["moved"] += 1
                print(f"{prog}move    {info['old_nc_path']} -> {info['nc_path']}", flush=True)
            else:  # CREATE / UPDATE
                meta, nc_path = info["meta"], info["nc_path"]
                title = meta.get("title") or nid
                if not dry_run:
                    tmp_pdf = os.path.join(config["cache_dir"], ".sync_tmp_%s.pdf" % nid)
                    converter.warnings.clear()
                    pages = converter.convert(info["path"], tmp_pdf, embed_source=config["embed_source"])
                    # --backup-dir is relative to copyto's destination *parent*, so put the file's own folder in the
                    # backup path to keep the structure (and avoid same-name collisions across folders)
                    bargs = ([backup_args[0], f"{backup_args[1]}/{os.path.dirname(nc_path)}"] if backup_args else [])
                    rclone("copyto", tmp_pdf, f"{config['nextcloud_remote']}:{nc_path}", *bargs)
                    if action == Action.UPDATE and info["old_nc_path"] != nc_path:
                        rclone("deletefile", f"{config['nextcloud_remote']}:{info['old_nc_path']}", check=False)
                    os.remove(tmp_pdf)
                else:
                    pages = "?"
                state[nid] = {"mod": meta.get("lastModificationTime"), "nc_path": nc_path, "title": title}
                if info.get("dedup"):
                    state[nid]["dedup"] = True
                report["created" if action == Action.CREATE else "updated"] += 1
                print(f"{prog}{'create' if action == Action.CREATE else 'update'}  {title[:60]:<60} {pages} pages -> {nc_path}", flush=True)
        except Exception:
            report["failed"] += 1
            print(f"FAIL  note {nid}\n{traceback.format_exc(limit=2)}", flush=True)
        if not dry_run:
            save_state(config["state_file"], state)

    if report["vanished"]:
        print(f"warning: {len(report['vanished'])} previously-synced note(s) missing from this cycle's Drive "
              f"listing (left untouched on Nextcloud): {report['vanished']}", flush=True)
    return report


def notify(title, body, replace_id=None):
    """Desktop notification via notify-send; returns its id so a later call can update it in place
    (replace_id). Silently skipped if unavailable (e.g. headless)."""
    cmd = ["notify-send", "-a", "notein2pdf", "-p"]
    if replace_id:
        cmd += ["-r", str(replace_id)]
    try:
        r = subprocess.run(cmd + [title, body], capture_output=True, text=True, timeout=5, check=False)
        return int(r.stdout.strip()) if r.stdout.strip().isdigit() else replace_id
    except (OSError, subprocess.SubprocessError):
        return replace_id


def main(argv=None):
    argv = sys.argv[1:] if argv is None else list(argv)
    dry_run = "--dry-run" in argv
    once = "--once" in argv or dry_run
    cfg_path = None
    if "--config" in argv:
        cfg_path = argv[argv.index("--config") + 1]
    if "--init" in argv:
        os.makedirs(os.path.dirname(cfg_path or DEFAULT_CONFIG_PATH), exist_ok=True)
        json.dump(DEFAULT_CONFIG, open(cfg_path or DEFAULT_CONFIG_PATH, "w"), indent=2)
        print("wrote", cfg_path or DEFAULT_CONFIG_PATH)
        return 0
    config = load_config(cfg_path)
    while True:
        state_n = {"id": None, "t": 0.0}
        every = config.get("notify_progress_seconds", 15)

        def progress(done, total, text):
            # One notification, updated in place. GNOME re-shows the banner on every update, so keep updates sparse:
            # the first one right away, then at most every `every` seconds (0 = only the final summary).
            if dry_run or not every:
                return
            now = time.time()
            if state_n["id"] is not None and now - state_n["t"] < every:
                return
            state_n["t"] = now
            state_n["id"] = notify("notein2pdf syncing", "%d of %d: %s" % (done, total, text), state_n["id"])
        report = run_cycle(config, dry_run=dry_run, progress=progress)
        print("cycle: %d created, %d updated, %d moved, %d deleted, %d unchanged, %d failed" %
              (report["created"], report["updated"], report["moved"], report["deleted"], report["skipped"], report["failed"]))
        changed = report["created"] + report["updated"] + report["moved"] + report["deleted"]
        if (changed or report["failed"]) and not dry_run:
            notify("notein2pdf sync done", "%d created, %d updated, %d moved, %d deleted, %d failed" % (
                report["created"], report["updated"], report["moved"], report["deleted"], report["failed"]),
                state_n["id"])
        if once:
            return 1 if report["failed"] else 0
        time.sleep(config["poll_seconds"])


def cli():
    sys.exit(main(sys.argv[1:]))


if __name__ == "__main__":
    cli()

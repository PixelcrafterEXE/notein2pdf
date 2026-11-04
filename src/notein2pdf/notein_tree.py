"""Shared helpers for reading Notein's note/folder zips and resolving folder paths.

Used by both `batch.py` (convert everything in a directory) and `sync_service.py` (the
one-way Drive -> Nextcloud sync). Pure, filesystem-reading functions only - no network.
"""
import json
import os
import re
import zipfile


def safe(name):
    """Sanitise one path segment the same way the app's own file names are built."""
    return re.sub(r'[\\/:*?"<>|\x00-\x1f]+', "_", name).strip(" .") or "untitled"


def norm(name):
    """Loose comparison key for a folder title: collapse/trim whitespace, case-insensitive.
    Notein folder titles sometimes carry stray whitespace (e.g. 'Archive ') that users don't see
    and don't expect to matter for matching a configured path prefix."""
    return re.sub(r"\s+", " ", name).strip().lower()


def read_meta(path):
    """note_meta.json of a note export, or None if `path` isn't one."""
    try:
        with zipfile.ZipFile(path) as z:
            return json.loads(z.read("note_meta.json"))
    except Exception:
        return None


def read_folder(path):
    """Folder records are zips holding one JSON entry (id, title, parentId). That entry is normally named
    'folder_<title>', but a '/' in the title splits the zip entry name (e.g. 'WiSe 25/26' -> '26 (Rep)'), so
    identify it by content instead of by name: the JSON entry that isn't folder_labels.json / folder_tn_*."""
    try:
        with zipfile.ZipFile(path) as z:
            for n in z.namelist():
                if n == "folder_labels.json" or n.startswith("folder_tn_") or n.endswith("/"):
                    continue
                try:
                    d = json.loads(z.read(n))
                except ValueError:
                    continue
                if isinstance(d, dict) and d.get("id") and "title" in d and "noteId" not in d:
                    return d
    except Exception:
        return None
    return None


def scan_dir(src):
    """Walk `src` once: returns (metas: {path: note_meta}, folders: {folderId: folder_record})."""
    metas, folders = {}, {}
    for root, _, files in os.walk(src):
        for fn in files:
            path = os.path.join(root, fn)
            m = read_meta(path)
            if m:
                metas[path] = m
                continue
            f = read_folder(path)
            if f and f.get("id"):
                folders[f["id"]] = f
    return metas, folders


def raw_folder_path(folders, pid):
    """Folder titles from root to `pid` (inclusive), unsanitised, in top-down order.
    [] if pid is None/unknown/root. `pid` here is a note's or folder's parentId."""
    parts, seen = [], set()
    while pid and pid in folders and pid not in seen:
        seen.add(pid)
        parts.append(folders[pid].get("title") or pid[:6])
        pid = folders[pid].get("parentId")
    return list(reversed(parts))


def folder_path(folders, pid):
    """Sanitised, OS-joined folder path (what batch.py writes PDFs under)."""
    parts = [safe(p) for p in raw_folder_path(folders, pid)]
    return os.path.join(*parts) if parts else ""

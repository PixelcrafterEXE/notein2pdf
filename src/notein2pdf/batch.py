"""Convert a folder of Notein exports (.in, or the extensionless zips Notein syncs to Drive) into PDFs.

usage: notein2pdf-batch SRC_DIR OUT_DIR [--force] [--include-trash] [--shard=I/N] [--no-embed]

* Every file that is a zip containing note_meta.json is treated as a note (extension doesn't matter).
* Output: OUT_DIR/<folder path from parentId>/<title>.pdf  (+ '<title> - <recording>.mp3' for embedded audio). Name clashes get a short id suffix.
* State is kept in OUT_DIR/.notein_state.json (note id -> last modification time), so re-runs only touch changed notes.
* Notes in Notein's trash are skipped unless --include-trash is given.
"""
import json, os, sys, zipfile, traceback
from . import converter
from .notein_tree import read_meta, read_folder, safe, folder_path as _folder_path

def zlib_crc(s):
    import zlib
    return zlib.crc32(s.encode())

def main(argv):
    no_embed = "--no-embed" in argv; force = "--force" in argv; trash = "--include-trash" in argv
    shard = next((a.split("=")[1] for a in argv if a.startswith("--shard=")), None)   # e.g. --shard=0/2
    si, sn = (int(x) for x in shard.split("/")) if shard else (0, 1)
    args = [a for a in argv if not a.startswith("--")]
    if len(args) != 2:
        print(__doc__); return 2
    src, out = args
    os.makedirs(out, exist_ok=True)
    sp = os.path.join(out, ".notein_state.json" if sn == 1 else ".notein_state.%d.json" % si)
    state = json.load(open(sp)) if os.path.exists(sp) else {}
    done = skipped = failed = 0
    # pass 1: folder tree
    folders = {}
    for root, _, files in os.walk(src):
        for fn in files:
            f = read_folder(os.path.join(root, fn))
            if f and f.get("id"):
                folders[f["id"]] = f
    def folder_path(pid):
        return _folder_path(folders, pid)
    # pass 2: read every note's meta once; find names that would clash (so all shards agree on file names)
    metas = {}
    for root, _, files in os.walk(src):
        for fn in files:
            path = os.path.join(root, fn)
            m = read_meta(path)
            if m:
                metas[path] = m
    clash = {}
    for path, m in metas.items():
        if m.get("inTrashBin") and not trash:
            continue
        key = (folder_path(m.get("parentId")), safe(m.get("title") or m["id"]).lower())
        clash.setdefault(key, []).append(m["id"])
    for root, _, files in os.walk(src):
        for fn in sorted(files):
            path = os.path.join(root, fn)
            if sn > 1 and (zlib_crc(fn) % sn) != si:
                continue
            meta = metas.get(path)
            if not meta:
                continue
            nid, title = meta["id"], meta.get("title") or meta["id"]
            if meta.get("inTrashBin") and not trash:
                skipped += 1; continue
            mod = meta.get("lastModificationTime", 0)
            sub = folder_path(meta.get("parentId"))
            os.makedirs(os.path.join(out, sub), exist_ok=True)
            dup = len(clash.get((sub, safe(title).lower()), [])) > 1
            target = os.path.join(out, sub, ("%s [%s]" % (safe(title), nid[:6]) if dup else safe(title)) + ".pdf")
            if state.get(nid, {}).get("mod") == mod and os.path.exists(state[nid].get("pdf", "")) and not force:
                skipped += 1; continue
            try:
                converter.warnings.clear()
                n = converter.convert(path, target, embed_source=not no_embed)
                state[nid] = {"mod": mod, "pdf": target, "title": title}
                json.dump(state, open(sp, "w"), indent=1)
                print("ok   %-60s %3d pages" % (title[:60], n), flush=True); done += 1
            except Exception:
                print("FAIL %s\n%s" % (title, traceback.format_exc(limit=1)), flush=True); failed += 1
    print("converted %d, unchanged/skipped %d, failed %d" % (done, skipped, failed))
    return 1 if failed else 0

def cli():
    sys.exit(main(sys.argv[1:]))


if __name__ == "__main__":
    cli()

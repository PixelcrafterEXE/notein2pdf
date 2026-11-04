#!/usr/bin/env python3
"""Extract Notein's Room database schema (DDL only, no user data) from a real .in file.

usage: extract_schema.py note.in [out.json]     (default out: src/notein2pdf/data/schema_v<dbVersion>.json)
The importer (`pdf2notein`) rebuilds new notes from this template so their database matches the app's exactly.
"""
import json
import os
import sqlite3
import sys
import tempfile
import zipfile


def main(argv):
    if not argv:
        print(__doc__)
        return 2
    z = zipfile.ZipFile(argv[0])
    base = next(n for n in z.namelist() if n.startswith("note_database_note_") and n.endswith("_db"))
    d = tempfile.mkdtemp()
    for suf, out in (("", "n.db"), ("-wal", "n.db-wal"), ("-shm", "n.db-shm")):
        if base + suf in z.namelist():
            open(os.path.join(d, out), "wb").write(z.read(base + suf))
    c = sqlite3.connect(os.path.join(d, "n.db"))
    version = c.execute("pragma user_version").fetchone()[0]
    schema = {
        "user_version": version,
        "page_size": c.execute("pragma page_size").fetchone()[0],
        "journal_mode": c.execute("pragma journal_mode").fetchone()[0],
        "android_locale": c.execute("select locale from android_metadata").fetchone()[0],
        "room_master": list(c.execute("select id, identity_hash from room_master_table").fetchone()),
        "ddl": [r[0] for r in c.execute("select sql from sqlite_master where sql is not null order by rowid")],
    }
    out = argv[1] if len(argv) > 1 else os.path.join(os.path.dirname(__file__), "..", "src", "notein2pdf", "data",
                                                     "schema_v%d.json" % version)
    json.dump(schema, open(out, "w"), indent=1)
    print("wrote", os.path.normpath(out), "-", len(schema["ddl"]), "DDL statements, db version", version)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

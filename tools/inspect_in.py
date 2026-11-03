#!/usr/bin/env python3
"""List non-empty tables of the SQLite DB inside a .in file. usage: inspect_in.py file.in"""
import sys, zipfile, tempfile, os, sqlite3, shutil
z = zipfile.ZipFile(sys.argv[1]); d = tempfile.mkdtemp()
names = z.namelist()
base = next(n for n in names if n.startswith("note_database_note_") and n.endswith("_db"))
for suf, out in (("", "n.db"), ("-wal", "n.db-wal"), ("-shm", "n.db-shm")):
    if base + suf in names:
        open(os.path.join(d, out), "wb").write(z.read(base + suf))
c = sqlite3.connect(os.path.join(d, "n.db"))
for (t,) in c.execute("select name from sqlite_master where type='table' and name like '%Entity'"):
    n = c.execute(f"select count(*) from {t}").fetchone()[0]
    if n: print(f"{t:28s}{n}")
print("files:", [n for n in names if not n.startswith("note_database")])
shutil.rmtree(d)

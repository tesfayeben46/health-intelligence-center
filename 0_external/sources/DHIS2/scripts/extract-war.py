#!/usr/bin/env python3
"""Extract the DHIS2 WAR zip, normalising Windows backslash path separators.

The dhisv40.zip archive stores entries with '\' separators, which Linux unzip
turns into literal filenames. Each member name is rewritten to use '/' so the
directory tree is recreated. Not used by the compose stack (which runs the
dhis2/core image).
"""
import os
import sys
import zipfile

src = sys.argv[1] if len(sys.argv) > 1 else "/tmp/dhisv40.zip"
dest = sys.argv[2] if len(sys.argv) > 2 else os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "war")

os.makedirs(dest, exist_ok=True)
count = 0
with zipfile.ZipFile(src) as z:
    for m in z.infolist():
        name = m.filename.replace("\\", "/")
        target = os.path.join(dest, name)
        if name.endswith("/"):
            os.makedirs(target, exist_ok=True)
            continue
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with z.open(m) as s, open(target, "wb") as f:
            f.write(s.read())
        count += 1
        if count % 2000 == 0:
            print(f"  ...{count} files", flush=True)

print(f"DONE: extracted {count} files into {dest}", flush=True)

#!/usr/bin/env python3
"""Decode the in-pod backup stream (===OBJ=== <bucket/key> line, then one base64 line)
into files under <backup>/objects/ and write manifest/SHA256SUMS. Reads stdin.
A separate file because `python3 - <<'EOF'` would take the heredoc as stdin and
silently discard the piped stream."""
import sys, base64, hashlib, os
B = sys.argv[1]; n = 0; cur = None; sums = []
for line in sys.stdin:
    if line.startswith("===OBJ=== "): cur = line[10:].rstrip("\n"); continue
    if cur is None: continue
    data = base64.b64decode(line.strip()); p = os.path.join(B, "objects", cur)
    os.makedirs(os.path.dirname(p), exist_ok=True); open(p, "wb").write(data)
    sums.append(f"{hashlib.sha256(data).hexdigest()}  {cur}"); n += 1; cur = None
os.makedirs(os.path.join(B, "manifest"), exist_ok=True)
open(os.path.join(B, "manifest", "SHA256SUMS"), "w").write("\n".join(sums) + "\n")
print(f"objects written: {n}")

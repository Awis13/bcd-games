#!/usr/bin/env python3
"""Relative includes in winemetal's unix side after upstream's reorganisation.

Upstream moved research/dxmt to dxmt (79e28f0) but kept the dxmt pin
(a5e0cd3), whose winemetal unix sources still count directories from the old
place: "../../../../../build/madeira_cfg.h" (five levels up was the
repository root, now it is the directory above it) and
"../../../../remote-metal/..." (four levels up was research/, now it is the
root; remote-metal stayed in research/). Build 285 stopped in "Build
dxmt-ios": winemetal_unix.c: '../../../../../build/madeira_cfg.h' file not
found.

Rewrites those includes for the new layout. Native only (the i386 farm does
not compile the unix side). Idempotent; fails by name if nothing matches.
Run from the repository root.
"""
import pathlib
import sys

DIR = pathlib.Path("dxmt/src/winemetal/unix")
FIXES = [
    ('"../../../../../build/madeira_cfg.h"', '"../../../../build/madeira_cfg.h"'),
    ('"../../../../remote-metal/', '"../../../../research/remote-metal/'),
]
FILES = ["winemetal_unix.c", "wmt_remote_client.h", "wmt_remote_pack.h"]

if not pathlib.Path("build/madeira_cfg.h").is_file() or not pathlib.Path("research/remote-metal/protocol.h").is_file():
    sys.exit("patch-winemetal-layout: build/madeira_cfg.h or research/remote-metal/ missing (layout changed again?)")

changed = 0
for name in FILES:
    path = DIR / name
    s = path.read_text()
    n = 0
    for old, new in FIXES:
        n += s.count(old)
        s = s.replace(old, new)
    if n:
        path.write_text(s)
        changed += n
        print(f"{path}: {n} include(s) rewritten")
    else:
        print(f"{path}: nothing to rewrite")

left = [str(DIR / f) for f in FILES for old, _ in FIXES if old in (DIR / f).read_text()]
if left:
    sys.exit(f"patch-winemetal-layout: old include still present in {left}")
if not changed and '"../../../../build/madeira_cfg.h"' not in (DIR / "winemetal_unix.c").read_text():
    sys.exit("patch-winemetal-layout: madeira_cfg.h include not found in winemetal_unix.c")

#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""The readback probes never crash a game (tools/patch-winemetal-probe-safe-read.py).

Batman: Arkham Asylum, Brutal Legend and Dishonored (build 574) ended in the [rb-tex] probe:
it read the previous readback buffer's memory after the game had freed it. Checks:
  - on the pinned winemetal_unix.c, after the luminance probe and the readback census, the
    patch applies once (a second run only says so) and leaves no direct read of a kept
    buffer's contents;
  - compiled (macOS): the helper reads a mapped page byte for byte (a working game logs the
    same values) and returns 0 with NaNs, without a fault, on a page that was unmapped;
  - the workflow runs it right after the readback census.
"""
from pathlib import Path
import subprocess, sys, tempfile, shutil, re
root = Path(__file__).resolve().parents[2]
wm_pin = root / "dxmt/src/winemetal/unix/winemetal_unix.c"
patch = root / "tools/patch-winemetal-probe-safe-read.py"
workflow = (root / ".github/workflows/build-ipa.yml").read_text()
ok = True


def check(what, cond):
    global ok
    print(("ok   " if cond else "FAIL ") + what)
    ok &= bool(cond)


if wm_pin.is_file():
    with tempfile.TemporaryDirectory() as t:
        dst = Path(t) / "dxmt/src/winemetal/unix/winemetal_unix.c"
        dst.parent.mkdir(parents=True)
        shutil.copy(wm_pin, dst)
        for p in ("patch-winemetal-lum-probe.py", "patch-winemetal-readback-census.py"):
            r = subprocess.run([sys.executable, str(root / "tools" / p)], cwd=t, capture_output=True, text=True)
            check(f"{p} applies to the pinned file", r.returncode == 0)
        r1 = subprocess.run([sys.executable, str(patch)], cwd=t, capture_output=True, text=True)
        r2 = subprocess.run([sys.executable, str(patch)], cwd=t, capture_output=True, text=True)
        text = dst.read_text()
        check("the patch applies once: " + r1.stdout.strip(), r1.returncode == 0 and "applied" in r1.stdout)
        check("a second run only says so", r2.returncode == 0 and "already" in r2.stdout)
        check("no direct read of a kept buffer's contents is left",
              not re.search(r"memcpy\(\w+, base \+ prev_off", text) and text.count("madeira_probe_read(") == 4)
else:
    print("SKIP: no dxmt sources for the patch (check out the submodule)")

src = patch.read_text()
helper = src.split("HELPER = r'''", 1)[1].split("'''", 1)[0]
harness = r'''
#include <stdio.h>
#include <string.h>
#include <sys/mman.h>
#include <unistd.h>
''' + helper + r'''
int main(void) {
  size_t pg = (size_t)getpagesize();
  unsigned char *p = mmap(NULL, pg, PROT_READ | PROT_WRITE, MAP_ANON | MAP_PRIVATE, -1, 0);
  for (int i = 0; i < 32; i++) p[100 + i] = (unsigned char)(i * 7 + 1);
  float f[8];
  int a = madeira_probe_read(f, p + 100, 32);
  int same = memcmp(f, p + 100, 32) == 0;
  munmap(p, pg);
  float g[4] = {1, 2, 3, 4};
  int b = madeira_probe_read(g, p + 100, 16);
  int nans = g[0] != g[0] && g[3] != g[3];
  printf("%d %d %d %d\n", a, same, b, nans);
  return 0;
}
'''
cc = shutil.which("cc") or shutil.which("clang")
if cc and sys.platform == "darwin":
    with tempfile.TemporaryDirectory() as t:
        c = Path(t) / "h.c"
        c.write_text(harness)
        exe = Path(t) / "h"
        b = subprocess.run([cc, "-std=c11", "-Wall", "-o", str(exe), str(c)], capture_output=True, text=True)
        check("the helper compiles", b.returncode == 0)
        if b.returncode == 0:
            r = subprocess.run([str(exe)], capture_output=True, text=True, timeout=60)
            check("a mapped page: the same bytes (" + r.stdout.strip() + ")", r.stdout.split()[:2] == ["1", "1"])
            check("an unmapped page: 0 and NaNs, no fault", r.returncode == 0 and r.stdout.split()[2:] == ["0", "1"])
        else:
            print(b.stderr[-800:])
else:
    print("SKIP: the helper runs only on macOS")

census = workflow.find("tools/patch-winemetal-readback-census.py")
safe = workflow.find("tools/patch-winemetal-probe-safe-read.py")
check("the workflow runs it right after the readback census", 0 < census < safe and
      workflow[census:safe].count("run: python3 tools/") == 0)
print("PASS: probe safe read" if ok else "FAILED: probe safe read")
sys.exit(0 if ok else 1)

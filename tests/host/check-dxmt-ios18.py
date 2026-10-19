#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""DXMT's winemetal for iOS 18 and later systems (iOS 18 audit, 2026-10-18).

tools/patch-winemetal-cache-spi-weak.py: Metal's private shader-cache path calls
(MTLSetShaderCachePath / MTLGetShaderCachePath, cache.c) were strong imports, so a system without
them would refuse to start the app (metal.md section 1.3). Declared weak; a missing one reports
"no cache path" as DXMT_NO_PRIVATE_API does (its callers then keep the system default); where they
exist the calls are the same.
tools/patch-winemetal-framegen-ios26.py: env.MADEIRA_FRAMEGEN answers off below iOS 26, where
MetalFX frame interpolation does not exist (gameplay.md section 1.4); iOS 26 and later unchanged.

Checks, on the pinned sources (patched in a copy):
  - both patches apply, and a second run only says they are there;
  - cache.c: both declarations weak, the check before the calls, the calls unchanged after it;
  - madeira_fg_enabled: the iOS 26 check only turns frame generation off, before the "ON" line;
  - with an iOS SDK: the patched cache.c imports both calls weak (nm), the unpatched one strong;
  - the workflow runs both patches after the other winemetal patches and before the i386 farm key
    (native only), and runs this check before them.
SKIP (exit 0) without the dxmt submodule.
"""
from pathlib import Path
import shutil, subprocess, sys, tempfile

root = Path(__file__).resolve().parents[2]
dxmt = root / "dxmt"
workflow = (root / ".github/workflows/build-ipa.yml").read_text()
ok = True


def check(what, cond):
    global ok
    print(("ok   " if cond else "FAIL ") + what)
    ok &= bool(cond)


if not (dxmt / "src/winemetal/unix/cache.c").is_file():
    print(f"SKIP: no dxmt sources at {dxmt} (check out the submodule)")
    sys.exit(0)

steps = ("tools/patch-winemetal-cache-spi-weak.py", "tools/patch-winemetal-framegen-ios26.py")
with tempfile.TemporaryDirectory() as t:
    t = Path(t)
    for d in ("src/winemetal", "include", "libs"):
        shutil.copytree(dxmt / d, t / "dxmt" / d)
    pristine = (t / "dxmt/src/winemetal/unix/cache.c").read_text()
    runs = [[subprocess.run([sys.executable, str(root / s)], cwd=t, capture_output=True, text=True) for s in steps]
            for _ in range(2)]
    check("both patches apply to the pinned sources", all(r.returncode == 0 and "already" not in r.stdout for r in runs[0]))
    check("a second run only says they are there", all(r.returncode == 0 and "already patched" in r.stdout for r in runs[1]))
    cache = (t / "dxmt/src/winemetal/unix/cache.c").read_text()
    wm = (t / "dxmt/src/winemetal/unix/winemetal_unix.c").read_text()

    check("both shader-cache calls are declared weak",
          "extern void MTLSetShaderCachePath(NSString* path) __attribute__((weak_import));" in cache
          and "extern NSString* MTLGetShaderCachePath() __attribute__((weak_import));" in cache
          and "extern void MTLSetShaderCachePath(NSString* path);" not in cache)
    fn = cache[cache.index("_WMTSetMetalShaderCachePath(void *obj) {"):]
    fn = fn[:fn.index("\n};\n")]
    guard = "  if (!MTLSetShaderCachePath || !MTLGetShaderCachePath) {"
    check("a missing call reports no cache path (and frees what it took), before any call",
          guard in fn and fn.index(guard) < fn.index("  MTLSetShaderCachePath(resolved_path);")
          and "params->ret_success = 0;\n    [path release];\n    return 0;" in fn[fn.index(guard):])
    check("where they exist the calls are the same as before",
          "  MTLSetShaderCachePath(resolved_path);\n  params->ret_success = [MTLGetShaderCachePath() isEqualToString:resolved_path];\n"
          in fn and fn.count("MTLSetShaderCachePath(resolved_path)") == 1)

    fg = wm[wm.index("static int madeira_fg_enabled(void) {"):]
    fg = fg[:fg.index("\n}\n")]
    check("frame generation: the iOS 26 check sits in madeira_fg_enabled before the ON line, and only turns it off",
          "if (@available(iOS 26.0, macOS 26.0, *)) {\n      } else {\n        g_fg_on = 0;" in fg
          and fg.index("@available(iOS 26.0, macOS 26.0, *)") < fg.index("[framegen] ON (MADEIRA_FRAMEGEN)")
          and fg.count("g_fg_on = ") == 2)

    sdk = shutil.which("xcrun") and subprocess.run(["xcrun", "--sdk", "iphoneos", "--show-sdk-path"],
                                                   capture_output=True, text=True).stdout.strip()
    if not sdk:
        print("SKIP: no iOS SDK for the import check (macOS CI runs it)")
    else:
        def imports(text, name):
            c = t / f"{name}.c"
            o = t / f"{name}.o"
            (t / "dxmt/src/winemetal/unix" / c.name).write_text(text)
            b = subprocess.run(["xcrun", "-sdk", "iphoneos", "clang", "-target", "arm64-apple-ios18.0", "-isysroot", sdk,
                                "-fblocks", "-O2", "-x", "objective-c", "-I", str(t / "dxmt/include"), "-I", str(t / "dxmt/libs"),
                                "-I", str(t / "dxmt/src/winemetal"), "-c", str(t / "dxmt/src/winemetal/unix" / c.name),
                                "-o", str(o)], capture_output=True, text=True)
            if b.returncode:
                print(b.stderr[-2000:])
                return None
            return subprocess.run(["xcrun", "nm", "-m", str(o)], capture_output=True, text=True).stdout
        after, before = imports(cache, "cache_patched"), imports(pristine, "cache_pin")
        check("patched cache.c compiles for iOS 18 and imports both calls weak",
              after is not None and "weak external _MTLSetShaderCachePath" in after
              and "weak external _MTLGetShaderCachePath" in after)
        check("the pinned one imported them strong (what the patch changes)",
              before is not None and "external _MTLSetShaderCachePath" in before
              and "weak external _MTLSetShaderCachePath" not in before)

i = {s: workflow.index(f"run: python3 {s}") for s in steps}
check("CI runs both after the other winemetal patches, before the i386 farm key",
      workflow.index("run: python3 tools/patch-winemetal-heap-untracked.py") < i[steps[0]] < i[steps[1]]
      < workflow.index("- name: i386 farm revision"))
check("CI runs this check before them", workflow.index("run: python3 tests/host/check-dxmt-ios18.py") < i[steps[0]])

print("PASS: winemetal for iOS 18" if ok else "FAILED: winemetal for iOS 18")
sys.exit(0 if ok else 1)

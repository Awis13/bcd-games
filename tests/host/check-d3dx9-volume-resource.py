#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""tools/patch-wine-d3dx9-volume-resource.py: D3DXCreateVolumeTextureFromResourceExA/W (GTA IV, build 574).

Checks: on the pinned Wine source (when the submodule is checked out) the patch applies once, turns the two
stubs into stdcall exports in every d3dx9_NN.spec and adds both functions to d3dx9_36/texture.c, which compiles
(llvm-mingw, when present); the workflow runs it; auto-tune names the stub as fixed in 575.
"""
from pathlib import Path
import shutil, subprocess, sys, tempfile
root = Path(__file__).resolve().parents[2]
patch = root / "tools/patch-wine-d3dx9-volume-resource.py"
ok = True


def check(what, cond):
    global ok
    print(("ok   " if cond else "FAIL ") + what)
    ok &= bool(cond)


wine = root / "wine"
if (wine / "dlls/d3dx9_36/texture.c").is_file():
    with tempfile.TemporaryDirectory() as t:
        w = Path(t) / "wine"
        (w / "dlls").mkdir(parents=True)
        for d in (wine / "dlls").glob("d3dx9_*"):
            shutil.copytree(d, w / "dlls" / d.name)
        r1 = subprocess.run([sys.executable, str(patch), str(w)], capture_output=True, text=True)
        r2 = subprocess.run([sys.executable, str(patch), str(w)], capture_output=True, text=True)
        check("applies once: " + r1.stdout.strip(), r1.returncode == 0 and "applied" in r1.stdout)
        check("a second run only says so", r2.returncode == 0 and "already" in r2.stdout)
        specs = sorted((w / "dlls").glob("d3dx9_*/d3dx9_*.spec"))
        check("no d3dx9 spec keeps the stubs, every one exports both",
              specs and all("@ stub D3DXCreateVolumeTextureFromResourceEx" not in s.read_text() and
                            s.read_text().count("@ stdcall D3DXCreateVolumeTextureFromResourceEx") == 2 for s in specs))
        tex = (w / "dlls/d3dx9_36/texture.c").read_text()
        check("both functions load the RT_RCDATA resource and hand it to the in-memory call",
              tex.count("D3DXCreateVolumeTextureFromFileInMemoryEx(device, buffer, size, width, height, depth") == 2 and
              "FindResourceW(srcmodule, resource, (const WCHAR *)RT_RCDATA)" in tex)
        cc = next(iter(sorted((root / "toolchains").glob("llvm-mingw-*/bin/x86_64-w64-mingw32-clang"))), None)
        inc = wine / "build-arm64ec/include"
        if cc and inc.is_dir():
            b = subprocess.run([str(cc), "-fsyntax-only", "-D__WINESRC__", "-I", str(wine / "include"),
                                "-I", str(w / "dlls/d3dx9_36"), "-I", str(inc), str(w / "dlls/d3dx9_36/texture.c")],
                               capture_output=True, text=True)
            check("texture.c compiles", b.returncode == 0)
        else:
            print("SKIP: no llvm-mingw or configured Wine build to compile with")
else:
    print("SKIP: no wine sources (check out the submodule)")
wf = (root / ".github/workflows/build-ipa.yml").read_text()
check("the workflow runs it on the Wine source", "python3 tools/patch-wine-d3dx9-volume-resource.py wine" in wf)
check("the i386 farm's cache key covers the Wine source patches (else the cached farm never gets them)",
      "'tools/patch-wine-*.py'" in wf[wf.index("i386-farm-"):wf.index("i386-farm-") + 400])
rules = (root / "build/tools/autotune_rules.py").read_text()
check("auto-tune names the stub as fixed in 576", '"id": "d3dx9-volume-resource-stub"' in rules and '"fixedIn": 576' in
      rules[rules.index('"id": "d3dx9-volume-resource-stub"'):][:400])
print("PASS: d3dx9 volume resource" if ok else "FAILED: d3dx9 volume resource")
sys.exit(0 if ok else 1)

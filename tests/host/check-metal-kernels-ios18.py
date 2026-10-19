#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""The D3D12 runtime's helper kernels on iOS 18 (gameplay.md section 1.2 of the iOS 18 audit).

mad_kernels.metal was compiled with no target, so its metallib took the SDK's own (iOS 26.2: AIR
2.8), and iOS 18's loader reads AIR 2.7 at most: "helper kernels: NOT available" there (indirect
DXIL tessellation draws skipped, clear-rects and fi-nan-clean silently off). Both build scripts now
build a second copy at --target=air64-apple-ios18.0 with the same flags, and madeira_d3d12.c loads
it only when the first library is refused, so iOS 26 and later load exactly what they did.

Checks:
  - tools/build-madeira-d3d12-dll.sh and build/madeira-d3d12/build-pe.sh: the first compile is
    unchanged (no target) and remembers its flags; the iOS 18 copy is built after it, inside its
    success branch, with those flags, into mad_kernels_ios18_metallib.h and MAD_HAVE_KERNELS_IOS18;
  - madeira_d3d12.c: the copy is included under MAD_HAVE_KERNELS_IOS18 and tried only when the
    first library is refused, before the kernels are looked up, with a log line either way;
  - with Xcode's metal compiler: both copies of the real source compile; the iOS 18 copy's header
    says iOS 18.0 and every function is AIR <= 2.7 / MSL <= 3.2, with the same functions as the
    first copy;
  - CI runs this check before the DLL build.
"""
from pathlib import Path
import shutil, struct, subprocess, sys, tempfile

root = Path(__file__).resolve().parents[2]
src = (root / "madeira-d3d12/src/pe/madeira_d3d12.c").read_text()
metal = root / "madeira-d3d12/src/pe/mad_kernels.metal"
workflow = (root / ".github/workflows/build-ipa.yml").read_text()
ok = True


def check(what, cond):
    global ok
    print(("ok   " if cond else "FAIL ") + what)
    ok &= bool(cond)


FIRST = 'xcrun -sdk iphoneos metal -std=metal3.0 "$@" -o "$OUT/mad_kernels.metallib" "$SRC/mad_kernels.metal" 2> "$OUT/mad_kernels.err" &&\n        MAD_KERNELS_FLAGS="$*"'
COPY = 'xcrun -sdk iphoneos metal -std=metal3.0 --target=air64-apple-ios18.0 $MAD_KERNELS_FLAGS \\\n           -o "$OUT/mad_kernels_ios18.metallib" "$SRC/mad_kernels.metal"'
for script, define in (("tools/build-madeira-d3d12-dll.sh", 'KDEF="$KDEF -DMAD_HAVE_KERNELS_IOS18"'),
                       ("build/madeira-d3d12/build-pe.sh", "EXTRA+=(-DMAD_HAVE_KERNELS_IOS18)")):
    s = (root / script).read_text()
    check(f"{script}: the first compile is the same command, no target, and remembers its flags",
          FIRST in s and s.count("--target=air64-apple-ios18.0") == 1)
    ok_branch = s[s.index("(cd \"$OUT\" && xxd -i mad_kernels.metallib > mad_kernels_metallib.h)"):]
    ok_branch = ok_branch[:ok_branch.index("\nelse\n")]
    check(f"{script}: the iOS 18 copy is built in the first's success branch, with its flags, into its own header",
          COPY in ok_branch and "xxd -i mad_kernels_ios18.metallib > mad_kernels_ios18_metallib.h" in ok_branch
          and define in ok_branch)

check("madeira_d3d12.c includes the copy only under MAD_HAVE_KERNELS_IOS18",
      '#ifdef MAD_HAVE_KERNELS_IOS18\n#include "mad_kernels_ios18_metallib.h"\n#endif' in src)
ready = src[src.index("static int mad_kernels_ready(struct mad_device *d) {"):]
ready = ready[:ready.index("\n}\n")]
first = ready.index("d->k_lib = MTLDevice_newLibrary(d->mtl_device, dd, &err);")
fallback = ready.index("if (!d->k_lib) {   /* madeira-bcd: refused (iOS 18): the iOS 18 copy */")
check("the copy is tried only when the first library was refused, before the kernels are looked up",
      first < fallback < ready.index('MTLLibrary_newFunction(d->k_lib, "mad_tess_indirect_args")')
      and "d->k_lib = MTLDevice_newLibrary(d->mtl_device, dd18, &err);" in ready[fallback:])
check("both outcomes are logged",
      'd3d12_log("[madeira-d3d12] helper kernels: the iOS 18 copy %s\\n", d->k_lib ? "loaded" : "was refused too");' in ready)
check("CI runs this check before the DLL build",
      workflow.index("run: python3 tests/host/check-metal-kernels-ios18.py") < workflow.index("run: bash tools/build-madeira-d3d12-dll.sh"))


def functions(data):
    """(name, (air_major, air_minor, msl_major, msl_minor)) of each function of a metallib."""
    fl = struct.unpack_from("<Q", data, 24)[0]
    count = struct.unpack_from("<I", data, fl)[0]
    p, out = fl + 4, []
    for _ in range(count):
        size = struct.unpack_from("<I", data, p)[0]
        q, end, name, vers = p + 4, p + size, None, None
        while q < end and data[q:q + 4] != b"ENDT":
            ln = struct.unpack_from("<H", data, q + 4)[0]
            if data[q:q + 4] == b"NAME":
                name = data[q + 6:q + 6 + ln].rstrip(b"\0").decode()
            elif data[q:q + 4] == b"VERS":
                vers = struct.unpack_from("<4H", data, q + 6)
            q += 6 + ln
        out.append((name, vers))
        p = end
    return out


have_metal = shutil.which("xcrun") and subprocess.run(["xcrun", "-sdk", "iphoneos", "--find", "metal"],
                                                      capture_output=True).returncode == 0
if not have_metal:
    print("SKIP: no Metal compiler for the AIR check (macOS CI runs it)")
else:
    with tempfile.TemporaryDirectory() as t:
        libs = {}
        for name, extra in (("first", []), ("ios18", ["--target=air64-apple-ios18.0"])):
            out = Path(t) / f"{name}.metallib"
            r = subprocess.run(["xcrun", "-sdk", "iphoneos", "metal", "-std=metal3.0"] + extra + ["-o", str(out), str(metal)],
                               capture_output=True, text=True)
            check(f"the {name} copy compiles", r.returncode == 0)
            if r.returncode:
                print(r.stderr[-2000:])
            else:
                libs[name] = out.read_bytes()
        if len(libs) == 2:
            f18, f = functions(libs["ios18"]), functions(libs["first"])
            print("     iOS 18 copy: " + ", ".join(f"{n} AIR {v[0]}.{v[1]} MSL {v[2]}.{v[3]}" for n, v in f18))
            print("     first copy:  " + ", ".join(f"{n} AIR {v[0]}.{v[1]}" for n, v in f))
            check("the iOS 18 copy: every function AIR <= 2.7 and MSL <= 3.2 (what iOS 18's loader reads)",
                  f18 and all(v and (v[0], v[1]) <= (2, 7) and (v[2], v[3]) <= (3, 2) for _, v in f18))
            check("the same functions as the first copy", sorted(n for n, _ in f18) == sorted(n for n, _ in f))
            hdr = subprocess.run(["xcrun", "-sdk", "iphoneos", "metal-readobj", "--file-header", str(Path(t) / "ios18.metallib")],
                                 capture_output=True, text=True).stdout
            check("its header names iOS 18.0", "PlatformMajor: 18" in hdr and "PlatformMinor: 0" in hdr
                  and "METALLIB_PLATFORM_IOS" in hdr)

print("PASS: D3D12 helper kernels for iOS 18" if ok else "FAILED: D3D12 helper kernels for iOS 18")
sys.exit(0 if ok else 1)

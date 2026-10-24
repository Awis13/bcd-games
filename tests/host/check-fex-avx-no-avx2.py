#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""env.MADEIRA_FEX_AVX = 2 (2026-10-23, Marvel's Spider-Man 2's Bink video): AVX reported and emulated, AVX2 not.
Read from the sources (FEX at the pinned commit in a full tree; the app's bridge):
  1. FEX CPUID: the AVX2 bit of leaf 7 (and VAES, VPCLMULQDQ) follow SupportsAVX2(), which is SupportsAVX() unless HideAVX2;
     HideAVX2 is set only for the exact value "2"; FMA, F16C, BMI1/2 and AVX itself still follow SupportsAVX().
  2. FEX's Windows host features: AVX is turned on for "1" as before and for exactly "2".
  3. The bridge links xtajit64-avx.dll for "1" as before and for exactly "2".
A tree without the FEX submodule checks only the bridge (and says so)."""
import re, sys
from pathlib import Path

root = Path(__file__).resolve().parents[2]
fails = []
def check(what, ok):
    print(("ok   " if ok else "FAIL ") + what)
    if not ok: fails.append(what)

cpuid_cpp = root / "FEX/FEXCore/Source/Interface/Core/CPUID.cpp"
cpuid_h = root / "FEX/FEXCore/Source/Interface/Core/CPUID.h"
feat = root / "FEX/Source/Windows/Common/CPUFeatures.cpp"
if cpuid_cpp.exists() and cpuid_h.exists() and feat.exists():
    h, c, f = cpuid_h.read_text(), cpuid_cpp.read_text(), feat.read_text()
    check("CPUID.h: SupportsAVX2() is SupportsAVX() unless HideAVX2",
          "bool HideAVX2 {};" in h and re.search(r"SupportsAVX2\(\) const \{\s*return \(SupportsAVX\(\) && !HideAVX2\) \? 1 : 0;", h) is not None)
    check("CPUID.cpp: HideAVX2 only for the exact value \"2\" (and only when AVX is on)",
          "getenv(\"MADEIRA_FEX_AVX\"); Avx && Avx[0] == '2' && Avx[1] == '\\0' && SupportsAVX()) {" in c and "HideAVX2 = true;" in c)
    check("CPUID.cpp: leaf 7 AVX2 bit from SupportsAVX2()", "(SupportsAVX2() << 5)" in c and "(SupportsAVX() << 5)" not in c)
    check("CPUID.cpp: VPCLMULQDQ and VAES hidden with AVX2",
          "SupportsPMULL_128Bit && SupportsAVX2()" in c and "(CTX->HostFeatures.SupportsAES256 && !HideAVX2) << 9" in c)
    check("CPUID.cpp: AVX, FMA3, F16C, BMI1, BMI2 still follow SupportsAVX()",
          all(s in c for s in ("(SupportsAVX() << 28)", "(SupportsAVX() << 12)", "(SupportsAVX() << 29)", "(SupportsAVX() << 3)", "(SupportsAVX() << 8)")))
    check("CPUFeatures.cpp: AVX on for '1' as before, and for exactly \"2\"",
          "Avx && (Avx[0] == '1' || (Avx[0] == '2' && Avx[1] == '\\0')) && HostType" in f)
else:
    print("skip FEX checks: no FEX submodule in this tree (the full tree runs them)")

bridge = (root / "app/Madeira/WineProcessBridge.m").read_text()
check("bridge: xtajit64-avx.dll for '1' as before, and for exactly \"2\"",
      "if (use_arm64ec && avxEnv && (avxEnv[0] == '1' || (avxEnv[0] == '2' && avxEnv[1] == '\\0'))) {" in bridge)
if fails:
    print("FAILED: MADEIRA_FEX_AVX=2"); sys.exit(1)
print("PASS: MADEIRA_FEX_AVX=2 (AVX without AVX2)")

#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""What only iOS 26 can run is hidden or refused below it (gameplay.md sections 1.3 and 1.4).

- Frame generation (MetalFX frame interpolation, iOS 26): the place that offers it (BCD's game page;
  wave 3 deleted the earlier ones) sits inside `if #available(iOS 26.0, *)`, and the native switch
  answers off below iOS 26 (tools/patch-winemetal-framegen-ios26.py, checked with DXMT in
  check-dxmt-ios18.py).
- ARM64-native Direct3D 11 (aarch64-windows/d3d11.dll, whose command library is MSL 4.0 / AIR
  2.8, refused below iOS 26 and then a DXMT abort): its only start was the developer "arm64 DX11
  cube" button, deleted in wave 3 together with the developer interface.
On iOS 26 and later every one of them is what it was. CI runs this check.
"""
from pathlib import Path
import re, sys

root = Path(__file__).resolve().parents[2]
app = root / "app/Madeira"
ok = True


def check(what, cond):
    global ok
    print(("ok   " if cond else "FAIL ") + what)
    ok &= bool(cond)


def guarded(text, needle, lines_before=2):
    """`needle` is on a line inside an `if #available(iOS 26.0, *) {` opened just above it."""
    lines = text.split("\n")
    for i, l in enumerate(lines):
        if needle in l:
            above = lines[max(0, i - lines_before):i]
            return any("if #available(iOS 26.0, *) {" in a for a in above)
    return False


live = (app / "BCDGamePageLive.swift").read_text()
workflow = (root / ".github/workflows/build-ipa.yml").read_text()

# Wave 3 (2026-10-19): the other places that offered frame generation (the earlier home screen's game settings,
# the classic Game details' BCD section and the library's Display section) are deleted; BCD's game page is the one
# place left, and it only offers the switch from iOS 26.
check("BCD's game page: the frame generation switch only from iOS 26",
      guarded(live, 'settings.append(Self.toggle("framegen", "Frame generation (experimental)"', 1)
      and guarded(live, 'setLine(game, "env.MADEIRA_FRAMEGEN"', 2))
check("every place that writes env.MADEIRA_FRAMEGEN from a control is that one",
      live.count('Self.toggle("framegen"') == 1
      and not any("MADEIRA_FRAMEGEN" in p.read_text() for p in app.glob("*.swift")
                  if p.name not in ("BCDGamePageLive.swift", "ConfigCatalog.generated.swift", "GameRecommendationsApply.swift",
                                    "GameRecommendations.swift", "GameRecommendationsList.swift", "GameProfiles.swift",
                                    "LibraryBCD.swift", "Library.swift")))
# The developer interface's "arm64 DX11 cube" button (the only start of ARM64-native Direct3D 11 below the
# games' own programs) and its iOS 26 refusal are deleted with that interface.
check("no 'arm64 DX11 cube' button or test start is left", "arm64 DX11 cube" not in (app / "ContentView.swift").read_text()
      and "runTriangleTest" not in (app / "ContentView.swift").read_text())
patch = (root / "tools/patch-winemetal-framegen-ios26.py").read_text()
check("the native switch answers off below iOS 26 (the patch is in the workflow, after DXMT's framegen)",
      "if (@available(iOS 26.0, macOS 26.0, *)) {" in patch and "g_fg_on = 0;" in patch
      and "run: python3 tools/patch-winemetal-framegen-ios26.py" in workflow
      and workflow.index("run: python3 tools/patch-dxmt-framegen.py") < workflow.index("run: python3 tools/patch-winemetal-framegen-ios26.py"))
check("CI runs this check", "run: python3 tests/host/check-frame-gen-ios26.py" in workflow)

print("PASS: iOS 26-only features below iOS 26" if ok else "FAILED: iOS 26-only features below iOS 26")
sys.exit(0 if ok else 1)

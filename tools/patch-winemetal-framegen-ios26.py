#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""Frame generation stays off below iOS 26.

MetalFX frame interpolation (MTLFXFrameInterpolator) is iOS 26's. Below it,
env.MADEIRA_FRAMEGEN = 1 still turned frame generation "on" in
madeira_fg_enabled(): every layer got framebufferOnly = NO and the three motion
kernels were compiled before the iOS 26 check in madeira_fg_setup said "needs
iOS 26 -- off". Nothing was interpolated; the layer and the kernels only cost
time (docs/research/ios18-2026-10-18/gameplay.md section 1.4).

madeira_fg_enabled() now answers 0 below iOS 26, once, with a [framegen] line;
the app hides the setting there too. DXMT is built at iOS 18, so the check is a
real run-time one. On iOS 26 and later nothing changes. Native only: not in the
i386 farm's cache key. Idempotent; fails by name if the anchor moves. Run from
the repository root.
"""
import pathlib
import sys

PATH = pathlib.Path("dxmt/src/winemetal/unix/winemetal_unix.c")
MARKER = "madeira-bcd: frame generation below iOS 26"

OLD = """    g_fg_on = (e && *e && *e != '0') ? 1 : 0;
    if (g_fg_on)
      dprintf(STDERR_FILENO, "[framegen] ON (MADEIRA_FRAMEGEN): MetalFX frame interpolation between game frames\\n");
"""
NEW = """    g_fg_on = (e && *e && *e != '0') ? 1 : 0;
    if (g_fg_on) {   /* madeira-bcd: frame generation below iOS 26 (tools/patch-winemetal-framegen-ios26.py) */
      if (@available(iOS 26.0, macOS 26.0, *)) {
      } else {
        g_fg_on = 0;
        dprintf(STDERR_FILENO, "[framegen] MADEIRA_FRAMEGEN is set, but MetalFX frame interpolation needs iOS 26 -- off; "
                               "the layer and the presents stay as without it\\n");
      }
    }
    if (g_fg_on)
      dprintf(STDERR_FILENO, "[framegen] ON (MADEIRA_FRAMEGEN): MetalFX frame interpolation between game frames\\n");
"""


def main():
    src = PATH.read_text()
    if MARKER in src:
        print(f"{PATH}: frame generation below iOS 26 already patched")
        return 0
    if src.count(OLD) != 1 or "static int madeira_fg_enabled(void) {" not in src:
        print(f"::error::{PATH}: madeira_fg_enabled anchor not found once -- dxmt moved, review "
              "tools/patch-winemetal-framegen-ios26.py")
        return 1
    fn = src.index("static int madeira_fg_enabled(void) {")
    if not fn < src.index(OLD) < fn + 400:
        print(f"::error::{PATH}: the anchor is no longer inside madeira_fg_enabled")
        return 1
    PATH.write_text(src.replace(OLD, NEW))
    print(f"{PATH}: frame generation below iOS 26 patched")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""Metal's private shader-cache path calls as weak imports.

dxmt/src/winemetal/unix/cache.c calls MTLSetShaderCachePath and
MTLGetShaderCachePath, which Metal exports (iOS 18.1 and 26 simulators, the
26.2 SDK's .tbd) but no header declares. They were strong imports of the app
binary: a system whose Metal lacks either of them would make dyld refuse to
start the app before any game (docs/research/ios18-2026-10-18/metal.md, section
1.3 and fix 1c; native.md, "Private SPI").

Both are declared weak, and _WMTSetMetalShaderCachePath reports failure when
either is missing, as the DXMT_NO_PRIVATE_API build does. Its callers (dxgi,
native D3D9) then log "Failed to set Metal cache path" and keep the system's
default cache. Where the calls exist (every iOS BCD runs on today) they are
called exactly as before, with the same arguments and the same result. Native
only: not in the i386 farm's cache key. Idempotent; fails by name if an anchor
moves. Run from the repository root.
"""
import pathlib
import sys

PATH = pathlib.Path("dxmt/src/winemetal/unix/cache.c")
MARKER = "madeira-bcd: weak shader-cache SPI"

DECL_OLD = """extern void MTLSetShaderCachePath(NSString* path);
extern NSString* MTLGetShaderCachePath();
"""
DECL_NEW = """/* madeira-bcd: weak shader-cache SPI (tools/patch-winemetal-cache-spi-weak.py).
 * Metal exports these but no header declares them. Weak, a system without them
 * still starts the app, and the cache path is reported as not set; where they
 * exist, the calls below are the same. */
extern void MTLSetShaderCachePath(NSString* path) __attribute__((weak_import));
extern NSString* MTLGetShaderCachePath() __attribute__((weak_import));
"""

CALL_OLD = """  MTLSetShaderCachePath(resolved_path);
  params->ret_success = [MTLGetShaderCachePath() isEqualToString:resolved_path];
"""
CALL_NEW = """  if (!MTLSetShaderCachePath || !MTLGetShaderCachePath) {   /* madeira-bcd: weak SPI absent on this system */
    params->ret_success = 0;
    [path release];
    return 0;
  }
  MTLSetShaderCachePath(resolved_path);
  params->ret_success = [MTLGetShaderCachePath() isEqualToString:resolved_path];
"""


def main():
    src = PATH.read_text()
    if MARKER in src:
        print(f"{PATH}: weak shader-cache SPI already patched")
        return 0
    for name, old in (("the SPI declarations", DECL_OLD), ("the SPI calls", CALL_OLD)):
        if src.count(old) != 1:
            print(f"::error::{PATH}: {name} anchor not found once -- dxmt moved, review "
                  "tools/patch-winemetal-cache-spi-weak.py")
            return 1
    if src.index(DECL_OLD) > src.index(CALL_OLD):
        print(f"::error::{PATH}: the SPI declarations no longer come before their calls")
        return 1
    src = src.replace(DECL_OLD, DECL_NEW).replace(CALL_OLD, CALL_NEW)
    PATH.write_text(src)
    print(f"{PATH}: weak shader-cache SPI patched")
    return 0


if __name__ == "__main__":
    sys.exit(main())

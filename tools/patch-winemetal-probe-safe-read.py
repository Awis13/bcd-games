#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""The readback probes never crash a game (madeira-bcd). Run after
tools/patch-winemetal-lum-probe.py and tools/patch-winemetal-readback-census.py.

Those probes ([lum-readback], [rb-tex], [rb-buf]; on unless MADEIRA_LUM_PROBE=0) log the
value of the previous small readback when the next one comes: they keep the previous
destination MTLBuffer and read its contents then. Keeping the buffer object does not keep
its memory: a staging buffer lies on the game's own memory, and when the game frees that
memory first, the read faults on the host (BUS->AV in MTLBlitCommandEncoder_encodeCommands,
code c0000005, the game ends). Logs of 2026-10-19/20, build 574: Batman: Arkham Asylum
(1Q05G8, RKHMA6), Brutal Legend (82BAXZ), Dishonored (0RA62D, 5B06QT), all 32-bit, all at
the same instruction (LDP s0,s1 at encodeCommands+3260, the [rb-tex] value read), on a
view the game had unmapped.

The read now goes through vm_read_overwrite on the app's own task: a page that is gone
gives an error instead of a fault, the line says "unreadable" and the game goes on. A
readable page gives the same bytes as before, so every log line of a game that worked is
unchanged. Native only. Idempotent; fails by name if an anchor moves. Run from the
repository root.
"""
import pathlib
import sys

PATH = pathlib.Path("dxmt/src/winemetal/unix/winemetal_unix.c")
MARKER = "madeira-bcd: probe safe read"

HELPER = r'''/* madeira-bcd: probe safe read (tools/patch-winemetal-probe-safe-read.py) */
#include <mach/mach.h>
#include <math.h>
static int madeira_probe_read(void *out, const void *src, size_t n) {
  vm_size_t got = 0;
  kern_return_t kr = vm_read_overwrite(mach_task_self(), (vm_address_t)src, (vm_size_t)n,
                                       (vm_address_t)out, &got);
  if (kr == KERN_SUCCESS && got == n) return 1;
  static unsigned told;
  if (told++ < 8)
    fprintf(stderr, "[rb-probe] madeira-bcd readback value unreadable (kr=%d): the game freed that memory; "
                    "not read\n", (int)kr);
  float *f = out;
  for (size_t i = 0; i < n / sizeof(float); i++) f[i] = NAN;
  return 0;
}
'''

EDITS = [
    ("/* madeira-bcd: luminance probe (tools/patch-winemetal-lum-probe.py) */",
     HELPER + "/* madeira-bcd: luminance probe (tools/patch-winemetal-lum-probe.py) */"),
    ("      memcpy(v, base + prev_off, sizeof v);",
     "      madeira_probe_read(v, base + prev_off, sizeof v);"),
    ("        memcpy(f, base + prev_off, 16); memcpy(u, base + prev_off, 16);",
     "        madeira_probe_read(f, base + prev_off, 16); memcpy(u, f, 16);"),
    ("      memcpy(f, base + prev_off, 32);",
     "      madeira_probe_read(f, base + prev_off, 32);"),
]


def main():
    text = PATH.read_text()
    if MARKER in text:
        print("probe safe read: already applied")
        return
    for old, new in EDITS:
        if text.count(old) != 1:
            sys.exit(f"patch-winemetal-probe-safe-read: anchor not found exactly once: {old.strip()[:70]!r}")
        text = text.replace(old, new)
    PATH.write_text(text)
    print("probe safe read: applied (3 reads)")


if __name__ == "__main__":
    main()

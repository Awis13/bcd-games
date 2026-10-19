#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""Metal work waits while the app is not active (env.MADEIRA_PAUSE_IN_BACKGROUND = 1, off by default).

iOS refuses GPU work from an app in the background ("Insufficient Permission (to
submit GPU work from background)"). A game keeps running for some seconds after
Home (about 30 while a Steam download holds a background task), and what it
submitted then never ran: DXMT marks the device as failed ("Device error at
frame N"), the D3D12 runtime logs "command buffer refused while the app was in
the background", and a texture streamed in that window can stay black after the
game comes back (docs/research/ios18-2026-10-18/gameplay.md section 2.2).

winemetal_unix.c calls madeira_app_wait_active (app/Madeira/WineProcessBridge.m)
before it commits a command buffer and before a present. The app sets the flag
that makes it wait only with the switch, between resign-active and
become-active; without the switch the call returns at once and the commit and
the present are what they were. It never waits on the main thread. DXMT, its
native D3D9 and the D3D12 runtime all commit through _MTLCommandBuffer_commit.
Remote Metal (a development mode) is left as it is. Native only: not in the i386
farm's cache key. Idempotent; fails by name if an anchor moves. Run from the
repository root.
"""
import pathlib
import sys

PATH = pathlib.Path("dxmt/src/winemetal/unix/winemetal_unix.c")
MARKER = "madeira-bcd: pause while inactive"

DECL_ANCHOR = "static NTSTATUS\n_MTLCommandBuffer_commit(void *obj) {\n"
DECL = """/* madeira-bcd: pause while inactive (tools/patch-winemetal-pause-inactive.py).
 * env.MADEIRA_PAUSE_IN_BACKGROUND = 1: a commit or a present waits while the app
 * is not active, so iOS never refuses the game's GPU work from the background.
 * Without the switch it returns at once (app/Madeira/WineProcessBridge.m). */
extern int madeira_app_wait_active(const char *where);

"""

COMMIT_OLD = """    wmtr_call(RM_OP_COMMIT, &a, sizeof a, 0, 0, 0);
    return STATUS_SUCCESS;
  }
"""
COMMIT_NEW = COMMIT_OLD + """  madeira_app_wait_active("commit");   /* madeira-bcd: pause while inactive */
"""

PRESENT_OLD = """    wmtr_call(RM_OP_PRESENT_DRAWABLE, &a, sizeof a, 0, 0, 0);
    return STATUS_SUCCESS;
  }
  int mode = g_madeira_vsync_mode;
"""
PRESENT_NEW = """    wmtr_call(RM_OP_PRESENT_DRAWABLE, &a, sizeof a, 0, 0, 0);
    return STATUS_SUCCESS;
  }
  madeira_app_wait_active("present");   /* madeira-bcd: pause while inactive */
  int mode = g_madeira_vsync_mode;
"""


def main():
    src = PATH.read_text()
    if MARKER in src:
        print(f"{PATH}: pause while inactive already patched")
        return 0
    for name, old in (("_MTLCommandBuffer_commit", DECL_ANCHOR), ("commit's remote branch", COMMIT_OLD),
                      ("presentDrawable's remote branch", PRESENT_OLD)):
        if src.count(old) != 1:
            print(f"::error::{PATH}: {name} anchor not found once -- dxmt moved, review "
                  "tools/patch-winemetal-pause-inactive.py")
            return 1
    if src.index(DECL_ANCHOR) > src.index(COMMIT_OLD) or src.index(DECL_ANCHOR) > src.index(PRESENT_OLD):
        print(f"::error::{PATH}: the commit function no longer comes before the present path")
        return 1
    src = src.replace(DECL_ANCHOR, DECL + DECL_ANCHOR)
    src = src.replace(COMMIT_OLD, COMMIT_NEW)
    src = src.replace(PRESENT_OLD, PRESENT_NEW)
    PATH.write_text(src)
    print(f"{PATH}: pause while inactive patched")
    return 0


if __name__ == "__main__":
    sys.exit(main())

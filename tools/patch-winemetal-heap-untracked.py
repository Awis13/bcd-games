#!/usr/bin/env python3
"""Opt-in untracked placement heaps for madeira-d3d12 (madeira.cfg heap-untracked).

madeira-d3d12 makes a Metal placement heap for every D3D12 DEFAULT heap the
game creates (ml1145) and for its own runtime texture heaps (ml1072), through
winemetal's _MTLDevice_newPlacementHeap, which hard-codes
MTLHazardTrackingModeTracked. Horizon Forbidden West keeps its ~2 GB texture
streaming pool in such heaps (32 MB, texture-only). On a tracked heap Metal
tracks hazards for the heap as a whole: a write to any resource in it makes
later work on every other resource of that heap wait, and every encoder pays
the driver's bookkeeping for it. The D3D12 runtime orders its encoders itself
with fences (fence-chain = 1, the default: every encoder waits for the one
before it, across command buffers through the device fence), which already
gives every ordering tracking would add, so the tracking only costs.

With madeira.cfg / a game's config heap-untracked = 1 the heap is created
with MTLHazardTrackingModeUntracked (resources placed in it inherit that);
the first lines say so ([heap-untracked]). 0 or unset: Tracked, as before.
Caution: with a looser fence-chain mode (6: barrier-driven, 5) the fences no
longer order everything, and untracked heaps then rely on the game's own
barriers; test it with fence-chain 1 first. Remote mode is untouched.
Native only. Idempotent; fails by name if the anchor moves.
Run from the repository root.
"""
import pathlib
import sys

PATH = pathlib.Path("dxmt/src/winemetal/unix/winemetal_unix.c")
MARKER = "madeira-bcd: heap-untracked"

OLD = """    hd.hazardTrackingMode = MTLHazardTrackingModeTracked;
    params->ret = (obj_handle_t)[(id<MTLDevice>)params->device newHeapWithDescriptor:hd];"""
NEW = """    { /* madeira-bcd: heap-untracked (tools/patch-winemetal-heap-untracked.py) */
      static int untracked = -1, said;
      if (untracked < 0) untracked = madeira_cfg_int("heap-untracked", 0) > 0;
      hd.hazardTrackingMode = untracked ? MTLHazardTrackingModeUntracked : MTLHazardTrackingModeTracked;
      if (untracked && said++ < 3)
        fprintf(stderr, "[heap-untracked] madeira-bcd placement heap %llu MB: hazard tracking OFF (madeira.cfg heap-untracked = 1; "
                        "the D3D12 runtime's encoder fences order its work)\\n", (unsigned long long)(params->size >> 20));
    }
    params->ret = (obj_handle_t)[(id<MTLDevice>)params->device newHeapWithDescriptor:hd];"""

src = PATH.read_text()
if MARKER in src:
    print("patch-winemetal-heap-untracked: already patched")
    sys.exit(0)
fn = src.find("_MTLDevice_newPlacementHeap(void *obj) {")
if fn < 0 or src.count(OLD) != 1 or src.index(OLD) < fn:
    sys.exit("patch-winemetal-heap-untracked: _MTLDevice_newPlacementHeap anchor not found")
PATH.write_text(src.replace(OLD, NEW))
print("patch-winemetal-heap-untracked: placement heaps untracked with madeira.cfg heap-untracked = 1 (patched)")

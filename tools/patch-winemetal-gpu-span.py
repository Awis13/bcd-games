#!/usr/bin/env python3
"""Hand each command buffer's GPU start/end time to the [frame] instrument.

God of War at native 1920x1080 (log 2026-10-01 15:06:44, build 279) left one
question the logs could not answer: is the frame GPU-bound? The only GPU
number was a Metal HUD screenshot (720p menu, GPU ~21-22 ms). DXMT's
winemetal_unix.c already reports every completed command buffer to the
[frame] hooks (ios_frame_gpu: span and queue depth, behind
madeira_frame_hooks_on()), and build/ntdll-unix/server_ios.c now implements
that instrument behind MADEIRA_FRAME_STATS. A sum of spans over-counts when
buffers overlap on the GPU, so the completion handler also passes
GPUStartTime/GPUEndTime to ios_frame_gpu_span(), which keeps their union: the
time the GPU actually had work. "GPU busy" near 100 % of the wall clock =
GPU-bound.

Changes nothing unless MADEIRA_FRAME_STATS is set: the handler that calls it
is only installed when madeira_frame_hooks_on() is true, which needs the
instrument on (ios_frame_stats_on, set from MADEIRA_FRAME_STATS at process
init). Native only (winemetal unix side), not in the i386 farm key.
Idempotent; fails by name if an anchor moves. Run from the repository root.
"""
import pathlib
import sys

PATH = pathlib.Path("dxmt/src/winemetal/unix/winemetal_unix.c")
MARKER = "madeira-bcd: [frame] GPU timeline"

PAIRS = [
    ("""extern void ios_frame_gpu(unsigned long long gpu_ns, unsigned long long inflight);
""",
     """extern void ios_frame_gpu(unsigned long long gpu_ns, unsigned long long inflight);
/* madeira-bcd: [frame] GPU timeline (tools/patch-winemetal-gpu-span.py): the
 * buffer's GPUStartTime/GPUEndTime, whose union is the GPU's busy time. */
extern void ios_frame_gpu_span(double start_s, double end_s);
"""),
    ("""      double span = buffer.GPUEndTime - buffer.GPUStartTime;
      ios_frame_gpu(buffer.GPUStartTime > 0.0 && span > 0.0 ? (unsigned long long)(span * 1e9) : 0, depth);
""",
     """      double span = buffer.GPUEndTime - buffer.GPUStartTime;
      ios_frame_gpu(buffer.GPUStartTime > 0.0 && span > 0.0 ? (unsigned long long)(span * 1e9) : 0, depth);
      ios_frame_gpu_span(buffer.GPUStartTime, buffer.GPUEndTime);   /* madeira-bcd: [frame] GPU timeline */
"""),
]


def main():
    s = PATH.read_text()
    if MARKER in s:
        print("winemetal_unix.c: GPU timeline already patched")
        return 0
    for old, new in PAIRS:
        if s.count(old) != 1:
            sys.exit(f"patch-winemetal-gpu-span: anchor found {s.count(old)} times (want 1) in {PATH}:\n{old}")
        s = s.replace(old, new)
    PATH.write_text(s)
    print("winemetal_unix.c: GPU timeline patched")
    return 0


if __name__ == "__main__":
    sys.exit(main())

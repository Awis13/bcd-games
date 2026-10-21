#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""A 32-bit program on iOS 18 ended in BTCpuThreadInit: the thread map had no initial state.

Every 32-bit Windows program in the intake from an iOS 18 device (pes2013, Stick Fight, Dead Space,
Terraria, Duck Game, Yume Nikki, Half-Life 2, ...; 63 GB address map) died at its first instruction
with the same host fault, `libwow64fex.dll+0x1b2a48` reading TEB+0x58 (the module's implicit-TLS
pointer, NULL for the main thread). That address is `__cxa_get_globals`, called by `__cxa_throw`,
called by `std::__throw_overflow_error` from `std::__next_prime`, called from the lambda of
`std::unordered_map<DWORD, InternalThreadState*>::emplace`, called from BTCpuThreadInit+608 ->
`Threads.emplace(ThreadTID, Thread)`. The throw is the map computing its bucket count as
ceil((size + 1) / max_load_factor()) with max_load_factor() == 0: `Threads` (a global of the WOW64
module's Module.cpp) is all zeros, not the state its static initialiser (_GLOBAL__sub_I_Module.cpp:
`stp q0, q0, [Threads]; str 1.0f, [Threads + 0x20]`) leaves. The throw cannot even unwind, because
the module's thread-local exception state needs the TLS pointer that this thread's TEB lacks
(Wine enters the module from init_wow64 before it exists; see AllocWatch.cpp's note).

The same binary runs the same line on iOS 26/27 devices (512 GB map) without trouble. The cause
(ajan4-ios18wow, 2026-10-21): the static initialiser DID run, on the other copy of the module.
On a kernel where TXM does not police code (iOS 18) the PE view of xtajit.dll stays executable
next to its JIT-pool copy, so the loader's call to the entry point (_CRT_INIT, __main, every
global constructor) ran in place and initialised the view's .data/.bss, while wow64.dll calls
BTCpuProcessInit/BTCpuThreadInit in the pool copy, copied before the entry ran. The fix is in
build/ntdll-unix/virtual_ios.c ([wow-cpu-pool], build 576: EXEC is taken back from that view
when a pool copy exists). This overlay is what is left of the first guess, kept as the failure-path
safety net and the diagnostic: it reports the map's state when the module starts and again when
the first thread is added, and when the map is still the all-zero object it constructs it in place
(the only state a zeroed map lacks is max_load_factor 1.0f, which is what the constructor writes) and
says so. It cannot repair the module's other constructors (SpinWaitLock's CyclesPerNanosecond,
the config layers, the atexit registrations) in such a copy, so a log with a [wow64-ctor] "constructing"
line means [wow-cpu-pool] did not take effect and the next failure is expected elsewhere in the module.
A map that was initialised is never touched; the compare is false for every session that works today.

Anchors are the pinned FEX's (f59cab9); a changed anchor refuses the patch. Apply to the FEX tree
before building the WOW64 module (build/fex-wow64/build.sh and tools/build-xtajit-wow64.sh, the
latter being the CI step that ships xtajit.dll, do; both restore the file after).
"""
from pathlib import Path
import sys

MARKER = "madeira-bcd: wow64-ctor-guard v1"
MODULE = "Source/Windows/WOW64/Module.cpp"

INCLUDE_ANCHOR = "#include <unordered_map>\n"
INCLUDE_ADD = "#include <new> // madeira-bcd: wow64-ctor-guard v1 (placement new for the thread map)\n"

INIT_ANCHOR = "void BTCpuProcessInit() {\n  FEX::Windows::InitCRTProcess();\n"
INIT_ADD = '''#ifdef FEX_IOS_HOST
  /* madeira-bcd: wow64-ctor-guard v1. The state of this module's global thread map as the module
   * starts. A map its static initialiser touched has max_load_factor 1; an all-zero one (0) makes the
   * first emplace throw, and the throw ends the process (see tools/patch-fex-ios-wow-threads-ctor.py). */
  IosRawReport("E [wow64-ctor] madeira-bcd: Threads@{} max_load_factor={} bucket_count={} size={} (1 = the static initialiser ran)",
               static_cast<void*>(&Threads), Threads.max_load_factor(), Threads.bucket_count(), Threads.size());
#endif
'''

ADD_ANCHOR = "  auto ThreadTID = GetCurrentThreadId();\n  Threads.emplace(ThreadTID, Thread);\n"
ADD_ADD = '''#ifdef FEX_IOS_HOST
  /* madeira-bcd: wow64-ctor-guard v1. A thread map that still has no initial state (max_load_factor 0:
   * its static initialiser did not take effect, as on iOS 18) would throw inside emplace below, and the
   * throw ends the process. Construct it now; a map that was initialised never gets here. */
  if (Threads.max_load_factor() <= 0.0f) {
    LogMan::Msg::EFmt("[wow64-ctor] madeira-bcd: Threads@{} max_load_factor={} bucket_count={} size={}: the static initialiser "
                      "had no effect; constructing the thread map now",
                      static_cast<void*>(&Threads), Threads.max_load_factor(), Threads.bucket_count(), Threads.size());
    ::new (static_cast<void*>(&Threads)) std::unordered_map<DWORD, FEXCore::Core::InternalThreadState*>();
  }
#endif
'''

EDITS = (
    (INCLUDE_ANCHOR, INCLUDE_ANCHOR + INCLUDE_ADD),
    (INIT_ANCHOR, INIT_ANCHOR + INIT_ADD),
    (ADD_ANCHOR, ADD_ADD + ADD_ANCHOR),
)


def patch(module):
    if MARKER in module:
        # applied (by this script, or committed into the pin): every edit present exactly once
        if any(module.count(after) != 1 for _, after in EDITS):
            raise ValueError("partial or changed wow64-ctor-guard")
        return module
    for anchor, _ in EDITS:
        if module.count(anchor) != 1:
            raise ValueError("WOW64/Module.cpp anchor changed: " + anchor.strip().splitlines()[0][:60])
    for anchor, after in EDITS:
        module = module.replace(anchor, after, 1)
    return module


if __name__ == "__main__":
    path = Path(sys.argv[1]) / MODULE
    source = path.read_text()
    try:
        result = patch(source)
    except ValueError as error:
        sys.exit("patch-fex-ios-wow-threads-ctor: " + str(error))
    if result != source:
        path.write_text(result)
    print("FEX WOW64: the thread map is constructed when its static initialiser had no effect (iOS 18); "
          "[wow64-ctor] lines report its state")

# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""madeira-bcd: auto-tune's rule table (docs/AUTO-TUNE.md).

Each rule says which existing config line(s) one attempt adds when a session
failed with these log signatures. build/tools/gen-autotune-rules.py turns this
file into app/Madeira/AutoTuneRules.generated.swift; tests/host/check-autotune.py
fails when that file is stale, when a rule names a key the settings catalog does
not know, or when a fixture no longer gets the line that fixed its game.

Growing it: a crash solved with a config line gets a rule here, and a scrubbed
excerpt of the log that showed it under tests/fixtures/autotune/ (with the line
it expects in the fixture's .expect file), in the same commit.

Fields (AutoTuneRule in app/Madeira/AutoTuneCore.swift):
  id        stable name; an override in Documents/autotune-rules.json with the
            same id replaces it, "lines": [] there disables it
  lines     "key = value" each, added together by one attempt
  priority  lower first, among the rules one failed session matches
  all       signals that must all be in the log; a signal is {"has": [strings
            that must all be on one line], "count": at least that many lines}
  any       at least one of these (optional)
  none      none of these (optional): the fix already ran, or the cause is another
  outcomes  "crashed", "killed", "hung", "exited-early" (every failure when absent)
  session   "library" (a direct start) or "dock" (a Steam game through Madeira
            Dock); both when absent
  api       "d3d11" or "d3d12"; either when absent
  ramMinMB / ramMaxMB   the device's RAM from "[device] model=... ram=NMB"
  minStage / maxStage   how far the session got: 1 Wine started, 2 the game's
            modules loaded, 3 first frame, 4 presenting for 20 s, 5 played on
  noRetry   the failure comes back the same on every start: when such a rule matches,
            the unchanged retry is skipped and its line is tried at once
  upgrade   may replace a line auto-tune kept earlier with the same key (never one
            the user sets); without it a rule only adds keys not yet in the layer
  reason    one sentence the app shows with the attempt
  evidence  where the rule comes from (fixtures, HANDOFF, OYUN-AYARLARI)
  fixedIn   a crash only a change in Madeira fixes: the build that fixed it. Such a
            rule has no lines ("lines": []); when it is the best match auto-tune
            stops ("known") and tells the user to update, or to send the report when
            the build that ran already had the fix. Every fix in code gets one, so
            auto-tune never spends attempts on a crash no line can help.

Signals must be lines every session prints, whatever the diagnostic switches
(MADEIRA_LEAN_DIAG, quiet-log, Extended logging).

Owner, 2026-10-17: every fix we make gets a rule here, a config line or a fix in code.
"""


def s(*has, count=None):
    d = {"has": list(has)}
    if count is not None:
        d["count"] = count
    return d


UNHANDLED = [s("Unhandled exception"), s("ACCEPTING terminal root frame"), s("Runtime error!")]

# Memory: iOS closed the game at its memory limit (a "killed" session whose last footprint line
# was at the limit), or the game's own error box said it ran out of memory. A swap file moves
# large allocations out of RAM; how much depends on the device (owner, 2026-10-16: "cihazın
# ihtiyacına göre"). Never added ahead of a failure: on a 12 GB phone the lines cost GTA V Legacy
# smoothness when it did not need them (OYUN-AYARLARI, 2026-10-15). swap-mb alone did nothing
# under classic coverage (HANDOFF: GoT 515), hence swap-mode and swap-min-mb with the first step.
# The second step raises swap-mb when the first was in use ([swap] ml1077 backed) and the game
# was still closed for memory.
SWAP_TIERS = [
    # name, ramMinMB, ramMaxMB, first swap-mb, second swap-mb, evidence
    ("8gb", None, 9000, 3072, 6144, "GTA V Legacy smallRAM lines (3072); RAPOR-8GB"),
    ("12gb", 9001, 13000, 6144, 8192, "HFW on the owner's 12 GB phone (6144); Rift Apart v1 (8192)"),
    ("16gb", 13001, None, 3072, 6144, "HFW for the 16 GB iPad (3072, OYUN-AYARLARI 2026-10-15)"),
]
MEMORY_DIALOG = [s("[win-name] #", "out of memory"), s("[win-name] #", "Out of memory"), s("[win-name] #", "Out Of Memory"),
                 s("[win-name] #", "not enough memory"), s("[win-name] #", "Not enough memory"),
                 s("[win-name] #", "OUT OF MEMORY"), s("[win-name] #", "insufficient memory"), s("[win-name] #", "Insufficient memory")]


def swap_rules():
    out = []
    for name, lo, hi, first, second, ev in SWAP_TIERS:
        ram = {k: v for k, v in (("ramMinMB", lo), ("ramMaxMB", hi)) if v is not None}
        base = ["swap-mb = %d" % first, "swap-mode = 2", "swap-min-mb = 1"]
        out.append(dict(id="swap-%s-memory-kill" % name, lines=base, priority=50,
                        all=[s("[footprint] rev=ml358 phys=")], outcomes=["killed"],
                        reason="iOS closed the game at its memory limit; a %d MB swap file moves large "
                               "allocations out of RAM on this device." % first,
                        evidence=[ev], **ram))
        out.append(dict(id="swap-%s-memory-dialog" % name, lines=base, priority=50,
                        all=[s("[footprint] rev=ml358 phys=")], any=MEMORY_DIALOG, outcomes=["crashed", "exited-early"],
                        reason="The game said it ran out of memory; a %d MB swap file moves large allocations "
                               "out of RAM on this device." % first,
                        evidence=[ev], **ram))
        out.append(dict(id="swap-%s-more" % name, lines=["swap-mb = %d" % second], priority=55, upgrade=True,
                        all=[s("[swap] ml1077 backed")], outcomes=["killed"],
                        reason="The game was closed for memory again with the swap file in use; the line "
                               "raises it to %d MB." % second,
                        evidence=[ev], **ram))
    return out


SWAP_RULES = swap_rules()

RULES = [
    {
        "id": "metal-layer-format",
        "lines": [],
        "fixedIn": 560,
        "priority": 1,
        # iOS ends the whole app when a CAMetalLayer is given a pixel format it does not take.
        # A D3D9 game with a 16-bit back buffer (R5G6B5: 40, X1R5G5B5: 43) did that at its first
        # Present; tools/patch-dxmt-layer-format.py (build 560) presents such a chain into
        # BGRA8Unorm. No config line changes it: the tester's auto-tune spent three attempts.
        "all": [s("'CAMetalLayerInvalid'", "invalid pixel format")],
        "reason": "BCD's Direct3D 9 layer handed the screen a 16-bit color format iOS does not "
                  "take, and iOS closed the app.",
        "evidence": ["warcraft-3-2026-10-16-22-24-07",
                     "HANDOFF 2026-10-16: Warcraft III, build 559; tools/patch-dxmt-layer-format.py (560)"],
    },
    {
        "id": "excl-logical-rwx-page",
        "lines": [],
        "fixedIn": 575,
        "priority": 1,
        # ntdll's LDAXR W11,[X8]; AND W11,W11,W10; STLXR W12,W11,[X8] on a word in the RWX memory
        # of a Unity/Mono runtime: the page is physically RX, the STLXR faults, and ml1284's
        # emulation refused a pair with an AND (it recovered the loaded value only through ADD/SUB
        # immediates). The fault came back 256 times and the redelivery guard ended the app.
        # build/ntdll-unix/signal_arm64_ios.c (build 575) applies AND/ORR/EOR/BIC/ORN/EON of another
        # register to the memory's current value, atomically. No config line changes it.
        # The pair is named by its bytes ([guest-bytes]) or, when that line is not printed for it, by
        # the store emulator's own line for the STLXR (Cult of the Lamb Q2MJ7N on 565, TEVI J75807 on
        # 574: the same ntdll+0x28408, the same storm).
        "all": [s("identical redeliveries")],
        "any": [s("[guest-bytes]", "6b 01 0a 0a 0b fd 0c 88"), s("[store-undecoded]", "insn=0x880cfd0b")],
        "reason": "A locked bit-clear in memory BCD keeps writable only through an alias was not "
                  "emulated, and the same fault came back until BCD ended the game.",
        "evidence": ["graveyard-keeper-2-2026-10-19-19-06-53", "tevi-2026-10-19 (J75807)",
                     "Cult of the Lamb Q2MJ7N, Mafia II DE 8KJGTX, Caves of Qud PZYMXY",
                     "tests/host/check-excl-rmw.py (build 575)"],
    },
    {
        "id": "tail-carve-lost-exec",
        "lines": [],
        "fixedIn": 576,
        "priority": 1,
        # Dead Space (97E8JF), Star Wars: The Force Unleashed (81A30H, R10XKS, 5EAVVG), build 574,
        # 2026-10-20: a 32-bit program's unnamed image copy was followed by a run of pool pages that
        # had lost EXEC for good (max_prot 3: "[pool-rot] ... LOST EXEC", "[pool-poison] HANDED OUT
        # NON-EXEC"); the pool was 400-560 MB, and the 128 MB code buffer FEX asked for lay on that
        # run. Its first block faulted at carve+0x1c ([exec-recover] mprotect_rx=-1 errno=13,
        # [fault_rip] entryprot=3, [av-detail] p0=EXECUTE p1_in_carve=1) and the exception dispatcher,
        # compiled into the same page, faulted again until the redelivery guard ended the process.
        # build/ntdll-unix/virtual_ios.c (build 576) refuses a tail carve whose first 16 MB cannot
        # execute, so FEX halves the buffer and the next carve lies above the dead run
        # (tests/host/check-tail-carve-exec.py). No config line changes it; pool-split = 1 would only
        # help by luck of where the run lies.
        "all": [s("[exec-recover] pg=", "mprotect_rx=-1", "pool-rx"),
                s("[av-detail] ml613", "p0=EXECUTE", "p1_in_carve=1"),
                s("[fault_rip]", "PROTECTION_FAILURE", "entryprot=3 nowprot=3"),
                s("BUS EXEC in JIT .data")],
        "reason": "Part of the JIT pool had lost the right to run code, and the 128 MB buffer the emulator "
                  "asked for was placed on it; from this build on BCD refuses such a buffer and the "
                  "emulator takes a smaller one.",
        "evidence": ["dead-space-2026-10-20 (97E8JF)", "star-wars-the-force-unleashed 81A30H, R10XKS, 5EAVVG",
                     "tests/host/check-tail-carve-exec.py (build 576)"],
    },
    {
        "id": "x18-repair-unrecognized-load",
        "lines": [],
        "fixedIn": 576,
        "priority": 1,
        # iOS zeroes x18 when it preempts a thread; the Mach handler repairs a TEB access that faulted
        # on the small offset by doing the load itself (signal_arm64_ios.c, "[x18-emul3]"). It did not
        # decode the sign-extending loads, so `ldrsw x28, [x18, #0x180c]` (0xb9980e5c; FEX's read of the
        # 64-bit TEB's WowTebOffset in the WOW64 entry code) was declined and the thread died:
        # Left 4 Dead 2 (RTTGKM, 200 s into the game), FFXIV's WebView2 setup process (TY3K5R), build 574,
        # 2026-10-20. LDRSW/LDRSB/LDRSH with an unsigned offset are decoded from build 576
        # (tests/host/check-x18-signed-load.py). No config line changes it. The signal is the handler's own
        # line for a TEB access it could not repair; it is printed whatever the diagnostic switches.
        "all": [s("[x18-emul3] UNRECOGNIZED insn=")],
        "any": UNHANDLED,
        "reason": "iOS cleared a register the emulator relies on, and BCD could not repair the access that "
                  "followed; from this build on it can.",
        "evidence": ["left-4-dead-2-2026-10-20 (RTTGKM)", "FFXIV TY3K5R (same instruction, same offset)",
                     "tests/host/check-x18-signed-load.py (build 576)"],
    },
    {
        "id": "wow64-thread-map-uninitialised",
        "lines": [],
        "fixedIn": 576,
        "priority": 1,
        # Every 32-bit program of the intake from an iOS 18 device (63 GB address map), 16 logs of 16, builds 573 and 574
        # (pes2013 x6, Stick Fight, Dead Space x2, Terraria, Duck Game QDH12X, Yume Nikki, Half-Life 2 TCKWZY, You Have 10
        # Seconds, speed.exe; none of the 44 on iOS 27): the first thread died in BTCpuThreadInit (libwow64fex.dll) on a
        # NULL read of TEB[0x58] (`ldr x8,[x9,x8,lsl #3]` in __cxa_get_globals) while throwing
        # std::overflow_error("__next_prime overflow") out of `Threads.emplace`, the module's global unordered_map, which
        # was still all zero (max_load_factor 0).
        # CAUSE (ajan4-ios18wow): the map's constructor DID run, on the other copy of the module. Where the kernel does
        # not police code (iOS 18; iOS 26 without TXM) the plain mprotect(r-x) in mprotect_exec succeeded on xtajit.dll's
        # PE view, so the loader's call to the entry point ran in place (log: [x18-emul3] pc = module base + 0x10a8, in
        # _CRT_INIT): DllMainCRTStartup, `__main` and every global constructor initialised the PE view's .data/.bss, while
        # wow64.dll called BTCpuProcessInit/BTCpuThreadInit in the module's POOL copy (made before the entry ran) with its
        # zeroed .bss. From build 576 build/ntdll-unix/virtual_ios.c takes EXEC back from that view when the pool copy
        # exists ([wow-cpu-pool], env.MADEIRA_WOW_CPU_POOL_ONLY = 0 restores the old behaviour; tests/host/check-wow-cpu-pool.py),
        # so the entry point and the constructors run in the pool copy as on iOS 27. The xtajit.dll built by CI also
        # carries a safety net (tools/patch-fex-ios-wow-threads-ctor.py, applied by tools/build-xtajit-wow64.sh): it
        # constructs the map if it still finds it zero and logs "[wow64-ctor]" lines with the map's state. No config line
        # changes any of it.
        # Signals: the Mach handler's line for the crashing PC inside the module, the x86 TLS line (the pointer that is
        # not there), and the start-up line that only a device without TXM prints (iOS 27 prints "not here"). All three
        # are in the 16 logs and in no other log of the intake (3MSXHS, iOS 27, crashes in the same module for another
        # reason and has no "allowed" line). A log of build 576 or later that still matches is a case for the report.
        "all": [s("[mach_exc] pc PE: libwow64fex.dll+"), s("[x86_seg]", "TEB[0x58]=0x0"),
                s("[jit-nodebug] debugger-less JIT pool: allowed")],
        "reason": "BCD ran the 32-bit emulator's start-up code from a different copy of the module than the one "
                  "it then used, so every 32-bit program stopped before its first instruction; from this build "
                  "on both use the same copy.",
        "evidence": ["half-life-2-2026-10-20 (TCKWZY, iPad16,3, iOS 18.5)",
                     "Duck Game QDH12X (iPhone13,1, iOS 18.6): [x18-emul3] #1 pc=0x3ffac10a8 = libwow64fex base 0x3ffac0000 + _CRT_INIT+0xa8",
                     "pes2013 4GQK0Y, 853B78, 7Y599P, B39EXN, KF01JG, TYAKAG; Dead Space DVXWGA, E05X42; Stick Fight KDY8E6; "
                     "Terraria 9VGQVC; Yume Nikki E69NBH; You Have 10 Seconds TTHVRF; speed.exe BFRJ1Z; S67X5G",
                     "docs/research/ios18-2026-10-18/runtime.md section 3.4 (PE images may execute in place on iOS 18)",
                     "tests/host/check-wow-cpu-pool.py, tests/host/check-fex-wow-threads-ctor.py (build 576)"],
    },
    {
        "id": "jit-pool-unanswered-exit",
        "lines": [],
        "fixedIn": 572,
        "priority": 1,
        # A debugger had attached and left without BCD's script (StikDebug's default on iOS 18 and on
        # iOS 26 devices it treats as non-TXM, or JIT turned on from StikDebug's app list). CS_DEBUGGED
        # stood for "attached", so Play asked a debugger that was gone for the JIT pool; nothing answered
        # and BCD quit itself 10 s later. ios18-jit (572): readiness asks P_TRACED, iOS 18 and iOS 26
        # without TXM build the pool without a debugger, and a failed request no longer quits BCD.
        "all": [s("Debugger failed to allocate RX memory"), s("BAD POOL — exiting now")],
        "reason": "BCD asked a debugger that had already left for its JIT memory and then closed itself; "
                  "no setting changes that.",
        "evidence": ["synthetic-jit-pool-unanswered-exit",
                     "docs/research/ios18-2026-10-18/runtime.md §2.3 (a); branch ios18-jit"],
    },
    {
        "id": "jit-pool-brk-no-debugger",
        "lines": [],
        "fixedIn": 572,
        "priority": 1,
        # BCD started already debugged (StikDebug's app list, LiveContainer's JIT launch, Xcode) installed
        # no SIGTRAP fallback, and once that debugger had left, the pool request's BRK ended the app: the
        # log stops after the pool census. ios18-jit (572): the fallback is installed whenever CS_DEBUGGED
        # is set too, and the request goes to a debugger only when one is attached.
        "all": [s("Debugger attached — skipping SIGTRAP handler (debugger handles BRK)"),
                s("[jit-debugger] attached=1 at the pool request")],
        "none": [s("RX pool at"), s("Debugger failed to allocate RX memory"), s("[jit-pool] path:")],
        "reason": "BCD was started with a debugger that had left before the game started, and its JIT "
                  "memory request ended the app; no setting changes that.",
        "evidence": ["synthetic-jit-pool-brk-no-debugger",
                     "docs/research/ios18-2026-10-18/runtime.md §2.3 (b); branch ios18-jit"],
    },
    {
        "id": "device-gpu-tier1",
        "lines": [],
        "fixedIn": 572,
        "priority": 2,
        # A GPU with Tier 1 argument buffers (A12 and older: iPhone XS, XS Max, XR, the A12 iPads,
        # the A10 iPads) cannot run DXMT or the D3D12 runtime, which write GPU addresses straight
        # into argument buffers: games drew garbage, faulted the GPU or were ended; the A10 also
        # stops every 64-bit program (no LSE atomics, no LDAPR). No line helps. From build 572 BCD
        # refuses such a launch with a message (app/Madeira/DeviceCapability.swift) and logs the
        # tier ("[device] gpu=... argument-buffers=tier1"). iOS 18 audit, gameplay.md section 1.1.
        "all": [s("[device] model=")],
        "any": [s("[device] model=iPhone11,"), s("[device] model=iPad7,"), s("[device] model=iPad8,"),
                s("[device] model=iPad11,"), s("[device] gpu=", "argument-buffers=tier1")],
        "reason": "This device's graphics chip (A12 or older) cannot run Direct3D games in BCD; from this "
                  "build on BCD says so before a game starts instead of crashing.",
        "evidence": ["synthetic-device-gpu-tier1",
                     "docs/research/ios18-2026-10-18/gameplay.md section 1.1 (Metal feature set tables: "
                     "argument buffers tier 2 = Apple6, A13)"],
    },
    {
        "id": "d3d12-helper-kernels-ios18",
        "lines": [],
        "fixedIn": 572,
        "priority": 90,
        "api": "d3d12",
        # iOS 18's Metal refused the D3D12 runtime's helper kernels: their metallib took the iOS 26.2
        # SDK's own target (AIR 2.8) and iOS 18 reads AIR 2.7 at most ("This library is using a
        # deployment target (0x00020008) that is not supported on this OS"). Indirect DXIL
        # tessellation draws were skipped, clear-rects and fi-nan-clean went quiet. Fixed in code
        # (build 572): a copy built for iOS 18 loads when the first is refused. Last in priority:
        # the kernels' absence degrades a game rather than ending it, so a matching line goes first.
        "all": [s("[madeira-d3d12] helper kernels: NOT available"), s("helper kernels:", "deployment target")],
        "none": [s("[madeira-d3d12] helper kernels: the iOS 18 copy loaded")],
        "reason": "iOS 18 refused the Direct3D 12 runtime's helper shaders, so indirect tessellation draws were "
                  "skipped; from this build on BCD carries a copy iOS 18 loads.",
        "evidence": ["synthetic-d3d12-helper-kernels-ios18",
                     "docs/research/ios18-2026-10-18/gameplay.md section 1.2 (mad_kernels: iOS 26.2, AIR 2.8)"],
    },
    {
        "id": "x64-image-smc-nocopy",
        "lines": ["env.MADEIRA_X64_IMAGE_NOCOPY = 1"],
        "priority": 14,
        # Mad Max (6DB55D, KK9H24, VZKCF3, build 569, one iPhone17,2): the 64-bit MadMax.exe writes into its own
        # 66 MB RWX section with locked read-modify-writes (`lock xadd [rcx], eax` at 0x143291fe2 on 0x143291ff0,
        # then an xchg: ldaddal / swpal on the host). FEX had armed the page (PAGE_EXECUTE_READ), the store
        # faulted ([store-noalias] ... prot=1 max=3), FEX's untrap (PAGE_EXECUTE_READWRITE) went through
        # mprotect_exec, which on the default path keeps EXEC out of the backing and, with a pool copy in
        # charge, never gives the page back its write ([wr-strip] ... wine_want=5 host_prot=1 (not a strip),
        # vprot 0x25 at every fault). Every pass through the loop cost two faults and a recompile, and the
        # redelivery guard ended the process at 2000 identical deliveries (L12625 "256 identical", L17944).
        # It is the symptom virtual_ios.c's ml1030 note describes for 32-bit images ("wine_want=5 host_prot=1",
        # "the emulator untraps with another PAGE_EXECUTE_READWRITE that lands in the same dead end"), whose
        # 64-bit counterpart is MADEIRA_X64_IMAGE_NOCOPY: a pure x64 image gets no pool copy, EXEC is dropped
        # from its requests, so the emulator's trap is a read-only page and its untrap a writable one. The
        # switch is already in the recommended lists of GTA V, RDR2, HFW, Rift Apart and MSFS 2024.
        # MADEIRA_WOW_RWX_PLAIN (which the older rule below adds to this symptom) is for 32-bit windows (its
        # own log line says so) and did nothing for Mad Max in the three logs. Not proven on a Mad Max run:
        # it is an attempt, the one the code points to. "none": the image already runs without a copy.
        "all": [s("[store-noalias]", "prot=1 max=3"),
                s("[wr-strip-declined]", "SEC_IMAGE"),
                s("[redeliv]", "identical redeliveries")],
        "none": [s("[x64-image]", "mapped without a pool copy")],
        "reason": "The game rewrites its own code and the emulator's write permission never reached the "
                  "program's memory, so one store faulted until BCD ended the game; the line lets the "
                  "program's image take writes the way the emulator's trap expects.",
        "evidence": ["mad-max-2026-10-20 (VZKCF3, KK9H24, 6DB55D)",
                     "virtual_ios.c ml1030 / ios_x64_image_nocopy_enabled (the same symptom for 32-bit images)",
                     "Dark Souls II, one iPhone, 2026-10-19: with the line (DN5QSB, E6G3RQ) 379 s of play after the same "
                     "256-delivery warning; without it (72R0CG, P9C7DG) the session ended 34 s in"],
    },
    {
        "id": "wow-rwx-plain-smc",
        "lines": ["env.MADEIRA_WOW_RWX_PLAIN = 1"],
        "priority": 15,
        # A 32-bit game that patches its own code (Warcraft II's blitters): FEX marks the page
        # read-only once it has translated code from it, and on the next store gives it back
        # read-write. With the switch unset (Wine Mono only) the game's RWX memory is neither
        # in the JIT pool nor plain, so the page stays read-only after FEX's untrap ([store-noalias]
        # ... exec-downgraded page that never got dual-mapped) and the same store faults until
        # the redelivery guard ends the app. As plain memory it behaves as on Windows (ml1279).
        "all": [s("[wow-rwx] ml1279 MADEIRA_WOW_RWX_PLAIN=(unset)"),
                s("[store-noalias]", "prot=1 max=3", "exec-downgraded page that never got dual-mapped")],
        "any": [s("[redeliv] 256 identical redeliveries"), s("[redeliv] 2000 identical redeliveries")],
        "reason": "The game rewrites its own code and BCD left that memory read-only after the "
                  "emulator released it, so one store faulted forever; the line makes the game's "
                  "executable memory plain read/write, as on Windows.",
        "evidence": ["warcraft-2-bne-2026-10-16-23-27-45",
                     "HANDOFF 2026-10-17: Warcraft II BNE, build 559 (tester, iPad16,5)"],
    },
    {
        "id": "wow-rwx-plain-unaligned-atomic",
        "lines": ["env.MADEIRA_WOW_RWX_PLAIN = 1"],
        "priority": 15,
        # The same switch for a second loop: a 32-bit game's RWX chunk (Unity Mono's 1 MB JIT
        # chunks: a multiple of 64 KB, so pool-aliased) gets an unaligned atomic swap, which the
        # store emulator refuses ([swp-emul] ... REFUSING unaligned atomic) and hands to FEX,
        # whose store faults again; FEX untraps the page as SMC, but the pool alias keeps it
        # read-only ([alias-own] ... no mprotect) until the redelivery guard ends the app.
        "all": [s("[wow-rwx] ml1279 MADEIRA_WOW_RWX_PLAIN=(unset)"),
                s("[swp-emul] ml626", "REFUSING unaligned atomic")],
        "any": [s("[redeliv] 256 identical redeliveries"), s("[redeliv] 2000 identical redeliveries")],
        "reason": "The game swaps values in its own executable memory at unaligned addresses, which "
                  "BCD's write emulation refuses, so one store faulted forever; the line makes the "
                  "game's executable memory plain read/write, as on Windows.",
        "evidence": ["breakneck-2026-10-17-12-53-34",
                     "HANDOFF 2026-10-17: Breakneck (Steam 432940, Unity), build 562, Send Log JW195K and 47GP93",
                     "owner, build 563: with the line the game runs well"],
    },
    {
        "id": "cage-release-band-exhausted",
        "lines": ["env.MADEIRA_CAGE_RELEASE = 1"],
        "priority": 10,
        "all": [s("[va-scan] FAILED", "gaps-exhausted"), s("[cage] holdback reserved")],
        "any": [s("[dll-missing]", "status=c0000017"), s("[delay-fail]", "status=c0000017"),
                s("[thr-create] FAILED at")] + UNHANDLED,
        "none": [s("[cage] holdback handed to the allocator")],
        "session": "library",
        "reason": "The game's address range ran out while 8 GB of it was kept for a Chromium cage this "
                  "game never asks for; the line hands that space over when the range is full.",
        "evidence": ["riftapart-2026-10-15-23-02-21", "riftapart-2026-10-15-22-49-00",
                     "HANDOFF 2026-10-15 (late, 2-4): Rift Apart ran with it on 550"],
    },
    {
        "id": "fex-band-steer-keep",
        "lines": ["env.MADEIRA_FEX_BAND_KEEP_MB = 6144"],
        "priority": 20,
        "outcomes": ["crashed"],
        # No Rest for the Wicked (0M4SJD, build 574): the game reserved 24 ranges of 511 MB, which BCD's steer
        # valve places in the emulator's own 16 GB range at 0x7c00000000 once the game's range is tight
        # ([steer] #3 ... "above ceiling 0x7c.." x8, 11.7 GB of the band by the 23rd). The emulator held 3.8 GB
        # at the first refused steer and 4.0 GB 4500 lines later; its 4 MB-aligned request then found no gap
        # ([va-scan] FAILED window=0x7c00000000..0x8000000000 size=0x400000 ... gaps-exhausted), FEX stopped on
        # purpose ("[alloc-fail] ml798 FEXAlloc could not allocate ... band=0x7c00000000..0x7fffffffff"), a trap
        # in libarm64ecfex.dll, exit c000001d. The line (build 576) keeps the lowest 6 GB of the arena for the
        # emulator; steered reserves that no longer fit go to [0x7400000000, 0x7800000000), as every one
        # already did after the band was full ([steer] #28..#30). "none": the session already ran with the
        # key ([fex-keep] line) and the arena still filled; no second attempt follows. The band named in the
        # [alloc-fail] line must be the hardware one at 0x7c00000000: with env.MADEIRA_SC_PA_POOLS the arena
        # starts higher (GTA V Enhanced, 0x7d00000000) and the key does not apply there.
        "all": [s("[alloc-fail] ml798 FEXAlloc could not allocate", "band=0x7c00000000..0x7fffffffff"),
                s("[steer] #", "above ceiling 0x7c", count=3)],
        "none": [s("[fex-keep] madeira-bcd:")],
        "reason": "The game's own reservations filled the memory range BCD keeps for the emulator, and the "
                  "emulator ran out of room and stopped; the line keeps the lowest 6 GB of that range for the "
                  "emulator, and the game's reservations that no longer fit go elsewhere.",
        "evidence": ["no-rest-for-the-wicked-2026-10-20 (0M4SJD, build 574)",
                     "tests/host/check-fex-band-keep.py (build 576)"],
    },
    {
        "id": "dotnet-wx-off",
        "lines": ["env.DOTNET_EnableWriteXorExecute = 0"],
        "priority": 10,
        "outcomes": ["crashed"],
        "noRetry": True,
        # Quaver (DKEQ3C, build 574; a self-contained .NET runtime: hostfxr, hostpolicy, coreclr.dll): the CLR
        # asserts "0xf0f0f0f0f0f0f0f0 == *(UINT64*)m_pRegionToGenTableImmediate" (jitinterfaceamd64.cpp:514, the
        # write-barrier patcher) and fails fast, c0000602. .NET 7+ keeps its code in a W^X "double mapping": one
        # section mapped twice, a PAGE_READWRITE view it writes through (log: NtMapViewOfSection handle=2C
        # base=0x13f650000, map-notify prot=4) and a PAGE_EXECUTE_READWRITE view it runs from (base=0xf54830000,
        # prot=40). BCD turns the executable view into a fresh JIT-pool slot ("[jit-pool] anon RWX 0xf54830000
        # ... NEW"), so what the CLR copies through the writable view never shows in the executable one and the
        # barrier's template reads back as zeroes. DOTNET_EnableWriteXorExecute=0 is the runtime's own switch for
        # plain executable memory (VirtualAlloc, which BCD serves the way it serves Wine Mono's JIT). "none":
        # the session already ran with the line ([madeira-env] game ...) and the assert still came.
        "all": [s("CLR: Assert failure", "m_pRegionToGenTableImmediate")],
        "none": [s("[madeira-env] game DOTNET_EnableWriteXorExecute=0")],
        "reason": "The game's .NET runtime keeps its code in two views of one memory section, and BCD cannot "
                  "keep the two views the same; the line makes the runtime use plain executable memory.",
        "evidence": ["quaver-2026-10-20 (DKEQ3C, build 574)"],
    },
    {
        "id": "d3d12-pipeline-library-fatal",
        "lines": ["d3d12-pipeline-library = 1"],
        "priority": 12,
        # Returnal (Unreal; CWKVD7, build 574, iPhone18,2): the RHI makes its pipeline library at start,
        # `pDevice1->CreatePipelineLibrary(nullptr, 0, ...)`, and the runtime's generated stub answers
        # E_NOTIMPL ("[madeira-d3d12] unimplemented: ID3D12Device10::CreatePipelineLibrary", L16518); the
        # engine's LowLevelFatalError names the call (L16524, also the error box) and the game exits 3.
        # GTA V Enhanced and RDR2 make the same call and carry on with the failure, so the key is a
        # switch: d3d12-pipeline-library = 1 makes a library that keeps nothing (madeira_d3d12.c, build
        # 576; tests/host/check-d3d12-pipeline-library.py). Not proven on a Returnal run. The fatal
        # message is the discriminator: the stub line alone is in the logs of games that play on.
        "all": [s("[madeira-d3d12] unimplemented: ID3D12Device10::CreatePipelineLibrary"),
                s("LowLevelFatalError", "CreatePipelineLibrary")],
        "none": [s("[madeira-d3d12] d3d12-pipeline-library = 1")],
        "reason": "The game's engine insists that Direct3D 12 can make a pipeline library and stops when "
                  "the call fails; the line makes BCD answer it with a library that keeps nothing.",
        "evidence": ["returnal-2026-10-20 (CWKVD7, build 574)",
                     "tests/host/check-d3d12-pipeline-library.py (build 576)"],
    },
    {
        "id": "locale-en-us",
        "lines": ["env.LC_ALL = en_US.UTF-8"],
        "priority": 12,
        # The game itself asked for the user locale's OEM code page: Wine loads 1252/437 at start,
        # and in 30 sessions of other games on the same Turkish phone 857 never loaded. In all three
        # Rift Apart crashes it was the line right before the fatal C++ throw.
        "all": [s("[nls-getptr] type=11 id=857")],
        "any": UNHANDLED,
        "reason": "Wine used the phone's Turkish locale and the game failed right after loading its code "
                  "page; the line gives Wine an English locale for this game.",
        "evidence": ["rift-apart-2026-10-16-10-43-45", "rift-apart-2026-10-16-10-45-10", "rift-apart-2026-10-15-22-49-00",
                     "owner 2026-10-16: with LC_ALL added the game opened"],
    },
    {
        "id": "locale-en-us-ansi",
        "lines": ["env.LC_ALL = en_US.UTF-8"],
        "priority": 12,
        "noRetry": True,
        # AN EXPERIMENT, not a proven fix (ajan3-dyinglight, 2026-10-21). Dying Light (Steam 239140),
        # the owner's iPhone18,2, build 575, logs SAHIP4/5/6 (three identical runs; MXXHZK and 8VB504
        # are the same sessions): engine_x64_rwdi.dll takes a NULL from a named lookup on a singleton
        # (+0x332ee, `call [rax+0x330]` then `mov eax,[rax+0x4c]`), 2 s after the display enumeration
        # ("[Video] > Monitor(0)") and before any renderer DLL loads; no frame is ever presented.
        # What points at the locale: the game itself asks for the Turkish ANSI code page (id=1254,
        # in groups of six, four times; Wine's own start-up loads 1252/437), and in 96 logs of that
        # phone only Dying Light (5 logs) and one Counter-Strike 2 log load it. Ratchet & Clank: Rift
        # Apart on the same phone asked for the Turkish OEM page (857), threw, and ran with LC_ALL
        # (rule locale-en-us above). Against it: Turkish players run Dying Light on Windows. The line
        # is the cheapest test; auto-tune keeps it only if the next session gets further.
        # Bound to the game's window title so no other 1254 loader (Counter-Strike 2 has a different
        # failure) is touched by this evidence.
        "all": [s("[nls-getptr] type=11 id=1254"), s("[win-name] #", 'text="Dying Light"')],
        "any": UNHANDLED,
        "reason": "The game asked for the phone's Turkish code page and ended at start-up; Rift Apart did "
                  "the same and ran with an English locale, so this attempt gives Wine one for this game.",
        "evidence": ["dying-light-owner-2026-10-20 (SAHIP4, SAHIP5, SAHIP6; MXXHZK, 8VB504)",
                     "rift-apart-2026-10-16-10-43-45 (same phone, same kind of event, LC_ALL fixed it)",
                     "UNPROVEN: the first session with the line decides"],
    },
    {
        "id": "alias-release-arena-reuse",
        "lines": ["env.MADEIRA_ALIAS_RELEASE = 1"],
        "priority": 14,
        # The game released memory it had made executable and reserved it again at the same base:
        # a decommit over the stale JIT pool record was refused, and the first store found a fresh
        # page left read-only (prot=1, max=3: plain memory, not a pool page) with no alias. The
        # image-region [store-noalias] line every Rift Apart session prints has prot=5 max=5.
        "all": [s("[alias-tomb] STALE alias decommit REFUSED"), s("[store-noalias]", "prot=1 max=3")],
        "none": [s("[alias-release]")],
        "reason": "The game freed a memory region and reserved it again, and BCD's records of the code it "
                  "had made there outlived the release; the line drops them with the region.",
        "evidence": ["rift-apart-2026-10-16-11-22-59", "rift-apart-2026-10-16-11-21-36",
                     "rift-apart-2026-10-16-12-32-19: with the line the save loaded (555)"],
    },
    {
        "id": "jumbo-half-probe",
        "lines": ["env.MADEIRA_JUMBO_HALF = 1"],
        "priority": 20,
        "all": [s("[jumbo] kernel-pick reserve failed", "top window is full"),
                s("[device] address-map=", "extended-va=1")],
        "any": [s("kernel-pick reserve failed", "for size=0x100000000 "),
                s("kernel-pick reserve failed", "for size=0x200000000 "),
                s("kernel-pick reserve failed", "for size=0x400000000 ")],
        "none": [s("[jumbo-half]")],
        "reason": "A reservation of 4 GB or more found no hole; the game probes with a double-size "
                  "reservation and keeps half, so the line gives it that half.",
        "evidence": ["riftapart-2026-10-15-22-07-15", "HANDOFF 2026-10-15 (evening): Rift Apart 549"],
    },
    {
        "id": "readback-probe-freed-buffer",
        "lines": ["env.MADEIRA_LUM_PROBE = 0"],
        "priority": 5,
        "noRetry": True,
        # Batman: Arkham Asylum (1Q05G8, RKHMA6), Brutal Legend (82BAXZ), Dishonored (0RA62D, 5B06QT),
        # build 574, 2026-10-19/20: the [rb-tex] readback probe read the previous readback buffer's memory
        # after the game had freed it (BUS->AV at MTLBlitCommandEncoder_encodeCommands+3260, c0000005).
        # Build 575 reads it safely (tools/patch-winemetal-probe-safe-read.py); on 574 the line turns the
        # probe off, which changes only log lines. Many readbacks logged: games with one or two (NFS Most
        # Wanted, Warcraft III) end elsewhere.
        # A read (rw=0) that ended the game, not a fault the guest kept retrying (Star Wars: The Force
        # Unleashed, 5EAVVG: a write and a redelivery storm, with as many readbacks logged).
        "all": [s("BUS->AV: unreadable target", "rw=0"), s("[rb-tex] madeira-bcd new readback", count=8)],
        "none": [s("identical redeliveries")],
        "reason": "A logging probe read GPU readback memory the game had already freed; turning the "
                  "probe off avoids it (build 575 reads it safely).",
        "evidence": ["batman-arkham-asylum-2026-10-19 (1Q05G8)", "brutal-legend 82BAXZ, dishonored 0RA62D/5B06QT",
                     "tests/host/check-probe-safe-read.py"],
    },
    {
        "id": "pool-split-exhausted",
        "lines": ["pool-split = 1"],
        "priority": 11,
        "noRetry": True,
        # Counter-Strike 2 (owner's iPhone18,2, build 574, 2026-10-20; logs d38e1274, 981cb2fc, b657f2e4):
        # no free hole fits the 896 MB pool (ml1036), the pool shrinks to 544-592 MB, and cs2.exe's DLLs
        # fill it ([jit-pool] EXHAUSTED -> STATUS_NO_MEMORY, "The system ran out of memory while loading
        # the game"). x64-image-nocopy did not get it further; pool-split = 1, added by hand, started it
        # (the pool takes two runs, about 880 MB). Also Albion Online's launcher and Spelunky 2
        # (2026-10-20 logs BYBFCH, PGKTDD, J2YJF8). Only a game that already failed this way gets it.
        # After x64-image-nocopy (priority 10), which fixed Spider-Man: Miles Morales: that path stays as it was.
        "all": [s("[jit-pool] EXHAUSTED")],
        "reason": "The JIT pool was smaller than usual and filled up while the game loaded; a split "
                  "pool is about 880 MB.",
        "evidence": ["counter-strike-2-owner-2026-10-20 (pool-split by hand started it)",
                     "albion-launcher BYBFCH/PGKTDD, spelunky-2 J2YJF8 (2026-10-20)"],
    },
    {
        "id": "x64-image-nocopy",
        "lines": ["env.MADEIRA_X64_IMAGE_NOCOPY = 1"],
        "priority": 10,
        "noRetry": True,
        "all": [s("[jit-pool] EXHAUSTED (image")],
        "reason": "The JIT pool ran out while DLLs were copied into it; pure x64 DLLs can run without "
                  "a copy.",
        "evidence": ["milesmorales-tester-548", "HANDOFF: HFW 517, MSFS 2024 538, GTA V Enhanced 422"],
    },
    {
        "id": "pool-bss-cut-pool",
        "lines": ["pool-bss = 1", "pool-floor = 0"],
        "priority": 9,
        # madeira-bcd pool-reserve (2026-10-18): start-up mappings take the JIT pool's range before the
        # ml1040 constructor runs (ml1135; RDR2 build 470: a 604 MB pool and ERR_GFX_INIT; launch C:
        # EXHAUSTED and a crash). A build made with BCD_POOL_BSS_MB holds that range from exec time; this
        # game did not use it. pool-bss = 1 places the pool at the reservation's start, pool-floor = 0
        # lets that start lie under 0x119000000 (behind its RW->RX self-test), for a full 896 MB below the
        # window. Only a reservation build prints the first signal, so a build without one never tries it.
        "all": [s("[pool-bss] pool-bss is not 1 for this game")],
        "any": [s("no hole fits a", "SHRINKING to"), s("SMALL JIT POOL"), s("[jit-pool] EXHAUSTED")],
        "reason": "Start-up mappings cut the JIT pool short; this build reserves the pool's range from app start, "
                  "and the lines give this game its full pool there.",
        "evidence": ["synthetic-pool-bss-cut-pool",
                     "HANDOFF: ml1135, RDR2 build 470 (604 MB pool), ml1040 launch C; "
                     "docs/research/open-fork-2026-10-18/playport.md §3 P1.2-P1.3; branch pool-reserve"],
    },
    {
        "id": "app-dll-shadow-vcruntime",
        "lines": ["env.MADEIRA_APP_DLL_SHADOW = vcruntime140.dll"],
        "priority": 10,
        "all": [s("\"MSVCP140.dll\" failed to initialize")],
        "reason": "The game ships its own x64 vcruntime140.dll, which Wine's msvcp140 cannot call; "
                  "the line makes the loader use the system's.",
        "evidence": ["crysis3remastered-2026-10-15-15-08-17", "HANDOFF 2026-10-15: Crysis 3 Remastered 547/549"],
    },
    {
        "id": "wer-dll-optin",
        "lines": ["env.MADEIRA_WER = 1"],
        "priority": 10,
        "all": [s("err:module:import_dll Library wer.dll", "not found")],
        "none": [s("MADEIRA_WER=1: wer.dll -> wer-optin.dll")],
        "reason": "The game's program imports wer.dll, which BCD does not ship for every game (Wine's has "
                  "unimplemented exports); the line gives this game Wine's wer.dll.",
        "evidence": ["hello-neighbor-2026-10-19-13-22-30 (Hello Neighbor Pre-Alpha, Steam 1092700, 0xc0000135 at once)"],
    },
    {
        "id": "wintypes-dll-optin",
        "lines": ["env.MADEIRA_WINTYPES = 1"],
        "priority": 10,
        "noRetry": True,
        "any": [s("err:module:import_dll Library api-ms-win-core-winrt-robuffer-l1-1-0.dll", "not found"),
                s("err:module:import_dll Library wintypes.dll", "not found")],
        "all": [s("err:module:import_dll Library", "not found")],
        "none": [s("MADEIRA_WINTYPES=1: wintypes.dll -> wintypes-optin.dll")],
        "reason": "The game imports wintypes.dll (Windows Runtime types), which BCD does not ship for every "
                  "game; the line gives this game Wine's wintypes.dll.",
        "evidence": ["megabonk-2026-10-20 (J7VAJV), No, I'm not a Human TZB1JX, Over the Hill HXQQN1 (c0000135 at once)"],
    },
    {
        "id": "dxva2-dll-optin",
        "lines": ["env.MADEIRA_OPTIN_DLLS = dxva2"],
        "priority": 10,
        "noRetry": True,
        "all": [s("err:module:import_dll Library dxva2.dll", "not found")],
        "none": [s("MADEIRA_OPTIN_DLLS: dxva2.dll -> dxva2-optin.dll")],
        "reason": "The game imports dxva2.dll, which BCD does not ship for every game; the line gives this "
                  "game Wine's dxva2.dll.",
        "evidence": ["fort-solis-2026-10-20 (94MSWJ), DRAGON BALL Sparking! ZERO H6XVPV (c0000135 at once)"],
    },
    {
        "id": "xpsprint-dll-optin",
        "lines": ["env.MADEIRA_OPTIN_DLLS = xpsprint"],
        "priority": 10,
        "noRetry": True,
        "all": [s("err:module:import_dll Library XPSPRINT.DLL", "not found")],
        "none": [s("MADEIRA_OPTIN_DLLS: xpsprint.dll -> xpsprint-optin.dll")],
        "reason": "The game imports XpsPrint.dll, which BCD does not ship for every game; the line gives this "
                  "game Wine's xpsprint.dll.",
        "evidence": ["animal-well NWHG8J (build 565, c0000135 at once)"],
    },
    {
        "id": "exe-window-fixed-base",
        "lines": ["exe-window-mb = 512"],
        "priority": 6,
        "noRetry": True,
        # A main program with its relocations stripped, larger than the 128 MB window at 0x140000000,
        # ran into the JIT pool (Hogwarts Legacy D67PYY, Star Wars Galactic Racer 3HWREK/XDCPK7, Metal
        # Gear Solid V 6E40B4, Stellar Blade's demo WNDRGF; 2026-10-17..20).
        "all": [s("failed to create main module", "c0000018")],
        "reason": "The game's program must load at a fixed address that BCD's JIT pool partly held; the "
                  "line keeps 512 MB there for it (the pool may be smaller).",
        "evidence": ["hogwarts-legacy-2026-10-20 (D67PYY)", "3HWREK, XDCPK7, 6E40B4, WNDRGF"],
    },
    {
        "id": "vcrt-arm64-marker",
        "lines": ["env.MADEIRA_VCRT_ARM64_KEY = 1"],
        "priority": 7,
        "noRetry": True,
        # An Unreal Engine bootstrap program (Denshattack_Proto.exe; FINAL FANTASY RESONANCE demo's FFRS.exe)
        # is an x64 exe that sees an ARM64 machine, checks for Microsoft's ARM64 Visual C++ redistributable
        # (the prefix has the x64 and x86 markers only) and shows "The following component(s) are required to
        # run this program: Microsoft Visual C++ 2015-2022 Redistributable (arm64)", exit 0x2329. The line
        # writes the volatile Runtimes\arm64 marker at session start (build 576). Whether the marker alone
        # satisfies the check is not proven: the bootstrap's registry reads are not in the logs. "none": the
        # session already printed the key's own [vcrt-arm64] line, so the line ran and did not help; no second
        # attempt follows.
        "all": [s("Redistributable (arm64)", "[win-name] #")],
        "none": [s("[vcrt-arm64] madeira-bcd:")],
        "reason": "The game's start-up program asked for Microsoft's ARM64 Visual C++ runtime, which the "
                  "Windows prefix does not list; the line adds that entry for the session.",
        "evidence": ["MKT2TW, BEB3XY (Denshattack, builds 574, 565), 4AW8BN, Q5KWDW (FINAL FANTASY RESONANCE demo, "
                     "build 573)", "ajan-vcarm64 report 2026-10-20"],
    },
    {
        "id": "d3dx9-volume-resource-stub",
        "lines": [],
        "fixedIn": 576,
        "priority": 1,
        # GTA IV (2934SV, build 574): Wine's d3dx9 had D3DXCreateVolumeTextureFromResourceExW as a stub
        # and the first call aborted the game. tools/patch-wine-d3dx9-volume-resource.py (576: build 575's
        # i386 farm came from the cache without it, see the farm's cache key).
        "all": [s("unimplemented function d3dx9_", "D3DXCreateVolumeTextureFromResourceEx")],
        "reason": "The game called a Direct3D 9 helper Wine had not implemented "
                  "(D3DXCreateVolumeTextureFromResourceEx).",
        "evidence": ["gta-iv-2026-10-19 (2934SV)"],
    },
    {
        "id": "fex-avx-on",
        "lines": ["env.MADEIRA_FEX_AVX = 1"],
        "priority": 25,
        "all": [s("[bcd] library launch", "avx=0")],
        "any": [s("[exc] ml811 code=c000001d"), s("Unhandled exception code c000001d")],
        # 0xD5 is not an x86-64 opcode whatever AVX says (FEX: "Invalid instruction in entry block"):
        # Age of Empires IV calls RelicCardinal.exe+0x3de24f0 whose first bytes are d5 dc ca a3 bc and
        # died the same with AVX on (7DXVSK, 34WSPP: [fex-avx] AVX and AVX2 reported and emulated).
        "none": [s("\" failed to initialize, aborting"), s("[guest-code] rev=ml333", "): d5 ")],
        "reason": "The game ran an instruction FEX does not decode with AVX off (c000001d); the line "
                  "turns AVX on. FEX reads it itself, so it works in BCD Dock sessions too.",
        "evidence": ["HANDOFF: Crysis 2 Remastered 540 (c000001d, DISABLEAVX expected), HFW, GoT, Rift Apart 531",
                     "not for an invalid opcode: Age of Empires IV 7DXVSK/34WSPP died the same with AVX on"],
    },
    {
        "id": "thread-stack-spill",
        "lines": ["env.MADEIRA_THREAD_STACK_SPILL = 1"],
        "priority": 20,
        "all": [s("[thr-create] FAILED at")],
        "reason": "A thread could not get its stack in the game's address range; the line lets "
                  "stacks take the kernel's placement.",
        "evidence": ["HANDOFF: Ghost of Tsushima 515, RDR2 460"],
    },
    {
        "id": "lock-orphan-stamped",
        "lines": ["env.MADEIRA_LOCK_ORPHAN = stamped"],
        "priority": 30,
        "all": [s("[lock-reap] SRW", "released exclusive")],
        "any": [s("is not owned exclusive!")] + UNHANDLED,
        "reason": "A lock held by a thread that died was released the old way and another thread "
                  "tripped over it; the line releases such locks safely.",
        "evidence": ["HANDOFF: Horizon Zero Dawn 479-480, GTA V 510"],
    },
    {
        "id": "d3d12-fl-12-1",
        "lines": ["d3d12-fl-12-1 = 1"],
        "priority": 10,
        # No "api": a game refused at device creation never opens a command queue.
        "all": [s("[madeira-d3d12] feature level 0xc100 refused")],
        "reason": "The game asks for Direct3D feature level 12_1; the line claims it.",
        "evidence": ["HANDOFF: Microsoft Flight Simulator 2024 538/540"],
    },
    {
        "id": "ec-hook-trace-off",
        "lines": ["env.MADEIRA_EC_HOOK_TRACE = 0"],
        "priority": 15,
        "all": [s("exit_code=0x80000101")],
        "any": [s("[ec-hook] #"), s("ntdll_set_exception_jmp_buf")],
        "reason": "The game hooks its own code and the hook reporter trips over it; the line turns "
                  "the reporter off.",
        "evidence": ["HANDOFF: Ghost of Tsushima 515 (the standard list's line)"],
    },
    {
        "id": "free-delay-off",
        "lines": ["env.MADEIRA_FREE_DELAY_MS = 0"],
        "priority": 40,
        "all": [s("[alloc-fail]", "status=c0000018", count=200)],
        "none": [s("[win-name] #", "not supported")],
        "maxStage": 2,
        "reason": "Address requests kept colliding with ranges held back after a free; the line "
                  "frees them at once.",
        "evidence": ["HANDOFF: Horizon Forbidden West 517"],
    },
    {
        "id": "audio-resume-after-interruption",
        "lines": ["env.MADEIRA_AUDIO_RESUME = 1"],
        "priority": 35,
        # iOS stopped the audio output (a call, Siri, an alarm, another app's sound, the app being
        # suspended) and nothing started it again. The driver's pacing line, every 10 s per stream,
        # then shows a started stream whose output made no render pass at all ("in 0 of 0
        # passes"): its ring stays full and its clock frozen, so a game timed by its audio stalls
        # (FMOD, Wwise, Media Foundation cutscenes). The line restarts the output after an
        # interruption and plays the ring out by the clock meanwhile (audio_null_ios.c).
        "all": [s("[ios_audio] ml1230 pacing", "in 0 of 0 passes")],
        "none": [s("[audio] interruption handling on")],
        "reason": "iOS stopped the game's audio (a call, Siri, an alarm, another app's sound or the app being "
                  "suspended) and it never came back, which also stalls a game timed by its audio; the line "
                  "restarts the output after an interruption and keeps the audio clock running meanwhile.",
        "evidence": ["synthetic-audio-resume-after-interruption",
                     "docs/research/ios18-2026-10-18/gameplay.md section 2.1 (every iOS version)"],
    },
    {
        "id": "gpu-work-refused-in-background",
        "lines": ["env.MADEIRA_PAUSE_IN_BACKGROUND = 1"],
        "priority": 45,
        # iOS refuses GPU work from an app in the background, and a game runs on for some seconds
        # after Home: what it submitted then was lost. DXMT: "Device error at frame N: ...
        # Insufficient Permission (to submit GPU work from background)", and a D3D9 readback may
        # report the device lost; the D3D12 runtime: "COMMAND BUFFER ENDED IN ERROR ... Insufficient
        # Permission ...". A texture streamed meanwhile can stay black. The line makes commits and
        # presents wait while the app is not active (tools/patch-winemetal-pause-inactive.py).
        "all": [s("Insufficient Permission", "from background")],
        "none": [s("[gpu-pause] Metal work waits")],
        "reason": "iOS refused GPU work the game submitted while BCD was in the background, which can leave "
                  "textures black or the device lost; the line holds the game's GPU work while BCD is not active.",
        "evidence": ["synthetic-gpu-work-refused-in-background",
                     "docs/research/ios18-2026-10-18/gameplay.md section 2.2",
                     "madeira_d3d12.c mad_cb_log_error: refused while the app was in the background"],
    },
] + SWAP_RULES

# Lines auto-tune must never add, whatever a future rule says (docs/AUTO-TUNE.md;
# the fix history's "do not try" list).
NEVER = {
    "eco": "crashed GTA V on the loading screen (449)",
    "env.MADEIRA_POOL_HEAD_RESERVE_MB": "above 128 dropped RDR2 to 2-3 FPS",
    "env.MADEIRA_AUDIO_MAX_CHANNELS": "2 gave no sound at all",
    "default-buffer-swap": "about 3x slower GPU; a last resort only",
    "fence-chain": "F0 poisons the session; other modes are per-game tuning",
    "env.MADEIRA_IR_NO_BOUNDS_CHECK": "never set",
    "pso-parallel": "never set to 0",
    "pso-lazy": "never set to 0",
    "env.MADEIRA_CHILD_OWN_NTDLL": "never set to 0",
    "env.MADEIRA_HEAL_OWNER": "never set to 0",
    "env.MADEIRA_NTDLL_SYNC": "never set to 1",
    "pool": "a bigger pool shrank further (RDR2); sized per device",
    "env.MADEIRA_SC_CEF_FLAGS": "--enable-logging crashed the whole app",
}

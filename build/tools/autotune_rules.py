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
        "id": "x64-image-nocopy",
        "lines": ["env.MADEIRA_X64_IMAGE_NOCOPY = 1"],
        "priority": 10,
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
        "id": "fex-avx-on",
        "lines": ["env.MADEIRA_FEX_AVX = 1"],
        "priority": 25,
        "all": [s("[bcd] library launch", "avx=0")],
        "any": [s("[exc] ml811 code=c000001d"), s("Unhandled exception code c000001d")],
        "none": [s("\" failed to initialize, aborting")],
        "reason": "The game ran an instruction FEX does not decode with AVX off (c000001d); the line "
                  "turns AVX on. FEX reads it itself, so it works in BCD Dock sessions too.",
        "evidence": ["HANDOFF: Crysis 2 Remastered 540 (c000001d, DISABLEAVX expected), HFW, GoT, Rift Apart 531"],
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

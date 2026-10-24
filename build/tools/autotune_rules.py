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
  lines     "key = value" each, added together by one attempt ("{exeWindowMB}" in a value: the window the main program needs, see
            AutoTuneEngine.resolve)
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
  imageMaxMB   the largest image the JIT pool refused ("[jit-pool] EXHAUSTED (image 0x..+0xSIZE)") was at most this many MiB
               (a log with no such line passes)
  exeMaxMB     the main program (relocations stripped, at 0x140000000: "ml985: map_image_view size=0x...") was at most this
               many MiB (a log with no such line passes)
  exeMinMB     the same program was at least this many MiB (the log has to have such a line)
  memoryMinPct the last footprint line was at least this percent of the memory limit (a log with no footprint line or limit passes)
  listUpgrade  after a `killed` verdict the lines may replace the recommended list's own lines in the game's file, with a larger number
               (a line equal to the list's is already in force; a user's key is never replaced)
  minStage / maxStage   how far the session got: 1 Wine started, 2 the game's
            modules loaded, 3 first frame, 4 presenting for 20 s, 5 played on
  noRetry   the failure comes back the same on every start: when such a rule matches,
            the unchanged retry is skipped and its line is tried at once
  upgrade   may replace a line auto-tune kept earlier with the same key (never one
            the user sets); without it a rule only adds keys not yet in the layer
  reason    one sentence the app shows with the attempt
  evidence  where the rule comes from (fixtures, HANDOFF, OYUN-AYARLARI)
  noFix     a failure no setting changes and no build is known to fix (Valve's client refusing the game, an open bug of BCD's): like fixedIn,
            no lines, and auto-tune stops at once ("known") without promising a build; `reason` says what it is
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
        # Round 591: a kill whose last footprint was far below the limit is not iOS closing the game for memory (Albino Lullaby 41D565: 2 GB
        # of 8 GB, a crash in mf.dll; the app died, the tester said "closed by itself", and the 12 GB swap file was tried). memoryMinPct 50
        # leaves the real kills alone (they end within a few percent of the limit). listUpgrade: GTA V Enhanced's list has swap-mb = 3072 in
        # the game's file, which kept the 12 GB tier's 6144 out as a "user key" (A24ES3); after this verdict the larger number may replace it.
        out.append(dict(id="swap-%s-memory-kill" % name, lines=base, priority=50, memoryMinPct=50, listUpgrade=True,
                        all=[s("[footprint] rev=ml358 phys=")], outcomes=["killed"],
                        reason="iOS closed the game at its memory limit; a %d MB swap file moves large "
                               "allocations out of RAM on this device." % first,
                        evidence=[ev], **ram))
        out.append(dict(id="swap-%s-memory-dialog" % name, lines=base, priority=50,
                        all=[s("[footprint] rev=ml358 phys=")], any=MEMORY_DIALOG, outcomes=["crashed", "exited-early"],
                        reason="The game said it ran out of memory; a %d MB swap file moves large allocations "
                               "out of RAM on this device." % first,
                        evidence=[ev], **ram))
        out.append(dict(id="swap-%s-more" % name, lines=["swap-mb = %d" % second], priority=55, upgrade=True, memoryMinPct=50,
                        all=[s("[swap] ml1077 backed")], outcomes=["killed"],
                        reason="The game was closed for memory again with the swap file in use; the line "
                               "raises it to %d MB." % second,
                        evidence=[ev], **ram))
    return out


SWAP_RULES = swap_rules()

# The heavy games whose GPU recovery gets gpu-reset-lower-graphics-settings (owner, 2026-10-23), as their programs are named on disk.
HEAVY_GAME_PROGRAMS = [
    "GTA5.exe", "GTA5_Enhanced.exe", "PlayGTAV.exe", "RDR2.exe", "PlayRDR2.exe",
    "GhostOfTsushima.exe", "GoW.exe", "GoWR.exe", "HorizonZeroDawn.exe", "HorizonZeroDawnRemastered.exe", "HorizonForbiddenWest.exe",
    "Spider-Man.exe", "MilesMorales.exe", "Spider-Man2.exe", "RiftApart.exe", "tlou-i.exe",
    "HogwartsLegacy.exe", "FlightSimulator2024.exe", "Cyberpunk2077.exe",
]
# ... and their library titles ("[bcd] library launch <title>:"), for logs that name only the title (older builds, Dock sessions).
HEAVY_GAME_TITLES = [
    "Grand Theft Auto V", "Grand Theft Auto V Legacy", "Grand Theft Auto V Enhanced", "Red Dead Redemption 2",
    "Ghost of Tsushima", "Ghost of Tsushima DIRECTOR'S CUT", "God of War", "God of War Ragnarök", "Horizon Zero Dawn",
    "Horizon Zero Dawn Remastered", "Horizon Forbidden West", "Horizon Forbidden West Complete Edition",
    "Marvel's Spider-Man Remastered", "Marvel's Spider-Man: Miles Morales", "Marvel's Spider-Man 2",
    "Ratchet & Clank: Rift Apart", "The Last of Us Part I", "Hogwarts Legacy", "Microsoft Flight Simulator 2024", "Cyberpunk 2077",
]

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
        "id": "child-ntdll-copy-lost-exec",
        "lines": [],
        "fixedIn": 577,
        "priority": 1,
        # GTA Vice City (Steam 12110) AE6NQZ build 576 and S0TH0N build 574, iPhone16,2, 2026-10-21, both at the same pool
        # offsets: the game's second process (steam.exe -applaunch) got a private copy of ntdll on JIT-pool pages that had
        # lost EXEC for good ("[pool-poison] HANDED OUT NON-EXEC ... 70 poisoned pages", "[copy-verify] ntdll.dll: 29 of 29
        # .text pages BORN NON-EXEC"); its first instruction faulted ([exec-recover] mprotect_rx=-1 errno=13) until the
        # redelivery guard ended the process. Cause (576's [pool-section-overrun]): an unnamed 32-bit image of the game has
        # VirtualSize = rva + 0x10000000 in every section, and mprotect_exec's mprotect(RW) of its writable .data covered 256 MB
        # of pool after the copy, which lowers max_prot for good. build/ntdll-unix/virtual_ios.c (build 577) gives up a
        # per-child ntdll copy that is handed such a range and takes the next one that can execute
        # (tests/host/check-pool-dead-range.py); env.MADEIRA_POOL_CLAMP_SECTIONS = 1 stops the dead run from forming (off by
        # default). Different from tail-carve-lost-exec: there the dead page lay under FEX's code buffer.
        "all": [s("[pool-poison] HANDED OUT NON-EXEC"),
                s("[copy-verify]", ".text pages BORN NON-EXEC"),
                s("[exec-recover] pg=", "mprotect_rx=-1", "pool-rx"),
                s("[redeliv] terminating process")],
        "reason": "The private copy of a system library that a helper process needs was placed on JIT-pool memory that "
                  "had lost the right to run code; from this build on BCD takes the next range that can.",
        "evidence": ["vice-city-2026-10-21 (AE6NQZ, build 576)", "S0TH0N (build 574, same offsets)",
                     "tests/host/check-pool-dead-range.py (build 577)"],
    },
    {
        "id": "small-map-window-no-place",
        "lines": [],
        "fixedIn": 577,
        "priority": 1,
        # Every 32-bit program on a device with a 63 GB address map (iPhone13,4 iOS 18.6.2, builds 576; extended-va=0): the
        # program's own image was refused at its preferred base inside the guest window (ml985 ... c0000017), and a
        # 0x11000-byte section map then found no place in the 4 GB window although 2 GB of it was free
        # ("[laa] ml1070 SPILL ... no room below guest 2 GB", "[va-scan] FAILED window=0x400110000..0x500000000 ... errno=17"):
        # STATUS_NO_MEMORY (c0000017), ucrtbase's init failed, c0000142. The errno=17 is only the fallback probe meeting our own
        # PROT_NONE reservation; the placement is map_reserved_area's MAP_FIXED over it, which returned NULL in silence (its
        # search found nothing, or the MAP_FIXED failed: the log cannot tell which). build/ntdll-unix/virtual_ios.c (build 577),
        # small address maps only: after an ENOMEM from that MAP_FIXED the next free range is tried
        # (tests/host/check-reserved-area-retry.py), and the failure path says why ([reserved-area], [va-scan] window room,
        # STAGE= on the [map-sec] line), which settles it if this still shows. No config line changes it.
        "all": [s("[wow-window] small address map"),
                s("[laa] ml1070 SPILL"),
                s("[map-sec] virtual_map_section", "-> 0xc0000017"),
                s("failed to initialize, aborting")],
        "reason": "On this device's smaller address map a 32-bit program's memory request found no place in its 4 GB window "
                  "although space was free; from this build on BCD tries the next free range when the system refuses one.",
        "evidence": ["small-map-window-2026-10-21 (Shade 39Z345, C534F0, Z5F6MG, build 576)",
                     "tests/host/check-reserved-area-retry.py (build 577)"],
    },
    {
        "id": "small-map-window-refusals",
        "lines": [],
        "fixedIn": 579,
        "priority": 1,
        # Shade: Wrath of Angels on build 578 (6NE7KH, iPhone13,4, iOS 18.6.2, 63 GB address map, 32-bit program): the 576 failure above
        # is fixed (the retry placed the loader-time requests), but every MAP_FIXED over the 4 GB window's lowest free ranges is still
        # refused with ENOMEM (9 consecutive refusals at 0x401310000, the kernel's region there reading "prot=0/0"), and the game's
        # 8 MB Direct3D 9 buffer (0x810000 at 0x40f8c0000) found all 32 retries, 64 KB apart, refused: [alloc-fail] ... STAGE=fixed-map, the
        # same address 13 times, "MTLDevice_newBuffer ... refusing", "[thr-create] FAILED at WoW64 32-bit stack", exit 3. thread-stack-spill
        # was proposed for it and was the wrong line (the stack is refused by the same MAP_FIXED, inside the window). build/ntdll-unix/
        # virtual_ios.c (build 579): a placement refused on every candidate, on a small address map only, is tried once more by unmapping
        # the first refused range (Wine's tables call it free) and mapping it without overwriting (env.MADEIRA_RESERVED_REMAP = 0 turns it
        # off), and [reserved-area] names every refused candidate, the kernel's regions across the refused range and what the last candidate
        # held before it was mapped (tests/host/check-reserved-area-retry.py). NOT PROVEN that the remap places it: if a 579 log still shows
        # this, the new [reserved-area] "detail" and "last resort" lines say why. "none": the free-range table had no range that fits (a
        # different failure). 576 logs have no [reserved-area] line and keep small-map-window-no-place.
        "all": [s("[wow-window] small address map"), s("[reserved-area] #", "small address map retry on"),
                s("[alloc-fail]", "STAGE=fixed-map"), s("[va-scan] window room", "range(s) that fit")],
        "none": [s("[va-scan] window room", "holds 0 range(s) that fit")],
        "reason": "On this device's smaller address map the system kept refusing BCD's memory placements inside a 32-bit program's "
                  "window and a larger request found none; from this build on BCD makes one more attempt when every candidate was refused.",
        "evidence": ["shade-2026-10-21 (6NE7KH, build 578)", "tests/host/check-reserved-area-retry.py (build 579)"],
    },
    {
        "id": "dotnet-setup-never-finishes",
        "lines": [],
        "fixedIn": 578,
        "priority": 1,
        "session": "dock",
        # Batman: Arkham City GOTY (Steam 200260), builds 574 (PTKASZ: "dotnet=unfinished", auto-tune "hung at stage 2") and 576
        # (log-83643051, iPad16,5, 2026-10-21): the game's one-time install ran its .NET Framework 3.5 setup (DotNetFx35.exe, then
        # dotNetFx35setup.exe, then the extracted setup.exe /web /q) and it never ended: the extractor's windows stayed hidden, the CPU
        # was idle from t+15 s, and the setup's main thread waited (wait-all) on two threads that had already exited. The batch never
        # reached its next program, so the session stayed at the starting screen. Star Wars: The Clone Wars Republic Heroes' setup
        # of the same kind exited 5100 (builds 574). build 578 (DockInstallers.swift, MADEIRA_DOCK_DOTNET_MONO): with Wine Mono the
        # program is not run (provided by Wine Mono, its record written); without it the program waits for Settings > .NET Framework.
        # A .NET Framework setup is named by Microsoft's file name; the plan line shows it as pending only in builds before 578.
        "all": [s("[dock-installers] start")],
        "any": [s("[dock-installers] app=", "status=pending", "file=DotNetFx"),
                s("[dock-installers] app=", "status=pending", "file=dotNetFx"),
                s("[dock-installers] app=", "status=pending", "file=dotnetfx"),
                s("[dock-installers] app=", "status=pending", "file=NDP"),
                s("[dock-installers] app=", "status=pending", "file=ndp"),
                s("[dock-installers] app=", "status=pending", "file=NetFx"),
                s("[dock-installers] app=", "status=pending", "file=netfx")],
        "none": [s("[dock-installers] end succeeded")],
        "reason": "The game's one-time install of Microsoft's .NET Framework never finished under Wine and kept the start from going "
                  "on; from this build on BCD does not run it (Wine Mono provides .NET) and starts the game.",
        "evidence": ["arkham-city-dotnet-2026-10-21 (log-83643051, build 576)", "PTKASZ (build 574, same game)",
                     "tests/host/check-dock-installers.py (build 578)"],
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
        "id": "wineserver-cancel-async-freed",
        "lines": [],
        "fixedIn": 577,
        "priority": 1,
        # BeamNG.drive (VV1FQ4, SRWY27, DZ6BN4 on build 574; SYDJDY on 576), 2026-10-20/21: the launcher's
        # I/O cancellation (the cancel_async request) died inside the app's own wineserver, which ended the
        # WHOLE app: [mach_exc] UNHANDLED pc=...Madeira`req_cancel_async+0x158 addr=0x8, called from
        # call_req_handler <- thread_poll_event <- ios_doorbell_poll_thread. cancel_process_async called
        # cancel_async( async ) and then wrote into that async; on iOS the APC for a busy thread cannot be queued
        # (send_thread_signal always fails: "[apc-requeue] ... dropped, no waiting thread"), so the async
        # completed and was freed inside the call, and list_remove( &async->process_entry ) stored through a NULL
        # link. tools/patch-wineserver-async-cancel.py (build 577) holds the async across the call
        # (tests/host/check-wineserver-async-cancel.py runs the real async.c). No config line changes it. The
        # signal is the crash handler's own line, printed whatever the diagnostic switches.
        "all": [s("[mach_exc] UNHANDLED")],
        "any": [s("[mach_exc] sym pc=", "req_cancel_async"), s("NATIVE-THREAD DECLINE", "req_cancel_async")],
        "reason": "BCD's own Windows server released an I/O request while it was still cancelling it, and iOS "
                  "closed the app; from this build on it keeps the request until it is done.",
        "evidence": ["beamng-drive-2026-10-21 (SYDJDY, build 576)", "BeamNG VV1FQ4, SRWY27, DZ6BN4 (build 574)",
                     "tests/host/check-wineserver-async-cancel.py (build 577)"],
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
        "id": "ceg-service-not-installed",
        "lines": [],
        "fixedIn": 578,
        "priority": 1,
        "session": "dock",
        # Steam games whose executable Valve builds per user (CEG: Just Cause 2 JYFG4D on build 574, Call of Duty: Black Ops on
        # build 576 / iPad16,10, the log the tester sent a minute before codbo-cur): the Dock host started Wine's services.exe,
        # found "Steam Client Service" missing and ran Valve's own bin\SteamService.exe /install, whose first RPC to the service
        # manager failed ("RpcAssoc_BindConnection receive failed with error 1726", Valve's "Failed to open service control
        # manager", exit 1), so the start ended with ceg-result -5 ("its Windows service isn't installed"). The 64-bit host's own
        # calls to the same service manager worked, the 32-bit installer's failed in 3 of 5 logged sessions and worked in 2.
        # build 578: ContentView starts such a game with the service already installed (DockCEGService in
        # app/Madeira/DockInstallers.swift: Valve's own SteamService.exe copied to Common Files\Steam, the service's key written
        # into system.reg with the values the installer's CreateService call passes; services.exe loads it, the host finds it
        # registered and runs no installer; tests/host/check-dock-ceg-service.py). No config line changes it; the switch is
        # env.MADEIRA_DOCK_CEG_SERVICE = 0 (off). This is the -5 only: the sessions that got the service registered still ended
        # with ceg-result 55 (88C3MN, REDAH7, codbo-cur), which this rule does not match and which is not fixed here.
        # The signal is the Dock report's own line for the result, printed whatever the diagnostic switches.
        "all": [s("[dock-report] ceg-result=-5")],
        "reason": "Valve's client service, which Steam needs to prepare this game's executable, was not installed in BCD's "
                  "Windows setup, and Valve's installer for it failed inside the session; from this build on BCD installs it "
                  "before the game starts.",
        "evidence": ["ceg-service-not-installed-2026-10-21 (Call of Duty: Black Ops codbo-prev, build 576)",
                     "Just Cause 2 JYFG4D (build 574): same RPC 1726, exit 1",
                     "tests/host/check-dock-ceg-service.py (build 578)"],
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
        #
        # Round 580 (DARK SOULS III, A3V7SV, build 579, iPad17,3): the same fault on a page of the image ("[store-noalias] #1
        # addr=0x140010122 ... prot=1 max=3", L10891; "[wr-strip-declined] ... SEC_IMAGE WRITECOPY", 12 times, L11024 on), 200+ unhandled
        # faults and an access violation at 28 s, but the log ended before the redelivery guard's "256 identical redeliveries" line, so the
        # rule never matched and auto-tune said "stuck". The guard's line is one way to say that the store keeps faulting; the other is the
        # Mach handler's own count of unhandled faults reaching 200 ("[mach_exc] UNHANDLED #200", printed once, when the 200th is delivered;
        # DARK SOULS III's #200 is at L11934, its sister logs DCWTGJ and R138Z4 (build 574 and 565) reach #1900). A few such faults are not
        # enough: of the 710 logs of 2026-10-17..22, 18 more than before show an exec-downgraded image page and the declined strip, and
        # four of them are Desktop (explorer.exe) sessions of build 572 that ended early with five unhandled faults (05RNCK, 3MJNAM,
        # 95M2Y7, MYCJ9K): the strip is recovered noise there. The declined strip alone (three SEC_IMAGE lines, which DARK SOULS III has 12
        # of and so do the Rockstar launcher sessions) does not tell them apart either.
        "all": [s("[store-noalias]", "prot=1 max=3"),
                s("[wr-strip-declined]", "SEC_IMAGE")],
        "any": [s("[redeliv]", "identical redeliveries"),
                s("[mach_exc] UNHANDLED #200")],
        "none": [s("[x64-image]", "mapped without a pool copy")],
        "reason": "The game rewrites its own code and the emulator's write permission never reached the "
                  "program's memory, so one store faulted until BCD ended the game; the line lets the "
                  "program's image take writes the way the emulator's trap expects.",
        "evidence": ["mad-max-2026-10-20 (VZKCF3, KK9H24, 6DB55D)",
                     "dark-souls-iii-2026-10-21 (A3V7SV, build 579: the same storm, no redelivery line yet)",
                     "virtual_ios.c ml1030 / ios_x64_image_nocopy_enabled (the same symptom for 32-bit images)",
                     "Dark Souls II, one iPhone, 2026-10-19: with the line (DN5QSB, E6G3RQ) 379 s of play after the same "
                     "256-delivery warning; without it (72R0CG, P9C7DG) the session ended 34 s in"],
    },
    {
        "id": "x64-image-rwx-protector-nocopy",
        "lines": ["env.MADEIRA_X64_IMAGE_NOCOPY = 1"],
        "priority": 14,
        "noRetry": True,
        "outcomes": ["crashed"],
        # Monster Hunter Stories 3 Trial (Steam 3470630, the owner's iPad16,5, build 576, MHS3-prev) and Monster Hunter Rise
        # (MKPN2N, build 574): both 64-bit programs carry the same protector (PE export name runtime_il2cpp.exe), an unnamed
        # RWX section whose entry stub runs `call $+5; pop rbp; sub rbp,<entry>` and then a byte-wise xor loop over the code
        # that follows it. The program stops within its first 0x80 stub bytes with c0000005 ("[guest-rip-sec] ... section #N ''
        # ... (RWX)", then an unhandled exception). MHS3: the pop returned the slot above the pushed return address, so the
        # loop's first load went to 0x1fd1. Rise: the pop was right, and the block after the loop was compiled from the bytes
        # before the loop ran (guest rip 0x13575a510 reads plaintext now, the faulting host load is [rdi+0x764afffd]) with no SMC
        # event in the log. Both programs ran with a JIT-pool copy ("[guest-code] ... pool_rip=...): <bytes>" is printed only
        # when a copy exists; no "[x64-image]" line). The line gives a pure x64 image the plain protection path, the one the
        # emulator's SMC trap/untrap expects, as for GTA V Enhanced, RDR2, HFW and RockstarService.exe (docs/madeira-bcd-421-
        # sonrasi.md 7e) and as rule x64-image-smc-nocopy does for Mad Max (Dark Souls II: 379 s of play with it, 34 s without).
        # NOT PROVEN on a run of these two games: an attempt. "[guest-rip-sec]" is an ERR() line, so a game run with quiet-log
        # gets no match here. "none": the image already runs without a copy.
        "all": [s("[guest-rip-sec]", "'' rva", "(RWX)"),
                s("[guest-code] rev=ml333 pool_rip=", "): ")],
        "any": UNHANDLED,
        "none": [s("[x64-image]", "mapped without a pool copy")],
        "reason": "The game's protector rewrites its own code at start and the emulator did not follow the "
                  "rewrite; the line runs the program from its loaded image, where the emulator's trap for "
                  "rewritten code works.",
        "evidence": ["monster-hunter-stories-3-trial-2026-10-21 (owner, build 576)",
                     "MKPN2N (Monster Hunter Rise, build 574)",
                     "virtual_ios.c ios_x64_image_is_host_data; docs/madeira-bcd-421-sonrasi.md 7e"],
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
        # Round 591 (Pro Evolution Soccer 2013, DAN35T): the same loop without the [store-noalias] line: FEX "handled" the same
        # self-modifying-code fault 2,000 times ("Handled self-modifying code: pc: 16B038E5C fault: 710455C01C") and the redelivery guard
        # ended the process. So the loop is named either by that store line or by 256 or more handled-SMC lines; "identical redeliveries"
        # is in all (both [redeliv] lines that carry it were the old "any").
        "all": [s("[wow-rwx] ml1279 MADEIRA_WOW_RWX_PLAIN=(unset)"),
                s("[redeliv]", "identical redeliveries")],
        "any": [s("[store-noalias]", "prot=1 max=3", "exec-downgraded page that never got dual-mapped"),
                s("Handled self-modifying code: pc:", count=256)],
        "reason": "The game rewrites its own code and BCD left that memory read-only after the "
                  "emulator released it, so one store faulted forever; the line makes the game's "
                  "executable memory plain read/write, as on Windows.",
        "evidence": ["warcraft-2-bne-2026-10-16-23-27-45",
                     "HANDOFF 2026-10-17: Warcraft II BNE, build 559 (tester, iPad16,5)",
                     "pro-evolution-soccer-2013-smc-loop-2026-10-22 (DAN35T, build 580: no [store-noalias] line)"],
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
        "id": "x87-int64-roundtrip",
        "lines": ["env.FEX_X87REDUCEDPRECISION = 0"],
        "priority": 16,
        "noRetry": True,
        "outcomes": ["crashed"],
        # Batman: Arkham City GOTY (Steam 200260: arkham-city-skip, build 576, a tester's iPad16,5; PTKASZ, build 574) and Injustice:
        # Gods Among Us (Steam 242700: G5HGVV, ND8YNB, build 574), all Unreal Engine 3: the first unhandled fault is the same
        # 4-byte load (`ldapr w4,[x24]`, the emulator's `mov eax,[edi]`) at a 64 KB aligned address that no view holds (or that a freed
        # view held), right after the function that reads an 8-byte lock-free list head with FILD qword / FISTP qword. The host code
        # of that function reads `scvtf d2,x20 / frinti.2d v2,v2 / fcvtzs.2d v2,v2` (9e620282 6ee19842 4ee1b842) in all four logs: BCD
        # runs 32-bit games with FEX's X87ReducedPrecision on by default (FEX WOW64 Module.cpp, ml950), where the integer goes through a
        # double and a value of 2^61 and more (the sequence counter sits in the head's high half) loses its low 9 bits, so a node
        # pointer that is not a multiple of 512 comes back rounded to one. In the Batman log x13 = 0xf07ff00 is the exact tie that
        # rounds to the fault address 0xf080000. NOT PROVEN: the logs cannot say whether the node was ever at the fault address;
        # build 578's [x87-int64] line compares the head's memory with the value the guest got and says LOST or EXACT
        # (tests/host/check-x87-int64-diag.py). An attempt: exact x87 math for this game (slower), kept only if the game then does better.
        # "none": the head held the value as the guest read it, so the loss is not the cause.
        "all": [s("[fault-host]", "9e620282 6ee19842 4ee1b842")],
        "none": [s("[x87-int64]", "verdict: EXACT")],
        "reason": "The game reads an 8-byte list head through the old x87 unit and BCD's fast x87 mode rounds it to 53 bits, which can "
                  "send the next load to memory that is not there; the line turns on exact x87 math for this game (slower).",
        "evidence": ["arkham-city-skip (a tester's iPad16,5, build 576), PTKASZ (build 574)",
                     "G5HGVV, ND8YNB (Injustice, build 574)",
                     "FEX WOW64 Module.cpp ml950 (X87ReducedPrecision on for 32-bit guests)",
                     "tests/host/check-x87-int64-diag.py (build 578)"],
    },
    {
        "id": "vdcfg-virtual-path",
        "lines": ["env.MADEIRA_VDCFG_PATH = 1"],
        "priority": 20,
        "noRetry": True,
        "outcomes": ["crashed"],
        "session": "dock",
        # Trails in the Sky 1st Chapter (Steam 3375780; 24VADD build 578, X56ZXZ and YTMNEK build 573, one iPhone18,1): about 2 s after
        # the game's process starts, a NULL read at sora_1st.exe+0x5215E0 (`mov rdx,[rdx+0x10]`, RDX = 0: a swapchain / render-target
        # wrapper that was never made), registers in sora_deploy_steam.exe. Right before it, GetDisplayConfigBufferSizes says "1 paths
        # 2 modes" for BCD's virtual monitor and QueryDisplayConfig answers "success, 0 paths 0 modes" (the virtual monitor has no
        # source, sysparams_ios.c skipped it: two answers of one driver that disagree), twice; then the game's SetWindowPos is
        # {32767,32767}-{32768,32768}: one pixel at the clamp, which is what a bounding box seeded with INT_MAX / INT_MIN gives when
        # no path was seen (INT_MIN - INT_MAX wraps to 1). Before the two calls the same window was a sane 1928x1114, and
        # GetMonitorInfo (1408x648) works. NOT PROVEN that the game reads those paths: an attempt. The line lists the virtual
        # monitor's path (one source mode and one target mode of the current desktop size) and names its source and target
        # (tests/host/check-vdcfg-path.py); kept only if the game then does better. Off (default) nothing changes for any game.
        "all": [s("[vdcfg] QueryDisplayConfig", "-> 0, 0 paths 0 modes"), s("[av-args]", "NULL-class fault addr=0x10"),
                s("[av-args]", "sora_deploy_steam.exe+")],
        "none": [s("[vdcfg] QueryDisplayConfig", "-> 0, 1 paths 2 modes")],
        "reason": "The game builds its fullscreen window from the display paths and BCD answered with none, so it made a one-pixel "
                  "window and then used a screen it never created; the line lists the virtual monitor's path as Windows does.",
        "evidence": ["trails-in-the-sky-1st-chapter-2026-10-21 (24VADD, build 578)", "X56ZXZ, YTMNEK (build 573)",
                     "tests/host/check-vdcfg-path.py (build 579)"],
    },
    {
        "id": "mono-child-tls-null",
        "lines": ["env.MADEIRA_TLS_CLEAR_OWN_PROCESS = 1"],
        "priority": 17,
        "noRetry": True,
        "outcomes": ["crashed"],
        # Batman: Arkham City GOTY (Steam 200260: C7DGSN, build 578, a tester's iPad16,5, auto-tune's FEX_X87REDUCEDPRECISION = 0
        # trial on): BatmanAC.exe gets as far as starting BmLauncher.exe, a .NET Framework program run by Wine Mono, and the
        # launcher's main thread dies in libmono-2.0-x86.dll (guest EIP 0x778635C9, +0x2135C9) on `mov dword [ebx+0x10c], 1` with
        # EBX = 0, right after corlib loaded: Mono's inlined TlsGetValue (TEB32->TlsSlots[key]) returned NULL for its SGen thread
        # info, which the same thread had set a moment before. Meanwhile the parent was shutting down (eight worker threads exiting).
        # virtual_clear_tls_index, the unix side of TlsFree, clears the freed cell in EVERY thread of EVERY pseudo-process (teb_list
        # holds them all); on Windows another process is never touched. NOT PROVEN: no TlsFree is logged in C7DGSN. Build 579's
        # [tls-cross-clear] line (a live cell cleared in another process, with the index) and [wow-null32] (the thread's TLS cells,
        # the stack and the return address) are the proof or the refutation. An attempt: only the calling program's threads are
        # cleared, kept only if the game then does better. The three signals are the 32-bit Wine Mono, a second 32-bit window
        # (a program a program started) and a NULL-page fault (8 hex digits = a 32-bit guest address below 64 KB).
        "all": [s("[mono-cfg]", "libmono-2.0-x86.dll", "HOOKS ARMED"), s("[wow-window] reserved B=", "(slot 1)"),
                s("wine: Unhandled page fault on", "access to 0000")],
        "reason": "A program the game started (a .NET launcher) died on an empty thread slot while the game was shutting down; "
                  "the line stops one program's thread-slot cleanup from reaching into other programs' threads, the suspected cause.",
        "evidence": ["C7DGSN (Batman: Arkham City GOTY, build 578, a tester's iPad16,5)",
                     "build/ntdll-unix/virtual_ios.c virtual_clear_tls_index (teb_list is every pseudo-process's threads)",
                     "tests/host/check-tls-clear-process.py, tests/host/check-wow-null-diag.py (build 579)"],
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
        "id": "dotnet-gc-region-range",
        "lines": ["env.DOTNET_GCRegionRange = 400000000"],
        "priority": 10,
        "noRetry": True,
        # Unrailed! 2: Back on Track (Steam 2211170, CR8H6H, build 576, iPhone18,2; a Godot game with a .NET 7+ bootstrap,
        # Bootstrap.exe): "GC heap initialization failed with error 0x8007000E" (E_OUTOFMEMORY), "Failed to create CoreCLR".
        # .NET 7+ reserves, at start, a range of virtual memory for its regions-based heap: 256 GB by default (twice the
        # physical memory if more), capped at half the process's virtual memory limit. The log's request is 0x3fd2d28000
        # (261421 MB), and BCD's map has no such gap: "[mmap-place] ml1027 NO GAP FITS size=0x3fd2d38000 ... largest gap
        # 32768 MB", "[jumbo] kernel-pick reserve failed (0xc0000017)". Microsoft documents the knob for exactly this
        # (learn.microsoft.com, .NET runtime configuration for the GC, "Region range"): DOTNET_GCRegionRange, a hexadecimal
        # size in bytes; "if you do get E_OUTOFMEMORY during the runtime initialization ... due to reserving this range" it
        # recommends two to five times the committed heap, or twice the physical memory available to the process when that
        # is unknown. BCD reports at most 8 GB ("ml992: clamping reported physical memory 11722 MB -> 8192 MB"), so 16 GB =
        # 0x400000000, which fits the 32 GB gap. A range is reserved, never committed: no memory is used by the number
        # itself. DOTNET_GCHeapHardLimit would also size the range, but it changes the GC's behaviour (a commit limit). The
        # next stop of such a game may be the CLR's write-barrier assert (rule dotnet-wx-off). "none": the session already
        # ran with the line ([madeira-env] game ... is auto-tune's own line for a kept or tried key) and failed again.
        #
        # Round 580: not on the 454 GB address map of an install without the memory entitlements ("[device] address-map=[...) 454GB
        # increased-memory=0"). Fortune Mill (Godot 4 .NET, NMDFT1, build 579, iPhone17,1): with the line in force the runtime asked for
        # 0x400010000 (16 GB) and "[mmap-place] ml1027 NO GAP FITS ... largest gap 513 MB ... the map really is full for this size"
        # (L14207), "GC heap initialization failed" (L14273): that map has no gap of any size the runtime would take, so the attempt
        # cannot help (GetMoreRam, the memory entitlements, is the remedy). A 512 GB map (Unrailed! 2) had 32 GB.
        "all": [s("GC heap initialization failed with error 0x8007000E", "ReportEventW")],
        "none": [s("[madeira-env] game DOTNET_GCRegionRange="), s("[device] address-map=", " 454GB", "increased-memory=0")],
        "reason": "The game's .NET runtime tried to reserve 256 GB of address space for its heap and BCD's address map has "
                  "no such gap; the line gives the runtime a 16 GB range.",
        "evidence": ["unrailed-2-2026-10-21 (CR8H6H, build 576)",
                     "learn.microsoft.com/dotnet/core/runtime-config/garbage-collector (Region range)",
                     "fortune-mill-2026-10-22 (NMDFT1: the 454 GB map of an install without memory rights has no such gap; no match)"],
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
        "id": "mip0-texture-view-beyond-levels",
        "lines": ["mip0-full = 1"],
        "priority": 12,
        "noRetry": True,
        "api": "d3d12",
        # Marvel's Spider-Man: Miles Morales (Steam 1817190), one tester's iPad16,5: build 574 (PD7S1E, L88752-88762)
        # and build 576 (2026-10-21 10:37, L83617-83627). About 55 s in, at the first 3D scene, the game makes a
        # 256x256 RGBA32F render-target + UAV texture with MipLevels 0 (the full 9-level chain in D3D12). This runtime
        # makes ONE level and GetDesc answers 0 unless mip0-full = 1 ("[mip0] ... GetDesc answers 0 (bcd.cfg mip0-full
        # = 1 resolves it)"). The game then makes a UAV for level 1; Metal's own validation refuses the view
        # ("(newLevelRange.location + newLevelRange.length)(2) must be <= [texture mipmapLevelCount](1)") and ends the
        # process, which BCD reports as exception 80000101. Rift Apart and Horizon Forbidden West carry the line (HFW
        # since build 533). A session that already has it prints its [mip0] lines without the "GetDesc answers 0"
        # suffix, so it never matches. Not proven on a Miles Morales run.
        "all": [s("[mip0]", "GetDesc answers 0"),
                s("_mtlValidateArgumentsForTextureViewOnDevice", "failed assertion"),
                s("newLevelRange.location", "mipmapLevelCount")],
        "reason": "The game made a texture with the full chain of mip levels and used a level this build did not "
                  "make, and the graphics driver ended the game; the line gives such textures all their levels.",
        "evidence": ["miles-morales-2026-10-21 (iPad16,5, build 576)", "PD7S1E (2026-10-19, iPad16,5, build 574): the same assertion",
                     "docs/arsiv/HFW-DURUM.md (mip0-full, build 533)"],
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
        # imageMaxMB (build 579): not for an image of more than 300 MiB. A copy must fit one region of the pool, the
        # earlier copies (Wine's own DLLs: 107-187 MiB in the logs below) sit at the start of the first region, and a
        # split pool's regions are 249-520 MB (14 pairs in the logs, the second never above 328 MB) and never larger than
        # the single pool the same start-up would have built. Stellar Blade Demo's 314 MiB exe (YBX2WT, AVFMGC: regions
        # of 432 and 256 MB; the pool then still had 245 and 256 MB free) and FINAL FANTASY RESONANCE DEMO's 474 MiB exe
        # (EJDV7Z: 288 and 272 MB) got pool-split = 1 and ended with the same "[jit-pool] EXHAUSTED (image" line; the
        # attempt only cost a start. Without the line the engine says "stuck" once x64-image-nocopy is tried or set.
        # Spelunky 2's 558 MiB exe (J2YJF8) is over the bar as well; BeamNG.drive's 265 MiB DLL (PEVMEQ) and every
        # smaller image keep this rule.
        "imageMaxMB": 300,
        "all": [s("[jit-pool] EXHAUSTED")],
        "reason": "The JIT pool was smaller than usual and filled up while the game loaded; a split "
                  "pool is about 880 MB.",
        "evidence": ["counter-strike-2-owner-2026-10-20 (pool-split by hand started it)",
                     "albion-launcher BYBFCH/PGKTDD, spelunky-2 J2YJF8 (2026-10-20)",
                     "stellar-blade-demo-2026-10-21 (YBX2WT), ffrs-demo-2026-10-21 (EJDV7Z): the guard"],
    },
    {
        "id": "dock-game-scm",
        "lines": ["env.MADEIRA_DOCK_GAME_SCM = 1"],
        "priority": 12,
        "noRetry": True,
        "session": "dock",
        # A Steam game whose launcher is Rockstar's (GTA San Andreas - The Definitive Edition MM0DAY, iPhone18,1 build 576;
        # Grand Theft Auto IV: The Complete Edition 4GAWSA, iPad13,6 build 576): PlayGTA*.exe starts Launcher.exe, which starts
        # RockstarService.exe four times (each exits 1) and logs "[svc] Unable to open SC manager! (1722)" (RPC: no service
        # manager). A Dock game session runs Wine's services.exe only for the install batch and for CEG; no services.exe is
        # spawned and the report has no game-scm line. env.MADEIRA_DOCK_GAME_SCM = 1 starts it before Steam's LaunchApp
        # (tools/patch-dock-game-scm.py): GTA V's sessions that carry it spawn services.exe, report game-scm=2 and have no 1722
        # (FMWZC5, YFYGD4, BT9TE3, 0DHNMC against MM0DAY and 4GAWSA). "none": the report says the manager already ran
        # (game-scm=2 started, 1 already there), so the line is not what is missing. Not proven to get the game itself running.
        "all": [s("[svc] Unable to open SC manager! (1722)")],
        "none": [s("[dock-report] game-scm=2"), s("[dock-report] game-scm=1")],
        "reason": "The game's launcher (Rockstar's) could not reach a Windows service manager, because a Steam game's "
                  "session starts none; the line starts Wine's before the game.",
        "evidence": ["gta-san-andreas-de-2026-10-21 (MM0DAY)", "gta-iv-launcher-2026-10-21 (4GAWSA)",
                     "FMWZC5 (GTA V Legacy, game-scm=2: no 1722, the Launcher's window stayed 6.9 min)"],
    },
    {
        "id": "tiny-swapchain-window",
        "lines": ["env.MADEIRA_TINY_WINDOW_FIX = 1"],
        "priority": 13,
        # American Truck Simulator (Steam 270880, iPad16,10, build 576, two logs of one tester): the game's window is
        # created 132x38 (Wine's minimum size of a captioned window), the game switches it to WS_POPUP and centres it
        # at that size, and its swapchain draws 1024x768 into it: "opens in an incredibly tiny window". Dying Light's
        # main window is 132x38 too. Build 578 logs [tiny-win] for a swapchain window whose client area stays under
        # 160x120 for 3 s while a 320x200 or larger picture is drawn into it, and with the line it sizes that window
        # to the virtual screen (driver_ios.c, tests/host/check-tiny-window.py). The session is not a crash, so this
        # only helps one the user ended as frozen or stopped early. "none": the line already ran.
        "outcomes": ["hung", "exited-early"],
        "all": [s("[tiny-win]", "would size it to the screen")],
        "none": [s("[tiny-win]", "sized to the virtual screen")],
        "reason": "The game drew into a window far smaller than its picture and never sized it; the line sizes "
                  "such a window to the screen after three seconds.",
        "evidence": ["american-truck-simulator-2026-10-21 (iPad16,10, build 576; the [tiny-win] line is 578's)",
                     "tests/host/check-tiny-window.py (build 578)"],
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
        "id": "winrt-wintypes-uncaught-exception",
        "lines": ["env.MADEIRA_WINTYPES = 1"],
        "priority": 10,
        "noRetry": True,
        # Risk of Rain 2 (Steam 632360; 1M1KDQ, build 592, iPad17,4; WJPFEG, build 579, iPhone18,1): Rewired_WindowsGamingInput.dll, the input plugin,
        # starts with RoGetActivationFactory("Windows.Foundation.Metadata.ApiInformation"); the 64-bit set has no wintypes.dll, Wine logs
        # "RoGetActivationFactory Failed to load module L"C:\\windows\\system32\\wintypes.dll"" and the plugin's C++/WinRT code throws the
        # 0x8007007e (module not found) as an exception nothing catches: "wine: Unhandled exception 0xe06d7363", the process ends 8 s after it starts.
        # Both signals are needed: the failed factory alone is logged by programs that handle it; an uncaught 0xe06d7363 is the end of the process.
        # The line is the one wintypes-dll-optin gives a program that cannot import the DLL at all (c0000135); here the DLL is loaded late, by
        # name, from the Windows Runtime activation code. Megabonk loads the same plugin (hash 55a4cd8724d701f9) with the line and goes on.
        "all": [s("err:combase:RoGetActivationFactory Failed to load module", "wintypes.dll"), s("Unhandled exception 0xe06d7363")],
        "none": [s("MADEIRA_WINTYPES=1: wintypes.dll -> wintypes-optin.dll")],
        "outcomes": ["crashed"],
        "reason": "The game's input plugin asked Windows for a Windows Runtime class, found no wintypes.dll and threw an exception nothing "
                  "catches; the line gives this game Wine's wintypes.dll.",
        "evidence": ["risk-of-rain-2-2026-10-23 (1M1KDQ, build 592; WJPFEG, build 579: the same stop)",
                     "megabonk 2FYRC6 (build 592): the same plugin runs with the line"],
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
        "id": "sfc-tbs-dll-optin",
        "lines": ["env.MADEIRA_OPTIN_DLLS = sfc,sfc_os,tbs"],
        "priority": 10,
        "noRetry": True,
        # Diablo II: Resurrected (bcd-games issue #3, build 573, iPhone 16 Pro): D2R_loader.dll imports sfc.dll and
        # tbs.dll, BCD's 64-bit set ships neither, and D2R.exe ended with c0000135. Build 579 ships Wine's sfc, sfc_os
        # (sfc.dll forwards to it) and tbs as -optin DLLs (tools/build-wine-extra-dlls.sh, OPTIN_WANT).
        "all": [s("loader_init Importing dlls for", "failed, status c0000135")],
        "any": [s("err:module:import_dll Library sfc.dll", "not found"),
                s("err:module:import_dll Library tbs.dll", "not found")],
        "none": [s("MADEIRA_OPTIN_DLLS: sfc.dll -> sfc-optin.dll"), s("MADEIRA_OPTIN_DLLS: tbs.dll -> tbs-optin.dll")],
        "reason": "The game imports sfc.dll or tbs.dll, which BCD does not ship for every game; the line gives this "
                  "game Wine's sfc, sfc_os and tbs.",
        "evidence": ["d2r-2026-10-20 (synthetic: the import lines quoted in bcd-games issue #3, in the Fort Solis "
                     "build 574 session frame; no Send Log of the game yet)"],
    },
    {
        "id": "gdi-table-32bit-child",
        "lines": ["env.MADEIRA_GDI_SHARED_SECTION = 1"],
        "priority": 10,
        "noRetry": True,
        # A session that began 64-bit (a launcher, a game "In a Wine desktop") and later starts a 32-bit
        # program: win32u made the GDI handle table when the first program started, as private host memory
        # (wine dlls/win32u/gdiobj.c, gdi_shared_use_section), and the 32-bit program's gdi32 truncates its
        # address and faults on its first GDI handle. Battle.net-Setup.exe from the Desktop (build 579) died
        # that way three times; env.MADEIRA_GDI_SHARED_SECTION = 1 opened its window (owner, 2026-10-21).
        # Build 580 gives the library's Desktop the section by default (ContentView.swift, DesktopGDISection)
        # and logs the signal below from ntdll (build/ntdll-unix/virtual_ios.c, ios_wow_session_arm).
        "all": [s("[gdi-shared] 32-bit program in a session whose GDI table is private host memory")],
        "none": [s("[gdi-shared] session table", "section-backed")],
        "reason": "A 32-bit program this game started could not reach Windows' GDI table, which the session made "
                  "for 64-bit programs only; the line makes it a table 32-bit programs can map.",
        "evidence": ["battlenet-setup-2026-10-21 (synthetic: the build 579 Desktop session of Battle.net-Setup.exe "
                     "with build 580's signal line added where the session was armed)",
                     "owner's next session (explorer.exe 21:01:40, the line in the Desktop's Game config): the setup "
                     "window opened"],
    },
    {
        "id": "cmd-exe-arm64ec-core",
        "lines": [],
        "fixedIn": 579,
        "priority": 1,
        "session": "library",
        # A library entry that is a .bat/.cmd (Library.swift launchCommand: "C:\windows\system32\cmd.exe /c <file>"):
        # VSWVB0 "Play NeoEE.bat" (build 576, iPad17,3) and the same lines in JFFRDP, H579EM (Watch Dogs) and ZSRKW7
        # (Miles Morales) on build 574. WineProcessBridge.m picked the arm64ec-windows core for the backslash path, but
        # cmd.exe is shipped only as a native aarch64 program (aarch64-windows, i386-windows; arm64ec-windows has none),
        # a native ARM64 process cannot load the ARM64EC kernel32, and Wine stopped after 0.5 s with "could not load
        # kernel32.dll, status c000007b" before the batch file was read. Build 579 (madeira_target_is_cmd,
        # tests/host/check-cmd-bundle.py) runs cmd.exe in the aarch64-windows core, the one the Wine desktop uses. No
        # config line changes it. Both signals are the engine's own lines, printed whatever the diagnostic switches; a
        # 579 session says "(bundle=aarch64-windows)" and no longer matches.
        "all": [s("[WineProc] Target exe: C:\\windows\\system32\\cmd.exe (bundle=arm64ec-windows)"),
                s("wine: could not load kernel32.dll, status c000007b")],
        "reason": "The batch file's command interpreter (cmd.exe) was started in the wrong set of Windows files, "
                  "so it stopped before it read your file; from this build on BCD starts it in the right one.",
        "evidence": ["bat-cmd-arm64ec-core-2026-10-21 (VSWVB0, build 576)", "JFFRDP, H579EM, ZSRKW7 (build 574)",
                     "tests/host/check-cmd-bundle.py (build 579)"],
    },
    {
        "id": "ilonly-native-no-mscoree",
        "lines": [],
        "fixedIn": 579,
        "priority": 1,
        # The Dishwasher: Vampire Smile (SSXDRW, build 576, iPhone13,1, a Steam game through Madeira Dock): VampireSmile.exe is an
        # AnyCPU IL-only .NET Framework program (PE32, no 32BITREQUIRED). Wine's server marks such an image ComPlusNativeReady and runs
        # it as a process of the NATIVE machine; here that is ARM64, whose set (aarch64-windows) has no mscoree.dll (Wine Mono has no
        # ARM64 build), so the loader stopped with "mscoree.dll not found, IL-only binary ... cannot be loaded", c000007b, 6 s after
        # the start ([Wine child] 64-bit image machine=0xaa64 for a PE32 whose header says 0x14c; virtual_map_module 0x4000000e).
        # Build 579 (build/wineserver/mapping_ios.c ios_native_has_mscoree, tests/host/check-ilonly-machine.py) withholds the flag
        # while the native set has no mscoree.dll: the program runs as i386 under WoW64 with the i386 mscoree.dll and Wine Mono, like
        # the 32BITREQUIRED .NET programs. Wine Mono must be installed (Settings > .NET Framework). No config line changes it.
        # The signal is Wine's own loader error, printed whatever the diagnostic switches. "none": the engine's "[ilonly-machine]"
        # line, which a 579 session prints when it withheld the flag -- a later mscoree failure is then another cause.
        "all": [s("err:module:fixup_imports_ilonly mscoree.dll not found, IL-only binary")],
        "none": [s("[ilonly-machine]")],
        "reason": "The game is a .NET program that Wine started in a mode BCD has no .NET runtime for, so it stopped at "
                  "once; from this build on BCD starts it the way its 32-bit .NET programs run (Wine Mono, from "
                  "Settings, is needed).",
        "evidence": ["dishwasher-ilonly-2026-10-21 (SSXDRW, build 576)", "tests/host/check-ilonly-machine.py (build 579)"],
    },
    {
        "id": "twinapi-appcore-dll-optin",
        "lines": ["env.MADEIRA_OPTIN_DLLS = twinapi.appcore"],
        "priority": 10,
        "noRetry": True,
        # Senua's Saga: Hellblade II (XJ7Z6Y, build 576, iPad16,5): the GDK libraries XCurl.dll and
        # Microsoft.Xbox.Services.GDK.C.Thunks.dll import api-ms-win-core-psm-appnotify-l1-1-0.dll; Wine's apiset
        # (apisetschema.spec) maps it to twinapi.appcore.dll ("[dll-missing] #1 twinapi.appcore.dll c0000135"), which BCD's
        # 64-bit set does not ship, so the exe's import chain failed (c0000135, 2 s into the session). Build 579 ships
        # Wine's twinapi.appcore as twinapi.appcore-optin.dll (tools/build-wine-extra-dlls.sh, OPTIN_WANT). Wine's
        # RegisterAppStateChangeNotification and its three neighbours return ERROR_CALL_NOT_IMPLEMENTED (stubs): the
        # import resolves, what the GDK libraries do with that answer is not known. Any program that imports that
        # API set gets the line, not only this one.
        "all": [s("err:module:import_dll Library api-ms-win-core-psm-appnotify", "not found")],
        "none": [s("MADEIRA_OPTIN_DLLS: twinapi.appcore.dll -> twinapi.appcore-optin.dll")],
        "reason": "The game imports an API set that Wine serves from twinapi.appcore.dll, which BCD does not ship for "
                  "every game; the line gives this game Wine's twinapi.appcore.dll.",
        "evidence": ["hellblade-2-2026-10-21 (XJ7Z6Y, build 576)"],
    },
    {
        "id": "x64-machine-arm64-launcher",
        "lines": ["env.MADEIRA_X64_MACHINE = 1"],
        "priority": 10,
        "noRetry": True,
        # BeamNG.drive (SYDJDY on 576; VV1FQ4, DZ6BN4, PEVMEQ on 574; all iPhone16,2, iOS 27.2): the 32-bit launcher
        # reads the machine as ARM ("Current mode: native ARM (click to change)") and starts
        # BinArm64\BeamNG.drive.arm64.exe, a native ARM64 image; BCD's native ARM64 set has no D3DCOMPILER_47.dll, the
        # loader stops with c000007b and the process ends with c0000135. The same launcher on iPhone18,2 (SRWY27)
        # chose "emulated x64" and started Bin64\BeamNG.drive.x64.exe. What decides is not in the logs; the line
        # (tools/patch-wine-x64-machine.py, build 577) makes the native machine a 32-bit program sees AMD64
        # (IsWow64Process2, PROCESSOR_ARCHITEW6432) and logs "[x64-machine] IsWow64Process2" for each answer. The
        # signal is Wine's own loader error for a native ARM64 image, printed whatever the diagnostic switches.
        "all": [s("loader_init Importing dlls for", ".arm64.exe", "failed, status c0000135")],
        "none": [s("[x64-machine] IsWow64Process2")],
        "reason": "The game's launcher took BCD for an ARM machine and started its ARM64 program, which BCD cannot "
                  "load yet; the line tells it the machine is x64, so it starts the x64 program.",
        "evidence": ["beamng-arm64-launcher-2026-10-21 (SYDJDY); BeamNG VV1FQ4, DZ6BN4, PEVMEQ (build 574)",
                     "tests/host/check-x64-machine.py (build 577)"],
    },
    {
        "id": "asf-wmv-media-source",
        "lines": ["env.MADEIRA_WG_64BIT = 1", "env.MADEIRA_OPTIN_DLLS = mfasfsrcsnk,wmvdecod,wmadmod"],
        "priority": 10,
        "noRetry": True,
        # Resident Evil 2 (Steam 883710, build 579, iPad16,3; log re2-ipadm4-579) and Resident Evil 7 (Steam 418370, build 576,
        # iPhone18,1; log 5MXS1S): the game plays a WMV cutscene through a Media Foundation source reader; the source resolver
        # looks up the ASF byte stream handler, CLSID {41457294-644c-4298-a28a-bd69f2c0cf3b}, whose in-process server is
        # C:\windows\system32\mfasfsrcsnk.dll, and BCD ships no such DLL ("[dll-missing] ... mfasfsrcsnk.dll status=c0000135",
        # "couldn't load in-process dll"). The resolver then falls back to the generic handler ("stream_handler_BeginCreateObject
        # Unimplemented flags 0x11"), which fails too (a 64-bit game has the stub winegstreamer table), and the game does not
        # check the result: `mov rcx,[rsi+8]` with RSI = 0, c0000005, within about a second. Build 580 ships Wine's ASF handler,
        # wmvdecod and wmadmod as -optin DLLs (tools/build-wine-extra-dlls.sh, OPTIN_WANT; the handler made to use winedmo's
        # FFmpeg demuxer by tools/patch-wine-mfasfsrcsnk-winedmo.py), and gives a 64-bit game BCD's winegstreamer unix side with
        # MADEIRA_WG_64BIT = 1 (it decodes WMV: build/ntdll-unix/winegstreamer_wmv_ios.c). Both lines come together: the DLLs do
        # nothing without the table, and the table has nothing to decode without the DLLs.
        "all": [s("err:module:load_dll [dll-missing]", "mfasfsrcsnk.dll", "status=c0000135")],
        "none": [s("MADEIRA_OPTIN_DLLS: mfasfsrcsnk.dll -> mfasfsrcsnk-optin.dll")],
        "reason": "The game plays a Windows Media Video cutscene through Media Foundation and BCD ships no ASF source; the "
                  "lines give this game Wine's ASF source and WMV and WMA decoders, and BCD's media decoders for a 64-bit game.",
        "evidence": ["re2-ipadm4-579 (Resident Evil 2, Discord, build 579)", "5MXS1S (Resident Evil 7, build 576, the same chain)"],
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
        "id": "setupapi-devinst-race",
        "lines": ["env.MADEIRA_OPTIN_DLLS = setupapi"],
        "priority": 10,
        "noRetry": True,
        "outcomes": ["crashed"],
        # Dying Light (Steam 239140), the owner's iPhone18,2, build 576, log 1c807435: the controller
        # library libpad.dll lists HID devices on thread 00fc while the main thread loads dinput8.dll, and
        # faults at setupapi.dll+0x132bc (create_device, get_devinst_for_device_id inlined) with RSI =
        # "HID\VID_845E&PID_0001\0&0000&0&0&0" ([av-args], build 576). Wine's alloc_devinst_for_device_id sets
        # devinst_table_size = 256 BEFORE devinst_table = calloc(...) and has no lock, so a second thread
        # sees 256 entries in a NULL table. setupapi-optin.dll (tools/patch-wine-setupapi-devinst-lock.py,
        # build 577) has the lock and allocates before it publishes the size; the line links it as
        # setupapi.dll for the game (the shipped setupapi.dll is untouched for every other game).
        # No unchanged retry first (noRetry): the signature names the race itself, so a retry could only
        # pass by luck (build 575's three sessions did, and ended later at engine_x64_rwdi.dll+0x332ee),
        # and the line is inert for any other path. The signature is a NULL-class fault whose PC is in
        # setupapi.dll with an HID instance id in [av-args]; "none": the line already ran. The experiment
        # locale-en-us-ansi (priority 12) matches the same session too and comes AFTER this rule: the lock
        # is a proven cause, the locale is a guess, and the next session shows which one was in the way.
        "all": [s("[mach_exc] pc PE: setupapi.dll+"), s("[av-args]", "NULL-class fault"), s("[av-args]", "\"HID\\")],
        "none": [s("MADEIRA_OPTIN_DLLS: setupapi.dll -> setupapi-optin.dll")],
        "reason": "The game's controller library and its main thread listed devices at the same moment, and "
                  "Wine's device list code read a table that was not made yet; the line gives this game a copy "
                  "of that library with a lock round the table.",
        "evidence": ["dying-light-2026-10-21 (log 1c807435, build 576)",
                     "tests/host/check-setupapi-devinst-lock.py (build 577)"],
    },
    {
        "id": "fallout-4-launcher-swap",
        "lines": ["env.MADEIRA_SPAWN_SWAP = Fallout4Launcher.exe=Fallout4.exe"],
        "priority": 5,
        "noRetry": True,
        "session": "dock",
        # Fallout 4 (Steam 377160) through BCD Dock, C050A4 (build 580, 2026-10-22): Steam's launch configuration for the game is ONE entry,
        # Fallout4Launcher.exe, and Valve's client starts that. It is a 4.3 MB 64-bit image with its relocations stripped; it starts a second
        # instance of itself 0.2 s later (L8341, L9241) and that one fails ("wine: failed to create main module ... Fallout4Launcher.exe, status
        # c0000018", L9334), so the Dock ends the session 19 s after Play (L10489). exe-window-fixed-base does not match it (the first instance has
        # the window: "[exe-window] ... gets the window"), and no window size helps a second instance. Two players swapped the file names
        # Fallout4.exe and Fallout4Launcher.exe and the game ran (CW3VZF, build 592, 14 minutes). The line makes the process start, when the
        # launcher is asked for, start Fallout4.exe of the same folder (build 598; the game's list has it from the first start).
        # Not once the swap has run ("[spawn-swap] ... starts as Fallout4.exe (command line rewritten|kept)"): then the launcher is not what
        # failed. The line of a swap that found no Fallout4.exe in the folder does not say "(command line", and the rule may still fire.
        "all": [s("failed to create main module", "Fallout4Launcher.exe", "c0000018")],
        "none": [s("[spawn-swap]", "starts as", "(command line")],
        "reason": "Fallout 4's launcher cannot start twice here (a second copy of the same program cannot be loaded). The line starts the "
                  "game's own program in its place, as swapping the two file names did for two players.",
        "evidence": ["fallout-4-launcher-2026-10-22 (C050A4, build 580)", "CW3VZF (build 592: the game started under the launcher's name ran 14 minutes)",
                     "tests/host/check-spawn-swap.py (build 598)"],
    },
    {
        "id": "skyrim-se-launcher-swap",
        "lines": ["env.MADEIRA_SPAWN_SWAP = SkyrimSELauncher.exe=SkyrimSE.exe"],
        "priority": 5,
        "noRetry": True,
        "session": "dock",
        # The Elder Scrolls V: Skyrim Special Edition (Steam 489830) through BCD Dock, the same stop as Fallout 4's launcher: Valve's client starts
        # SkyrimSELauncher.exe, which starts a second instance of itself, and that one fails ("wine: failed to create main module ...
        # SkyrimSELauncher.exe, status c0000018"); the Dock ends the session ("[dock-game-end] skyrimselauncher.exe ended with status 0xc0000018").
        # Four logs on three days (E3DMDC, VMY6C5 2026-10-21; WYE377 2026-10-23; V1W4P9 2026-10-24; builds 580-592). A Discord player (Zerxiese,
        # iPhone16,2, build 592) renamed the launcher and put a copy of SkyrimSE.exe under its name, and the game ran (TRMJDZ, 2026-10-24). The line
        # (build 598's spawn swap) starts SkyrimSE.exe of the same folder when the launcher is asked for; nothing on disk changes.
        "all": [s("failed to create main module", "SkyrimSELauncher.exe", "c0000018")],
        "none": [s("[spawn-swap]", "starts as", "(command line")],
        "reason": "Skyrim Special Edition's launcher cannot start twice here (a second copy of the same program cannot be loaded). The line starts "
                  "the game's own program in its place, as renaming the two files did for a player.",
        "evidence": ["skyrim-se-launcher-2026-10-23 (WYE377, build 592)", "TRMJDZ (build 592: the game started under the launcher's name ran)",
                     "tests/host/check-spawn-swap.py (build 598)"],
    },
    {
        "id": "exe-window-fixed-base",
        "lines": ["exe-window-mb = 512"],
        "priority": 6,
        "noRetry": True,
        "exeMaxMB": 511,
        # A main program with its relocations stripped, larger than the 128 MB window at 0x140000000,
        # ran into the JIT pool (Hogwarts Legacy D67PYY, Star Wars Galactic Racer 3HWREK/XDCPK7, Metal
        # Gear Solid V 6E40B4, Stellar Blade's demo WNDRGF; 2026-10-17..20).
        "all": [s("failed to create main module", "c0000018")],
        # Round 580: not when the program is too big for the window the line asks for. Persona 4 Golden (2F8ZK6, 9AMZKP, QK7BQP, build 579,
        # iPhone14,3): the exe is 888 MiB ("ml985: map_image_view size=0x378b2000 base=0x140000000 ... charact=0x23"), the try of
        # exe-window-mb = 512 widened the window and the program was still 376 MiB short ("tried exe-window-fixed-base: reverted", L10233),
        # and no window can be larger than the free run below the thread stacks that exist before BCD starts (0x140000000 up to
        # 0x16d000000 there, 720 MiB: L66 "ml1135: the placeholder ended at the mapping 0x16d060000 (VM tag 30 ...), already there at image
        # load"). The main exe's own line gives its size ("ml985: map_image_view size=0x378b2000 base=0x140000000 ... charact=0x23", the
        # scanner reads it as AutoTuneLogFacts.fixedExeMB); exeMaxMB 511: a program of 512 MiB or more is one the line cannot hold.
        # Dead Space 2023 (419 MiB), Persona 3 Reload (355 MiB), Yakuza: Like a Dragon (397 MiB) and the lists' 314-480 MiB programs are
        # below it and keep the rule.
        #
        # Not when the first process already got the window for its small stripped program (DELTARUNE 035KD6,
        # Skyrim SE VMY6C5, round 577): there a SECOND instance of the same program fails, which no window size fixes.
        "none": [s("[exe-window]", "gets the window"),
                 # Not when the program that failed is a one-time installer's helper, not the game: Saints Row: The
                 # Third's DirectX setup extracts infinst.exe into %TEMP% and its main module failed the same way
                 # (T3P8HK, build 576, line 6990: ...\\AppData\\Local\\Temp\\DXa9e1.tmp\\infinst.exe, c0000018), and the
                 # rule proposed a window for the GAME. The path in Wine's own line is the signal (backslashes
                 # doubled, as debugstr_w writes them); a game's program is never run from %TEMP%.
                 s("failed to create main module", "\\\\Temp\\\\"),
                 s("failed to create main module", "infinst.exe")],
        "reason": "The game's program must load at a fixed address that BCD's JIT pool partly held; the "
                  "line keeps 512 MB there for it (the pool may be smaller).",
        "evidence": ["hogwarts-legacy-2026-10-20 (D67PYY)", "3HWREK, XDCPK7, 6E40B4, WNDRGF",
                     "saints-row-the-third-directx-helper-2026-10-21 (T3P8HK: the DirectX installer's helper is not the game; no match)",
                     "persona-4-golden-2026-10-21 (QK7BQP: an 888 MiB program no window holds; no match)",
                     "yakuza-like-a-dragon-2026-10-21 (F4V6BQ: a 397 MiB program keeps the rule)"],
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
        "id": "vcrt-arm64-marker-current",
        "lines": ["env.MADEIRA_VCRT_ARM64_KEY = 14.44.35211.0"],
        "priority": 8,
        "noRetry": True,
        # bcd-games issue #13 (Dead as Disco, 2026-10-23): up to build 590 the line above wrote 14.42.34433.0, and a
        # newer Unreal bootstrap (Pagoda.exe) refused it; with 14.44.35211.0 it started the game. From build 592 the
        # line above writes 14.44.35211.0 (or the session runtime's newer version), so a game meeting the dialog for the
        # first time is served by it. This rule is for a game whose search already tried that line on an older build
        # and gave up: auto-tune never tries a rule twice, so the same signature gets a second rule with a different line.
        # "none" as above: a session that wrote the marker and still showed the dialog gets no further attempt.
        "all": [s("Redistributable (arm64)", "[win-name] #")],
        "none": [s("[vcrt-arm64] madeira-bcd:")],
        "reason": "The game's start-up program asked for Microsoft's ARM64 Visual C++ runtime; an earlier BCD wrote an "
                  "older version of that entry, which newer games refuse. The line writes the current version.",
        "evidence": ["bcd-games issue #13 (Dead as Disco, Steam 3404260, build 580: 14.42.34433.0 refused, 14.44.35211.0 passed)",
                     "denshattack-2026-10-20 (MKT2TW; the first rule comes first)"],
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
        #
        # Not for a game started through the Rockstar Games Launcher (GTA V Legacy and Enhanced, Red
        # Dead Redemption 2; ajan5/rockstar, 2026-10-21): the line turns AVX on in the Launcher's
        # process only. RockstarService.exe is started by services.exe with the environment of the
        # registry, so FEX in it never prints "[fex-avx]" and keeps AVX off, while the Launcher and the
        # `RockstarService.exe start` helper run with it. All 6 Rockstar sessions with the line
        # (GBY65K, Y5MEB6, A50T0Q/ZME02J on the owner's iPhone 18 Pro, Y7GWQB, G2G7WA) end at the
        # Launcher's "Failed to connect to the Rockstar Games Library Service" (code 159) or with no
        # handshake; all 11 sessions of the same builds that got past Steam without the line connected to
        # the service, started Social Club's helper and (7 of them) GTA5.exe. On the owner's phone the
        # same game worked on 8JXT6Q (no line) and failed on every session that had it. The c000001d
        # that matched it in GTA V Legacy sessions came from the Rockstar installers' own probing
        # (DR83Z9: Rockstar-Games-Launcher.exe), which a played session also shows.
        #
        # Round 580: not for a c000001d that is the end of another fault.
        #  - After an unhandled store the emulator could not do: Jetboard Joust (MonoGame on CoreCLR; 5QQ3XX, 5WG4FG, N4F8JR, build 578):
        #    "[store-undecoded] #1 ... insn=0xc8e8ff60 (CASAL) ... ADD THIS ENCODING to the case-4 emulator" (N4F8JR L10246) and more
        #    than 200 unhandled faults came before the c000001d (L13428). The line was tried (N4F8JR "tried fex-avx-on: reverted", 5QQ3XX
        #    already ran with FEX_AVX=1, L57) and the game died the same; every start it cost was a wasted one.
        #  - On a host SIGILL on a data word: Portal (non-Steam, Q3SG8T and CQEQJX, build 579): the program executed the word 0x00000036
        #    ("[mach_exc] insn_stream PC-12..PC+8: ... [00000036] ...", then "ill_handler ILL at pc=0x301131358" in kernelbase.dll) after a
        #    child process's I/O completion faulted. A64 words with 16 zero bits on top are UDF encodings, never what the emulator plants for an
        #    instruction it cannot decode: The Last of Us Part I (JP833A and R2NFT1, build 576, the proven case: AVX on made the game go on)
        #    and No Rest for the Wicked (0M4SJD) print "[d4400000]" / "[d4400020]" (HLT, what FEX plants). Mere "ill_handler ILL" is no
        #    signal: the proven case has it.
        "none": [s("\" failed to initialize, aborting"), s("[guest-code] rev=ml333", "): d5 "),
                 s("[bcd] library launch Grand Theft Auto V"), s("[bcd] library launch Red Dead Redemption 2"),
                 s("[phase] spawn:", "RockstarService.exe"), s("[phase] spawn:", "Rockstar-Games-Launcher.exe"),
                 s("[phase] spawn:", "Rockstar Games\\\\Launcher\\\\Launcher.exe"),
                 s("[store-undecoded]"), s("[mach_exc] insn_stream PC-12..PC+8:", "[0000")],
        "reason": "The game ran an instruction FEX does not decode with AVX off (c000001d); the line "
                  "turns AVX on. FEX reads it itself, so it works in BCD Dock sessions too.",
        "evidence": ["HANDOFF: Crysis 2 Remastered 540 (c000001d, DISABLEAVX expected), HFW, GoT, Rift Apart 531",
                     "not for an invalid opcode: Age of Empires IV 7DXVSK/34WSPP died the same with AVX on",
                     "not for the Rockstar Games Launcher's games: GBY65K, Y5MEB6, A50T0Q (AVX on: service "
                     "connection fails) against 8JXT6Q, DGNRT5 (AVX off: connects, GTA5.exe plays); "
                     "gta-v-legacy-2026-10-20 (DR83Z9)",
                     "not after a store the emulator could not do (Jetboard Joust N4F8JR, 5QQ3XX: AVX on, the same end) or a host SIGILL on "
                     "a data word (Portal Q3SG8T); the proven case, The Last of Us Part I JP833A (build 576), keeps the rule"],
    },
    {
        "id": "thread-stack-spill",
        "lines": ["env.MADEIRA_THREAD_STACK_SPILL = 1"],
        "priority": 20,
        "all": [s("[thr-create] FAILED at")],
        # Not for a 32-bit program's own stack: it is bounded by the guest window's ceiling (limit_high), and the line
        # only relaxes requests bounded by a 4 GB floor with no ceiling (virtual_ios.c map_view: `!limit_high`; the
        # kernel, emulator and WoW64 64-bit stacks). Shade on build 578 (6NE7KH, lines 2951-3088): its 32-bit stack
        # and the 8 MB d3d9 buffer were refused by a MAP_FIXED over the window's own free ranges ("fit > 0" in the
        # [va-scan] window room line), the line was tried and reverted: it could not have helped.
        "none": [s("[thr-create] FAILED at WoW64 32-bit stack")],
        "reason": "A thread could not get its stack in the game's address range; the line lets "
                  "stacks take the kernel's placement.",
        "evidence": ["HANDOFF: Ghost of Tsushima 515, RDR2 460",
                     "shade-wow64-stack-2026-10-21 (6NE7KH: a WoW64 32-bit stack refused inside the window; no match)"],
    },
    {
        "id": "lock-orphan-stamped",
        "lines": ["env.MADEIRA_LOCK_ORPHAN = stamped"],
        "priority": 30,
        "all": [s("[lock-reap] SRW", "released exclusive")],
        "any": [s("is not owned exclusive!")] + UNHANDLED,
        # Round 591: not in a Dock session whose host result was 47 (the nested Steam bootstrapper of GTA: Vice City, D938HM): the reaped
        # lock was a dying child's of that bootstrapper.
        "none": [s("[dock-report] launch-host-result=47")],
        "reason": "A lock held by a thread that died was released the old way and another thread "
                  "tripped over it; the line releases such locks safely.",
        "evidence": ["HANDOFF: Horizon Zero Dawn 479-480, GTA V 510",
                     "vice-city-dock-steam-bootstrapper-2026-10-22 (D938HM, build 580: the nested bootstrapper's children; no match)"],
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
        # The rule's own evidence is a held release: the hold prints "[free-delay] #N release of ... held for 2000 ms"
        # for its first eight holds, and the trap (HFW: the program allocates, releases, then reserves inside the
        # released block) cannot happen without one. Minecraft Dungeons (CYDN1E, build 578) refused 458,752 RWX probes
        # of its own protection sweep, with no [free-delay] line anywhere in the log: not this failure.
        "all": [s("[alloc-fail]", "status=c0000018", count=200), s("[free-delay] #")],
        "none": [s("[win-name] #", "not supported")],
        "maxStage": 2,
        "reason": "Address requests kept colliding with ranges held back after a free; the line "
                  "frees them at once.",
        "evidence": ["HANDOFF: Horizon Forbidden West 517",
                     "minecraft-dungeons-2026-10-21 (CYDN1E: 200+ refused probes and no [free-delay] line; no match)"],
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
    {
        "id": "program-exit-off-boot-thread",
        "lines": [],
        "fixedIn": 578,
        "priority": 1,
        "session": "library",
        # GTA IV (non-store folder, mod ConsoleSelectMenuIV.asi; log gta4-freeze, build 576, 2026-10-21 10:18:42) and
        # Stranglehold (RVBE4K, build 569): the program ended through ExitProcess / abort_process on one of its own threads,
        # not on the engine's boot thread (the one running __wine_main). exit() long-jumps only on the boot thread
        # (shims/wine_ios_exit.h), so __wine_main never returned, "[WineProc] stopping wineserver..." never printed, the
        # session never ended and BCD kept the last frame ("the game freezes"); a tap on it then crashed iOS in
        # AssistantServices (getprogname() pointed into the boot thread's dead stack: [native-bt] progname). A program that
        # ends on the boot thread (tid 0024) returns to the library. WineProcessBridge.m (build 578,
        # "Session end"): when the program has been gone for 4 s and none of its own processes runs, BCD ends the
        # session itself, says "The game closed (exit code N)" and returns to the library; the app's own program name is
        # given back as the program ends (tests/host/check-session-end.py). No config line changes it. The signals are the
        # engine's own lines, printed whatever the diagnostic switches; the initial process is always pid 0020. "none":
        # the end work ran ("[WineProc] stopping wineserver..." is what both the boot thread and the new path print).
        "all": [s("[Wine ntdll/server] process_exit_wrapper("), s("[srv-kill] kill_process pid=0020 violent=0")],
        "none": [s("[WineProc] stopping wineserver")],
        "reason": "The game ended on a thread BCD was not watching, so BCD stayed on its last frame; "
                  "from this build on BCD notices, says that the game closed and returns to the library.",
        "evidence": ["gta-iv-2026-10-21 (gta4-freeze, build 576)", "Stranglehold RVBE4K (build 569)",
                     "tests/host/check-session-end.py (build 578)"],
    },
    {
        "id": "dock-game-died-session-open",
        "lines": [],
        "fixedIn": 579,
        # After the LOCALE experiment (priority 12): that line is the thing to try for Dying Light, and this rule must
        # not hide it. A BCD Dock session's own program is Wine's desktop, which never closes by itself on iOS, so a game
        # that died (Dying Light 32FEAT, build 578, c0000005 at 16:00:05; The Binding of Isaac VV5NHZ, build 576, c0000005)
        # left the session open on its dead desktop: the host reported "launch-game-ended=1" and finished, then
        # "detach-wait: presents=0 running=1" for as long as the user waited, and the session never reached auto-tune.
        # From build 579 the library ends such a session when the game's last process ended with an error status,
        # nothing of the game runs, 10 s passed and the host finished (DockGameEnd.swift, WineProcessBridge.m
        # wine_session_dock_game_ended, tests/host/check-dock-game-end.py), prints "[dock-game-end]" and says "The game
        # quit with Windows error ...". The signals are the engine's own and the host's report lines, printed whatever
        # the diagnostic switches: a process that ended with an NTSTATUS error (-10737... in decimal: 0xC00000xx-0xC00004xx)
        # and the host's own "game ended". "none": the new path ran (or the session ended some other way).
        # Round 591: not when the host's result is 47 (another Steam process took the registration): a legacy SteamStub game (GTA: Vice City,
        # D938HM and V85VDT) starts "steam.exe -applaunch" from the Dock, Valve's bootstrapper updates itself in the Dock install, and the
        # processes that died with c0000005 were the bootstrapper's children, not the game; "known" there sent the tester to a build that
        # fixed a different crash.
        "session": "dock",
        "priority": 13,
        "all": [s("[dock-report] launch-game-ended=1"), s("[Wine ntdll/server] process_exit_wrapper(-10737", "closing child")],
        "none": [s("[dock-game-end]"), s("[frontend] returned to library"), s("[dock-report] launch-host-result=47")],
        "reason": "The game's process crashed and Valve's client and the Dock host finished, but BCD kept the session "
                  "open on its dead desktop; from this build on BCD ends the session, says which Windows error the "
                  "game closed with, and auto-tune goes on from there.",
        "evidence": ["dying-light-dock-game-end-2026-10-21 (32FEAT, build 578)", "Isaac VV5NHZ (build 576)",
                     "tests/host/check-dock-game-end.py (build 579)"],
    },
    {
        "id": "wow-window-main-image-hang",
        "lines": [],
        "fixedIn": 579,
        "priority": 14,
        "session": "library",
        # Tomb Raider: Underworld ZWAA2K (build 576, iPhone 11, started from the library): the 32-bit main program could
        # not get its 4 GB window ("[wow-window] main-process reserve FAILED 0xc0000017", 6319 MB free of the 4096 MB plus
        # MADEIRA_WOW_MIN_FREE_GB left over). fatal_error() ended the boot thread with pthread_exit in the middle of
        # __wine_main, so the end work never ran and the app showed "running" for three minutes (detach-wait ... running=1).
        # From build 579 the same failure leaves __wine_main by the normal exit (loader_ios.c start_main_thread), the
        # session ends at once and says "The game quit with Windows error 0xC0000017 (not enough memory)". The cause
        # (the address space this install has) is not changed by it. "none": the new exit's own line.
        "all": [s("[wow-window] main-process reserve FAILED")],
        "none": [s("[WineProc] Wine exited with code"), s("[frontend] returned to library")],
        "reason": "A 32-bit game could not get the address space it needs on this install, and BCD stayed on 'running' "
                  "for minutes; from this build on the session ends at once and says so (the lack of address space is "
                  "the cause, and it is the install's).",
        "evidence": ["tomb-raider-underworld-main-window-2026-10-21 (ZWAA2K, build 576)",
                     "tests/host/check-dock-game-end.py (build 579)"],
    },
    {
        "id": "wow-window-no-address-space",
        "lines": [],
        "noFix": True,
        # Round tur600 (ajan36): Far Cry 3 (Steam 220240; its FC3UpdaterSteam.exe is a 32-bit program, W9521T = ERCDZS = ECMHQH, one session sent three
        # times, iPhone15,2, iOS 26.7.1, a 17 GB map) and Metro 2033 (Steam 43110, a 32-bit program; ZSQP7H, 8ZHPR2, iPad13,8, iPadOS 27, a 454 GB map,
        # a 7-day profile with profile-increased-memory=0): the Dock's Steam client started the game's 32-bit program, "[wow-window] refused: map too
        # small" (17 GB map: "ends at 0x458000000, MADEIRA_WOW_MIN_MAP_GB=48"; 454 GB map: "1660 MB free VA; a window needs 4096 MB plus ...") and
        # "BOOT FAILED at stage 'guest-window-reserve'" (c0000017), so Steam's launch failed (launch-client-error=46, host result 45, "The Steam launch
        # from BCD Dock failed (code 45)"). Without extended virtual addressing CoreAnimation's range ("[layerkit-range] ... 0x12052c000..0x7177000000")
        # and the 4 GB FEX arena leave no 4 GB-aligned place for the window; the install, not BCD, has too little address space. Of the 1116 intake logs
        # with a "[device] address-map=" line, all 57 that carry both "increased-memory=0 extended-va=0" and a 32-bit program (8 on a 17 GB map, 49 on a
        # 454 GB map) were refused, and none was given a window; the 56 logs of a 63 GB map with increased-memory=1 that armed one were all given it.
        # Nothing a setting changes. Low precedence (priority 60): a rule with lines that matches the same session is tried first, and a session that
        # presented for 20 s (maxStage 3) is playing, so the refusal of a helper does not describe it. The words are MemoryRights.startNotice's.
        "priority": 60,
        "maxStage": 3,
        "outcomes": ["crashed", "exited-early"],
        "all": [s("[wow-window] refused: map too small"),
                s("[device] address-map=", "increased-memory=0", "extended-va=0")],
        "any": [s("BOOT FAILED at stage 'guest-window-reserve'"), s("[wow-window] main-process reserve FAILED")],
        "reason": "A 32-bit program could not get the 4 GB address window it needs, because this install lacks the increased memory limit and "
                  "extended virtual addressing that give BCD enough address space. Add them with GetMoreRam (with SideStore or LiveContainer), "
                  "or sign BCD with a paid developer account.",
        "evidence": ["far-cry-3-wow-window-no-address-space-2026-10-24 (W9521T, ERCDZS, ECMHQH: one session, build 592, iPhone15,2, 17 GB map)",
                     "metro-2033-wow-window-no-address-space-2026-10-24 (8ZHPR2, ZSQP7H, build 592, iPad13,8, 454 GB map)",
                     "tests/host/check-autotune.py (the two fixtures)"],
    },
    # ajan9/motor (build 580): code fixes, no config line changes any of them
    {
        "id": "anon-alias-cas",
        "lines": [],
        "fixedIn": 580,
        "priority": 1,
        # Jetboard Joust (MonoGame on a self-contained CoreCLR; 5QQ3XX, 5WG4FG, N4F8JR, build 578): the CLR patches its own code
        # in an RWX allocation with `lock cmpxchg`, which FEX translates to CASAL X8,X0,[X27] (0xc8e8ff60). The page is physically
        # RX, so the store faults; the Mach handler's alias emulation had no compare-and-swap for an anonymous alias, the line
        # "[store-undecoded] ... ADD THIS ENCODING" was printed and the unhandled fault ended the program (c000001d came after it,
        # which made fex-avx-on match and cost a start). Build 580 (signal_arm64_ios.c) emulates CAS/CASA/CASL/CASAL on the alias,
        # atomically. The signal is the undecoded line with a CAS encoding (bits 31-20 of a CAS, any register): 0xc8a/0xc8b/0xc8e/
        # 0xc8f for 64-bit, 0x88a/0x88b/0x88e/0x88f for 32-bit.
        "any": [s("[store-undecoded]", "insn=0xc8a"), s("[store-undecoded]", "insn=0xc8b"), s("[store-undecoded]", "insn=0xc8e"),
                s("[store-undecoded]", "insn=0xc8f"), s("[store-undecoded]", "insn=0x88a"), s("[store-undecoded]", "insn=0x88b"),
                s("[store-undecoded]", "insn=0x88e"), s("[store-undecoded]", "insn=0x88f")],
        "all": [s("[store-undecoded]", "ADD THIS ENCODING")],
        "reason": "The game patches its own code in memory BCD keeps writable only through an alias, with an atomic "
                  "compare-and-swap BCD did not emulate, and the program ended on the failed write.",
        "evidence": ["jetboard-joust-2026-10-21 (N4F8JR; 5QQ3XX, 5WG4FG)", "tests/host/check-anon-cas.py (build 580)"],
    },
    {
        "id": "stp-q-page-straddle",
        "lines": [],
        "fixedIn": 580,
        "priority": 1,
        # Need for Speed Underground 2 (7F9ZRF, build 579): FEX's `STP Q16,Q17,[X10],#32` into an RWX allocation, 28 bytes in the page
        # that is written through an alias and 4 bytes in the next page, which has none (a plain writable page). The Q-pair emulation
        # wanted all 32 bytes in one alias, refused ("[stp-emul] ml629 ... REFUSING: 32B span leaves the alias"), the store was never
        # completed and faulted again 2000 times until the redelivery guard ended the program. Build 580 (signal_arm64_ios.c,
        # ios_store_split.h) stores the two parts page by page. "addr=0x7": a guest address, not the JIT pool's own view.
        "all": [s("[stp-emul] ml629", "REFUSING: 32B span leaves the alias", "addr=0x7"), s("identical redeliveries")],
        "reason": "A 32-byte store into the game's own code buffer crossed a memory page boundary, BCD refused to "
                  "complete it and the same write was repeated until BCD ended the game.",
        "evidence": ["need-for-speed-underground-2-2026-10-21 (7F9ZRF)", "tests/host/check-stp-straddle.py (build 580)"],
    },
    {
        "id": "dock-game-named-crash-session-open",
        "lines": [],
        "fixedIn": 580,
        "priority": 12,
        # Crash Bandicoot N. Sane Trilogy (3CH670, build 579): the game died with c0000005 and Valve's client and the Dock host
        # finished, but the session stayed open ("detach-wait ... running=1" for 211 s): the engine's file-name rule for crash
        # reporters took "CrashBandicootNSaneTrilogy.exe" for one (process_ios.c ios_child_is_helper), so the game-end watch of
        # build 579 never saw the game. Build 580 matches helper names by word. Before dock-game-died-session-open (13), whose
        # signals this repeats with the spawn of a program whose folder or file name starts with "Crash".
        "session": "dock",
        "all": [s("[dock-report] launch-game-ended=1"), s("[Wine ntdll/server] process_exit_wrapper(-10737", "closing child"),
                s("[phase] spawn:", "\\\\Crash")],
        "none": [s("[dock-game-end]"), s("[frontend] returned to library")],
        "reason": "The game's name starts with 'Crash', so BCD took it for a crash reporter and kept the session "
                  "open after the game had died; from this build on BCD tells them apart.",
        "evidence": ["crash-bandicoot-nsane-trilogy-2026-10-22 (3CH670, build 579)",
                     "tests/host/check-dock-game-end.py (build 580)"],
    },
    {
        "id": "d3d12-zero-extent-texture",
        "lines": [],
        "fixedIn": 580,
        "priority": 1,
        "api": "d3d12",
        # Marvel's Spider-Man Remastered (T3EXJB) and Miles Morales (X1TNT2): the game asked for a texture with a zero width and
        # height and Metal's descriptor validation ended the whole program ("failed assertion `Texture Descriptor Validation ...
        # MTLTextureDescriptor has width of zero"). Direct3D 12 answers such a request with E_INVALIDARG; build 580
        # (madeira_d3d12.c) does too and logs the game's description ("resource creation REFUSED (zero-size texture ...").
        # What the game does with the failure is not known.
        "all": [s("MTLTextureDescriptor has width of zero")],
        "reason": "The game asked for a texture with no size and Metal ended the program; from this build on BCD "
                  "answers that request with an error, as Windows does.",
        "evidence": ["spider-man-remastered-2026-10-21 (T3EXJB)", "Miles Morales X1TNT2",
                     "tests/host/check-d3d12-zero-extent.py (build 580)"],
    },
    # ajan9/app
    {
        "id": "dock-launch-failed-session-open",
        "lines": [],
        "fixedIn": 580,
        "session": "dock",
        "priority": 15,
        # Spec Ops: The Line (2FRXXM, NEXHHM) and Risen 2 (9DBDZ6), builds 579: games whose executable Valve builds per account
        # (CEG). Valve's client could not ("RpcAssoc_BindConnection receive failed with error 1726", ceg-job-result 55), the Dock
        # host finished with probe-result 49 a few seconds after it asked, no game process ever ran, and the session stayed open on
        # its desktop (2FRXXM: "detach-wait: presents=0 running=1" for 271 s until the user closed BCD). The starting screen showed
        # the reason, but nothing ended the session. From build 580 the library ends such a session 20 s after the host's failure
        # when nothing of the game runs (DockGameEnd.swift, Library.swift watchDockFailedLaunch) and shows the host's reason in
        # words. The cause (Valve's client cannot prepare the executable here) is not changed by it. The signals are the host's
        # own report lines, printed whatever the diagnostic switches. "none": the host finished well (probe-result 0), the game ran
        # (launch-game-running 1: the crash rule's), the new path ran ([dock-game-end]) or the session ended some other way.
        "all": [s("[dock-report] probe-result=")],
        "none": [s("[dock-report] probe-result=0"), s("[dock-report] launch-game-running=1"), s("[dock-game-end]"),
                 s("[frontend] returned to library")],
        "reason": "The Dock host reported that the game's launch failed and the game never ran, but BCD kept the session open on "
                  "its desktop; from this build on BCD ends the session after 20 seconds and shows the reason (the cause, for a "
                  "game with Steam's per-account copy protection, is Steam's own and is not changed by this).",
        "evidence": ["spec-ops-the-line-dock-launch-failed-2026-10-21 (NEXHHM, build 579)", "2FRXXM (build 579), Risen 2 9DBDZ6 (build 579)",
                     "tests/host/check-dock-game-end.py (build 580)"],
    },
    {
        "id": "bcd-date-formatter-crash",
        "lines": [],
        "fixedIn": 580,
        "priority": 2,
        # BCD's own interface died inside ICU's date formatter (libicucore udat_open under a Madeira frame: BCDGamePageLive.day when
        # a game's page opened after a session, A7V9T2 on build 576; BCDMenuModel.showCard from the in-game menu, DFSTWB on 576;
        # KDY8E6 on 574). Not a game's failure and no config line changes it; the program name the Wine session left dangling
        # (fixed in 579) was the cause of the two 576 logs, and from build 580 no date text of the app is made by ICU at all
        # (BCDFormat, tests/host/check-bcd-game-page.py). Early priority: the app's death reads as a kill with the game's last footprint lines, which the swap rules
        # would answer with a swap file; no game setting changes it.
        "all": [s("[native-bt]", "udat_open"), s("[native-bt]", "Madeira`$s7Madeira")],
        "reason": "BCD's own screen closed the app while it was making a date for display; this is in BCD, not in the game, and no "
                  "setting changes it.",
        "evidence": ["synthetic-bcd-date-formatter-crash (the backtraces of A7V9T2 and DFSTWB, builds 576)",
                     "tests/host/check-bcd-game-page.py (build 580)"],
    },
    # ajan10/wine (build 591)
    {
        "id": "oledlg-dll-optin",
        "lines": ["env.MADEIRA_OPTIN_DLLS = oledlg"],
        "priority": 10,
        "noRetry": True,
        # Digimon Story Cyber Sleuth: Complete Edition (Steam 1042550, PGET5A, build 580): "Library oledlg.dll (which is needed by
        # ...\\Digimon Story CS.exe) not found" and "[dll-missing] #1 oledlg.dll status=c0000135", the program ended at once.
        # BCD's 64-bit set had no oledlg.dll (the 32-bit set has it). Build 591 ships Wine's as oledlg-optin.dll
        # (tools/build-wine-extra-dlls.sh, OPTIN_WANT); its imports (ole32, comdlg32, user32, advapi32) all ship. Other imports of the
        # exe may be missing too: the loader stops at the first. The importing module spells the name as it likes, so the
        # signal lists the usual spellings (a signal matches case-sensitively).
        "all": [s("err:module:import_dll Library", "not found")],
        "any": [s("err:module:import_dll Library " + n + e, "not found") for n in ("oledlg", "OLEDLG", "Oledlg") for e in (".dll", ".DLL")],
        "none": [s("MADEIRA_OPTIN_DLLS: oledlg.dll -> oledlg-optin.dll")],
        "reason": "The game imports oledlg.dll, which BCD does not ship for every game; the line gives this game Wine's oledlg.dll.",
        "evidence": ["digimon-story-cyber-sleuth-2026-10-22 (PGET5A, build 580, c0000135 at once)"],
    },
    {
        "id": "fwpuclnt-dll-optin",
        "lines": ["env.MADEIRA_OPTIN_DLLS = fwpuclnt"],
        "priority": 10,
        "noRetry": True,
        # FINAL FANTASY XV WINDOWS EDITION PLAYABLE DEMO (Steam 671210, DM9SAF and its second Send Log CDC7AK, build 580): "Library
        # fwpuclnt.dll (which is needed by ...\\ffxv_s.exe) not found", c0000135 after 0 s. Windows' Filtering Platform client library
        # is not in the 64-bit set (the 32-bit set has it). Build 591 ships Wine's as fwpuclnt-optin.dll; most of its exports are
        # stubs, so what the game does with them is not known.
        "all": [s("err:module:import_dll Library", "not found")],
        "any": [s("err:module:import_dll Library " + n + e, "not found") for n in ("fwpuclnt", "FWPUCLNT", "Fwpuclnt") for e in (".dll", ".DLL")],
        "none": [s("MADEIRA_OPTIN_DLLS: fwpuclnt.dll -> fwpuclnt-optin.dll")],
        "reason": "The game imports fwpuclnt.dll, which BCD does not ship for every game; the line gives this game Wine's fwpuclnt.dll.",
        "evidence": ["ffxv-demo-2026-10-22 (DM9SAF, CDC7AK, build 580, c0000135 at once)"],
    },
    {
        "id": "sensapi-dll-optin",
        "lines": ["env.MADEIRA_OPTIN_DLLS = sensapi"],
        "priority": 10,
        "noRetry": True,
        # Borderlands 3 (Steam 397540, VFM84T, build 580): vivoxsdk.dll (the voice library under Engine\\Binaries\\ThirdParty\\Vivox)
        # imports SensApi.dll ("Library SensApi.dll (which is needed by ...\\vivoxsdk.dll) not found", "[dll-missing] #4 SensApi.dll
        # status=c0000135"); the failed load raised UE4's delay-load exception (c06d007e), nothing caught it and the game closed. Build 591
        # ships Wine's sensapi.dll (IsNetworkAlive and two more, always "connected") as sensapi-optin.dll. Whether anything else stops the game
        # afterwards is not known.
        "all": [s("err:module:import_dll Library", "not found")],
        "any": [s("err:module:import_dll Library " + n + e, "not found") for n in ("SensApi", "sensapi", "SENSAPI", "Sensapi") for e in (".dll", ".DLL")],
        "none": [s("MADEIRA_OPTIN_DLLS: sensapi.dll -> sensapi-optin.dll")],
        "reason": "A library of the game imports SensApi.dll, which BCD does not ship for every game; the line gives this game Wine's sensapi.dll.",
        "evidence": ["borderlands-3-2026-10-22 (VFM84T, build 580, vivoxsdk.dll)"],
    },
    {
        "id": "ddraw-dll-optin",
        "lines": ["env.MADEIRA_OPTIN_DLLS = ddraw"],
        "priority": 10,
        "noRetry": True,
        # Dying Light (Steam 239140, the owner's iPhone18,2, build 593, log 17:27:59): its renderer rd3d11_x64_rwdi.dll imports DDRAW.dll
        # ("Library DDRAW.dll (which is needed by ...\\rd3d11_x64_rwdi.dll) not found", "[dll-missing] #8 DDRAW.dll status=c0000135"); the game
        # said "Cannot load 'rd3d11_x64_rwdi.dll'" and "FATAL ERROR Cannot initialize renderer". Build 594 ships Wine's ddraw.dll as
        # ddraw-optin.dll. Dying Light's own list names it next to setupapi; this rule is for another 64-bit program that imports it.
        "all": [s("err:module:import_dll Library", "not found")],
        "any": [s("err:module:import_dll Library " + n + e, "not found") for n in ("DDRAW", "ddraw", "DDraw", "Ddraw") for e in (".dll", ".DLL")],
        "none": [s("MADEIRA_OPTIN_DLLS: ddraw.dll -> ddraw-optin.dll")],
        "reason": "A part of the game imports DDRAW.dll (DirectDraw), which BCD does not ship for every game; the line gives this game "
                  "Wine's ddraw.dll.",
        "evidence": ["dying-light-ddraw-2026-10-23 (owner, build 593, rd3d11_x64_rwdi.dll)"],
    },
    {
        "id": "eaapp-background-service-msi-dll",
        "lines": ["env.MADEIRA_OPTIN_DLLS = msi,mspatcha,odbccp32,sxs,rstrtmgr"],
        "priority": 10,
        "noRetry": True,
        # The EA app's install (the owner's build 594, log 19:20:11): past JunoInitializeSession the MSI copied the app, then its
        # StartServices action started EABackgroundService.exe, a 64-bit service that imports msi.dll and RstrtMgr.dll ("Library
        # msi.dll (which is needed by ...\\EABackgroundService.exe) not found", the same for RstrtMgr.DLL, "Importing dlls ... failed,
        # status c0000135"); services.exe answered 1053, StartServices returned 1627 and the package rolled back ("INST-14-1627").
        # Build 595 ships msi, rstrtmgr and the three DLLs msi.dll imports (mspatcha, odbccp32, sxs) as <name>-optin.dll. The EA app's
        # launcher entry names them; this rule is for an EA install started another way. Only EABackgroundService.exe's line matches.
        "all": [s("err:module:import_dll Library msi.dll (which is needed by", "EABackgroundService.exe")],
        "none": [s("MADEIRA_OPTIN_DLLS: msi.dll -> msi-optin.dll")],
        "reason": "The EA app's background service needs Windows Installer's msi.dll and the Restart Manager, which BCD does not ship "
                  "for every program; the line gives this session both.",
        "evidence": ["eaapp-background-service-msi-2026-10-23 (owner, build 594, EABackgroundService.exe)"],
    },
    {
        "id": "eaapp-desktop-libcef-dxva2",
        "lines": ["env.MADEIRA_OPTIN_DLLS = msi,mspatcha,odbccp32,sxs,rstrtmgr,dxva2"],
        "priority": 9,
        "noRetry": True,
        # The EA app's Launch (the owner's iPhone18,2, build 598, log 13:21:12): EADesktop.exe stopped at once with c0000135 and left the
        # empty Wine desktop. Its CEF, libcef.dll (64-bit), imports dxva2.dll ("Library dxva2.dll (which is needed by ...\\EA Desktop\\
        # libcef.dll) not found", then "Library libcef.dll ... not found", "Importing dlls ... failed, status c0000135"). The 64-bit set ships
        # dxva2 only as dxva2-optin.dll; every other DLL that CEF imports is in it. Build 599's EA launcher entry adds dxva2 to its DLL line;
        # this rule is for the EA app started another way, with the whole line (the service's five DLLs too). Priority 9, before
        # dxva2-dll-optin (10), whose line alone would leave the service without msi.dll.
        "all": [s("err:module:import_dll Library dxva2.dll (which is needed by", "EA Desktop", "libcef.dll")],
        "none": [s("MADEIRA_OPTIN_DLLS: dxva2.dll -> dxva2-optin.dll")],
        "reason": "The EA app's built-in browser (CEF) needs dxva2.dll, which BCD does not ship for every program; the line gives this "
                  "session Wine's dxva2.dll next to the DLLs the EA app's service needs.",
        "evidence": ["eaapp-desktop-libcef-dxva2-2026-10-24 (owner, build 598, EADesktop.exe Launch)",
                     "tests/host/check-bcd-launchers.py (build 599)"],
    },
    {
        "id": "eaapp-desktop-cef-pools",
        "lines": ["env.MADEIRA_SC_PA_POOLS = 2", "env.MADEIRA_EA_CEF = 1"],
        "priority": 8,
        "noRetry": True,
        # The EA app's Launch (the owner's iPhone18,2, build 598, log 13:54:38, dxva2 in the DLL line): EADesktop.exe started Chromium (CEF 109,
        # "Chrome/109.0.5414.120" in its user agent) and PartitionAlloc, in two copies (chrome_elf.dll, libcef.dll), asked for four 16 GB
        # pools: jumbo#1 plain -> 0x7800000000, #2 guard -> 0x73ffff0000 (both real), #3 plain -> a soft grant at 0x7400000000 inside #2's view,
        # #4 guard -> "[soft-pool] GRANTED soft 0x7800000000 (base returned 0x77ffff0000, size=0x400010000)" on #1's view: two live pools on
        # the same pages, a commit of size 0 ("[nt-fail] ... NtAllocateVirtualMemory status=c000000d proc=eadesktop.exe ... hint=0x780020d000")
        # and PartitionAlloc's int3. Its GPU process, EACefSubProcess.exe, then found no slot for its own four ([alloc-fail] ... size=0x400000000,
        # c0000018). The band holds four 16 GB slots, so the EA app's Chromium has to run in one process: env.MADEIRA_EA_CEF = 1 runs it
        # --single-process, refuses its --type= children and gives the four pools the four slots of Social Club's layout 2
        # (env.MADEIRA_SC_PA_POOLS = 2: the slots held at boot, the JIT pool's RW alias at 0x7900000000, the emulator's arena higher).
        # Build 599's EA launcher entry sets both; this rule is for the EA app started another way. An attempt: no device has run it.
        "all": [s("[soft-pool] GRANTED soft 0x7800000000", "base returned 0x77ffff0000", "size=0x400010000")],
        "any": [s("Session log: logs/EADesktop.exe-"), s("library launch EADesktop.exe:")],
        "none": [s("[madeira-env] game MADEIRA_EA_CEF=")],
        "reason": "The EA app's built-in browser needs four 16 GB memory pools and BCD gave two of them the same address space; the lines "
                  "run the browser in one process and give each pool its own slot.",
        "evidence": ["eaapp-desktop-cef-pools-2026-10-24 (owner, build 598, EADesktop.exe Launch)",
                     "tests/host/check-ea-cef.py (build 599)"],
    },
    {
        "id": "nettle-cbc-unaligned-abort",
        "lines": [],
        "fixedIn": 591,
        "priority": 1,
        # A Plague Tale: Innocence (GOG copy started from the library, 3S5DV8 on build 580; 7ZGYFT on 576): after BCryptCreateHash and
        # BCryptGenerateSymmetricKey a thread asked GnuTLS for an AES-CBC step with a length that is not a multiple of the block size,
        # and nettle's assert ended the whole app ("Assertion failed: (!(length % block_size)), function nettle_cbc_decrypt, file
        # cbc.c, line 72"; BCD's abort handler then raised 80000101 and the process died with a junk status). Build 591: Wine's bcrypt
        # unix side (dlls/bcrypt/gnutls.c) refuses such a length with STATUS_INVALID_BUFFER_SIZE, the status Windows gives, before it
        # calls GnuTLS, and logs "[bcrypt-guard]" with the key and the lengths. What the game does with the error is not known. No
        # config line changes it.
        "all": [s("Assertion failed: (!(length % block_size))", "nettle_cbc_")],
        "reason": "A library of the game asked the encryption code for a step with a length it cannot take, and the program that does the "
                  "work ended the whole app; from this build on BCD answers such a request with an error, as Windows does.",
        "evidence": ["plague-tale-innocence-2026-10-22 (3S5DV8, build 580)", "7ZGYFT (build 576, the same assert)",
                     "tests/host/check-bcrypt-cbc-guard.py (build 591)"],
    },
    {
        "id": "nls-extra-codepage",
        "lines": ["env.MADEIRA_NLS_EXTRA = 1"],
        "priority": 60,
        "session": "library",
        "outcomes": ["crashed", "exited-early"],
        # Sword and Fairy 3 (PAL3.exe, 32-bit, a local copy; 6FW5J5, build 580): the game asks for the Simplified-Chinese ANSI code page
        # four times ("[nls-getptr] type=11 id=936"), BCD's nls folder has no c_936.nls (only c_1252, c_437, c_20127, c_28591), so
        # GetCPInfo / MultiByteToWideChar(936) fail, and a few lines later the game's own code reads address 0 and the process ends
        # (0.8 s). Build 591 ships Wine's c_932, c_936, c_949, c_950, c_874 and c_1250 ... c_1258 in nls/extra and env.MADEIRA_NLS_EXTRA = 1
        # makes them visible (build/ntdll-unix/env_ios.c). Only a failed session of a direct start (library) that asked for one of
        # the Asian pages: Steam's own client asks for 932 in every Dock session and runs without it (a Dock session is left out, since
        # the client would see the tables too), and games on a Turkish or Russian phone ask for 1254 or 1251 and run without them, so
        # neither starts an attempt. "none": the tables were already in use.
        "all": [s("[nls-getptr] type=11")],
        "any": [s("[nls-getptr] type=11 id=%d " % cp) for cp in (932, 936, 949, 950, 874)],
        "none": [s("[nls-extra]")],
        "reason": "The game asked for a Windows code page whose table BCD does not load for every game and ended right after; the line "
                  "gives this game Wine's tables for the Japanese, Chinese, Korean and Thai code pages.",
        "evidence": ["sword-and-fairy-3-2026-10-22 (6FW5J5, build 580)", "UNPROVEN: the first session with the line decides"],
    },
    # ajan10/app
    {
        "id": "mfc-missing-dock",
        "lines": ["env.MADEIRA_MFC_DOCK = 1"],
        "priority": 18,
        "noRetry": True,
        "outcomes": ["crashed", "exited-early"],
        "session": "dock",
        # Horizon Zero Dawn (TH3BJ8, Steam 1151640) and Age of Empires II HD (B2NWDX, GYYAAC, MXMRJ3; 221380), builds 580, started through
        # BCD Dock: the game's process stopped within a second with c0000135 ("import_dll Library mfc140.dll / mfc140u.dll (which is needed
        # by ...exe) not found", then "loader_init Importing dlls for ...exe failed"). A Dock session runs the aarch64 explorer.exe, so the
        # VC++ overlay that links Microsoft's MFC into system32 never ran for it (WineProcessBridge.m: use_arm64ec is NO), and the x64
        # game's ntdll searches system32 (aarch64 farm) and then C:\windows\sysx64, which holds Wine's builtins and no MFC. The line links
        # the two MFC DLLs from the VC++ runtime folder into sysx64 (madeira_mfc_dock); the same game started directly (ARM64EC) has them.
        # "none": the line already ran and linked them, so the import fails for another reason. The failed process-start line keeps
        # a plug-in's quiet import failure (Moonlighter's Razer DLL) from matching: that game runs.
        "all": [s("import_dll Library mfc140", "(which is needed by"), s("loader_init Importing dlls for", "failed, status c0000135"),
                s("[madeira-dock] starting the host")],
        "none": [s("MADEIRA_MFC_DOCK=1:", "linked into sysx64")],
        "reason": "The game needs Microsoft's MFC library, and the session BCD Dock starts it in does not have it where the game looks; "
                  "the line links MFC there from the downloaded Visual C++ runtime (Settings).",
        "evidence": ["horizon-zero-dawn-mfc-dock-2026-10-22 (TH3BJ8, build 580)", "age-of-empires-ii-hd-mfc-dock-2026-10-22 (B2NWDX; GYYAAC, MXMRJ3)",
                     "tests/host/check-mfc-dock.py (build 591)"],
    },
    {
        "id": "install-script-registry-missing",
        "lines": ["env.MADEIRA_DOCK_SCRIPT_REGISTRY = 1"],
        "priority": 20,
        "noRetry": True,
        "session": "dock",
        # Fallout: New Vegas (GA6QN2, Steam 22380, build 580, through BCD Dock): FalloutNVLauncher.exe runs and shows "Fallout: New Vegas does
        # not appear to be installed and the installer could not be found. Try exploring to your DVD-ROM drive and selecting setup.exe to
        # install the game." (the box's text is logged as [msgbox-origin], an error-channel line every session prints). That is the
        # launcher's own registry check: Bethesda's games read HKLM\Software\(Wow6432Node\)Bethesda Softworks\<game>\Installed Path, which the
        # "Registry" section of the game's install script (installscript.vdf) has Valve's Windows client write. A Dock start runs Valve's client
        # in the host and never the script, and BCD's install-script feature runs only its "Run Process" entries. The line makes the app write
        # the script's missing registry values before the start (DockScriptRegistry). "none": the line is on already ([dock-script-registry]
        # is printed whenever it is), so the box has another cause.
        "all": [s("[msgbox-origin] box", "does not appear to be installed and the installer could not be found")],
        "none": [s("[dock-script-registry] app=")],
        "reason": "The game's launcher looks in the registry for the install folder that Steam's install script writes on Windows, and BCD "
                  "Dock never wrote it; the line has BCD write the script's missing registry values before the start.",
        "evidence": ["fallout-new-vegas-install-script-registry-2026-10-22 (GA6QN2, build 580)", "tests/host/check-dock-script-registry.py (build 591)"],
    },
    # ajan10/motor (build 591): engine switches (keyed, off by default) and one code fix
    {
        "id": "fault-guard-rate-redeliv",
        "lines": ["env.MADEIRA_FAULT_GUARD_RATE = 1"],
        "priority": 30,
        # Unity Mono games whose null-check traps are managed exceptions (SpiderHeck ZHVF9Y, ROUNDS A1CX12 on build 580): one host pc faults and
        # is delivered to the game's handler again and again while the game keeps drawing (44 a second at 92 fps, 460 a second at 56 fps), and
        # ml461's guard ends the process at 2000 identical deliveries in a row. With the line a run only counts as a storm when it is dense
        # (2000 within half a second) or the presenter stands still. The signals are the guard's own lines (printed whatever the diagnostic
        # switches) and the Mono runtime's module: the storms of Unity Mono games that really are storms (Cult of the Lamb QSYCSY, TEVI J75807,
        # Graveyard Keeper 2: an unemulated STLXR in the runtime's RWX page) print "[excl-store]" and have their own rule; every other storm
        # (Dead Space, NFS Underground 2, Warcraft II BNE) is not Mono. "minStage 3": the game had drawn a frame. "none": the line is already
        # on and the guard still ended a real storm (build 591 prints "[redeliv] MADEIRA_FAULT_GUARD_RATE ..." then). Red Dead Redemption 2
        # (2SR5VJ, Arxan self-checks at 27 a second, 7 fps) reaches the same kill without Mono: that game gets the line from its list.
        "all": [s("Load module mono-2.0"), s("[redeliv] 2000 identical redeliveries"), s("[redeliv] terminating process")],
        "none": [s("[excl-store]"), s("[redeliv] MADEIRA_FAULT_GUARD_RATE")],
        "minStage": 3,
        "reason": "BCD ended the game because one fault kept coming back, but the game was still drawing frames: it handles that "
                  "fault itself (a managed null check). The line makes BCD end a game only for a fast, real storm.",
        "evidence": ["spiderheck-2026-10-22 (ZHVF9Y)", "rounds-2026-10-22 (A1CX12)", "2SR5VJ (RDR2, log only)",
                     "tests/host/check-fault-guard-rate.py (build 591)"],
    },
    {
        "id": "fault-guard-rate-segv",
        "lines": ["env.MADEIRA_FAULT_GUARD_RATE = 1"],
        "priority": 30,
        # PlateUp! (21DKZW, build 580, iPad13,8): Mono's null-check traps from several threads at one pc (Mach declines them, x18 == 0) reach the
        # BSD path's guard, which counts the repeats over ALL threads, skips the instruction and sets x0 = 0xDEAD0001; seconds later the game
        # read that value as a pointer ("[av-detail] ... p1=0xdead0011") and died with c0000005. With the line the repeats are counted per
        # thread and only a run of five within 5 ms counts.
        "all": [s("SEGV LOOP FATAL", "forcing thread exit"), s("[av-detail]", "p1=0xdead00")],
        "none": [s("[segv-loop] MADEIRA_FAULT_GUARD_RATE")],
        "minStage": 2,
        "reason": "BCD took a game's own null checks for a fault loop, skipped the instruction and handed the game a made-up value, "
                  "which it then used as a pointer. The line counts the repeats per thread and only when they are fast.",
        "evidence": ["plateup-2026-10-22 (21DKZW)", "tests/host/check-fault-guard-rate.py (build 591)"],
    },
    {
        "id": "user-va-cap-reserve-256gb",
        "lines": ["env.MADEIRA_USER_VA_GB = 32"],
        "priority": 30,
        # FINAL FANTASY TACTICS - The Ivalice Chronicles (DR8613 on build 580, 7QVF78 on 579, two testers): the game asks for one NOACCESS
        # reservation of 256 GiB + 64 MiB at start ("[jumbo] kernel-pick reserve failed (0xc0000017) for size=0x4003ffffff"), gets
        # c0000017 (BCD's largest hole is 32 GiB) and calls ExitProcess(0) a few seconds in. BCD tells every process its user address
        # space is 512 GB (ullTotalVirtual 524,287 MB), which the game plausibly sizes the arena from. The line tells a 64-bit game 32 GiB
        # (only the report changes). Not proven that the game then asks for less. The signals: a failed jumbo NOACCESS reserve and the
        # process's own exit with code 0, in a session that stopped before the first frame.
        "all": [s("[jumbo] kernel-pick reserve failed (0xc0000017)"), s("[nt-fail]", "NtAllocateVirtualMemory status=c0000017", "type=0x2000 prot=0x1"),
                s("NtTerminateProcess(handle=0x0, exit_code=0x0)")],
        "none": [s("[user-va] MADEIRA_USER_VA_GB:")],
        "outcomes": ["exited-early"],
        "maxStage": 2,
        "reason": "The game asked for hundreds of gigabytes of address space, which this device cannot give, and quit. The line "
                  "tells it the device has 32 GB of address space; the request may then be smaller.",
        "evidence": ["final-fantasy-tactics-2026-10-22 (DR8613; 7QVF78 on build 579)", "tests/host/check-user-va-gb.py (build 591)"],
    },
    {
        "id": "create-view-assert",
        "lines": [],
        "fixedIn": 591,
        "priority": 1,
        # Fallout 4 (C5Y6PW, build 580): 89 s in, while FEX started a thread and asked for 16 MB, create_view found an overlapping view that
        # is not a system view and its assert ended the process ("Assertion failed: (view->protect & VPROT_SYSTEM), function create_view, file
        # virtual_ios.c", exit 0x80000101). Build 591 refuses the allocation with STATUS_CONFLICTING_ADDRESSES and logs "[create-view]"
        # with both views. No config line changes it.
        "all": [s("Assertion failed: (view->protect & VPROT_SYSTEM), function create_view")],
        "reason": "BCD's own memory manager stopped on an internal check when the game's memory use overlapped a mapping it did "
                  "not expect; from this build on it refuses that one allocation instead of ending the game.",
        "evidence": ["fallout-4-create-view-2026-10-23 (C5Y6PW)", "tests/host/check-create-view-conflict.py (build 591)"],
    },
    {
        "id": "gs-input-instance-id-assert",
        "lines": [],
        "fixedIn": 598,
        "priority": 1,
        # ROBOBEAT (Epic Games Store, Direct3D 11, build 592, iPhone18,1, Send Log HH0KNF): 8.6 s into the start, while the game's shaders were
        # converted, airconv's handle_signature_gs met a geometry shader that declares dcl_input vGSInstanceID (a Shader Model 5 [instance(N)]
        # shader) and its assert ended the process ("Assertion failed: (0 && "unhandled geometry shader input"), function handle_signature_gs,
        # file dxbc_signature.cpp, line 1082", exit 0x80000101). The converter already does the rest of geometry shader instancing (the id
        # from the threadgroup position, the grid sized by dcl_gsinstances); only the declaration was missing. Build 598 accepts it
        # (tools/patch-airconv-gs-instance-id.py) and names any other register type in a "[gs-input]" line before it asserts. Only shaders
        # that aborted before change. No config line changes it.
        "all": [s('Assertion failed: (0 && "unhandled geometry shader input")')],
        "reason": "BCD's shader converter stopped on a declaration in one of the game's geometry shaders (its instance id) and that ended "
                  "the game; from this build on it accepts that declaration.",
        "evidence": ["robobeat-gs-input-assert-2026-10-23 (HH0KNF, build 592)", "tests/host/check-airconv-gs-instance-id.py (build 598)"],
    },
    {
        "id": "anon-cas-flush",
        "lines": ["env.MADEIRA_ANON_CAS_FLUSH = 1"],
        "priority": 30,
        # Jetboard Joust (MonoGame on CoreCLR; GHVYNG, T9CQD7, RTSYP6, CTR7BV on build 580): the CLR patches code it has already run with
        # `lock cmpxchg`; build 580 emulates that CAS in the Mach handler, FEX never learns that the page changed, and ~190 lines later the
        # main thread calls one routine 11-12 million times a second (the [spin] report: "x64->EC ... go to kernelbase.dll+0x6c248") and
        # never presents. On build 579 the same CAS reached FEX, which dropped the page's translation and did the CAS itself. The line hands
        # the CAS to FEX again. Signals: an emulated CAS on an anonymous alias, and the spin report's call count in the millions.
        # Round 2026-10-24: Stardew Valley (.NET 6 on CoreCLR; 7FZXF2, build 592, iPad16,3, the same stop in BD8079, S4QB4S, XK1A2J, X99DVE, G6EEFP) has
        # exactly this signature. Its tester closed the game after 90 s of a frozen start ("frontend graceful close requested", then the Dock
        # closes BCD): the next start finds a killed run, scored `hung` because the quit came before the library did (AutoTuneEngine.outcome),
        # so this rule already covers it (fixture stardew-valley-2026-10-23, end {"killed": true}); read as a log alone, with no end facts,
        # the same session is `exited-early`. The two signals cannot be in a healthy session: a [spin] report needs a thread at 11 million
        # calls a second for 4 s, and the CAS line is the build 580 emulation this switch replaces.
        "all": [s("[anon-cas] build580 insn="), s("[spin] ml1140", "look 1: x64->EC")],
        "none": [s("[anon-cas] build591 MADEIRA_ANON_CAS_FLUSH")],
        "outcomes": ["hung"],
        "reason": "The game patches its own code and BCD completed that write without telling the emulator, which kept running "
                  "the old code in a loop. The line passes that write through the emulator, which refreshes its copy.",
        "evidence": ["jetboard-joust-livelock-2026-10-22 (GHVYNG; T9CQD7, RTSYP6, CTR7BV)", "tests/host/check-anon-cas-flush.py (build 591)",
                     "stardew-valley-2026-10-23 (7FZXF2: the same spin, the player closed the game)"],
    },
    # ---- ajan10/lists (tur591, build 591) ----
    {
        "id": "exe-window-fixed-base-large",
        "lines": ["exe-window-mb = {exeWindowMB}"],
        "priority": 6,
        "noRetry": True,
        "exeMinMB": 512,
        "exeMaxMB": 703,
        # The programs exe-window-fixed-base's 512 MB window cannot hold: Resident Evil Requiem's re9.exe is 575.07 MiB (YQRQ86, build 579:
        # the try of exe-window-mb = 512 failed unchanged; P0GY6V, build 580: the rule stays away from 512 MiB and more, and the search ended
        # "stuck"). The window is the image plus 17 MiB up to a multiple of 16 ("{exeWindowMB}": 575 -> 592, the list's number; 379 -> 400;
        # AutoTuneEngine.resolve), at most 720 MiB for an image of 703: the free run above 0x140000000 ends at the main thread's stack,
        # 751-758 MiB in the RE Requiem logs, 721 in Life is Strange 2's, 720 in Persona 4 Golden's; no window is larger. Images of 704 MiB and
        # more get nothing (no window holds them with room to spare). The same "none" as exe-window-fixed-base: not for a second instance of a
        # program whose first got the window, and not for a one-time installer's helper in %TEMP%.
        "all": [s("failed to create main module", "c0000018")],
        "none": [s("[exe-window]", "gets the window"),
                 s("failed to create main module", "\\\\Temp\\\\"),
                 s("failed to create main module", "infinst.exe")],
        "reason": "The game's program (512 MiB or more) must load at a fixed address that BCD's JIT pool partly held, and a 512 MB window is "
                  "too small for it; the line keeps a window of the program's size plus about 17 MiB there (the pool may be smaller).",
        "evidence": ["resident-evil-requiem-2026-10-22 (P0GY6V, build 580: a 575 MiB program; YQRQ86 tried 512 on build 579)",
                     "persona-4-golden-2026-10-21 (QK7BQP: 888 MiB, no window holds it; no match)",
                     "tests/host/autotune_engine_checks.swift (window sizes)"],
    },
    {
        "id": "dock-ceg-result-55",
        "lines": [],
        "noFix": True,
        "session": "dock",
        "priority": 5,
        # Call of Duty: Black Ops (YFWA6Z, build 580, Madeira Dock): Valve's client could not prepare the executable that Steam builds per account
        # (CEG): "[dock-report] ceg-service-registered=1", "ceg-server-result=22", "ceg-job-result=55", "ceg-result=55", "launch-host-result=49", and
        # the client's own service_log.txt ends "Failed to create Service pipe (GLE 2)". The game never ran; build 580 ends the session and says
        # so, but auto-tune answered with the unchanged retry ("tried retry: reverted"), a start for a failure no setting changes
        # (tur577 ajan6-ceg.md: 55 is not fixed).
        # Only from build 580, which ends such a session itself ("[dock-game-end]" or the return to the library): a 579 log of the same failure
        # keeps dock-launch-failed-session-open's "fixed in 580: update BCD" (Spec Ops: The Line 2FRXXM, NEXHHM; Risen 2 9DBDZ6).
        "all": [s("[dock-report] ceg-result=55")],
        "any": [s("[dock-game-end]"), s("[frontend] returned to library")],
        "reason": "Valve's copy protection (CEG) could not prepare this game's program: the Steam client in BCD Dock reported result 55 and the "
                  "game never ran. It is not a game setting, and starting again gives the same result.",
        "evidence": ["call-of-duty-black-ops-ceg-55-2026-10-22 (YFWA6Z, build 580)", "DG6N4E (same failure on a second device)"],
    },
    {
        "id": "unity-gc-stopped-thread-context",
        "lines": ["env.MADEIRA_CTX_FRAME = 1"],
        "priority": 30,
        # Unity games (IL2CPP: No Rest for the Wicked, Megabonk, Vampire Survivors, Lonely Mountains, 1000xRESIST, Tape to Tape; Mono: Subnautica, Oxygen Not
        # Included) that play for a while and stop in the object header with an odd low bit ("[badrip] ... guest RIP=0x98000000.... is not a canonical user
        # address", a virtual call through the first word of a freed object: the Boehm collector's free-list link). 31 logs of 2026-10-21..24 (builds 576-592),
        # and in all of them the stop comes right after a stop-the-world: the collector's NtGetContextThread calls on its stopped threads ("[ec-getctx] ml715"
        # in a burst, one per thread), 12 to 36 lines before in 22 logs, 68 to 212 before in 8 of No Rest for the Wicked's 9 (GGFKAY L40808 -> L40884,
        # RKZSCQ L62675 -> L62769). Every answer is the thread's Mach-O state ("is_ec=0", native_pc in libsystem_kernel's wait, rsp a host pthread stack),
        # not its x64 registers and stack: ntdll's wrapper converts the native context unconditionally, and the server's saved AMD64 context is empty
        # ("[srv-getctx] ml715 ... saved_amd64 ... rip=0x0"). A collector that scans a stopped thread from such a context misses the roots only that
        # thread holds. env.MADEIRA_CTX_FRAME = 1 (build/wineserver/mach_ios.c, ml716; never run on a device until this rule) makes the server report a thread
        # parked in a system call by its saved call frame; the server prints "[ctx-frame] ml716" for each. An attempt, not proven: the correlation is
        # strong, the switch is old and untested. The crash itself is the signal ("[badrip]" is printed only by the failure path); the burst lines say the
        # game stopped threads and was told their Mach-O state.
        "all": [s("[badrip] rev=ajan2-tani", "is not a canonical user address"), s("[ec-getctx] ml715", "is_ec=0", count=3)],
        "none": [s("[madeira-env] game MADEIRA_CTX_FRAME=1")],
        "outcomes": ["crashed"],
        "reason": "The game's memory manager stopped its threads and was handed their system state instead of their registers and stack, then the "
                  "game used an object it had freed; the line makes BCD report a stopped thread by its saved call frame.",
        "evidence": ["no-rest-for-the-wicked-2026-10-23 (RKZSCQ, GGFKAY, build 592)", "31 logs of eight Unity titles, builds 576-592 (the burst, then the stop)",
                     "megabonk 2FYRC6 (build 592: the same order with a smaller game)"],
    },
    {
        "id": "game-mandatory-aslr-box",
        "lines": [],
        "noFix": True,
        "priority": 3,
        # Demon Slayer -Kimetsu no Yaiba- The Hinokami Chronicles 2 (APK2, a 435 MiB Unreal program with its relocations stripped; KG3A3G, build 592,
        # iPhone17,1; N4GR8A, PPV8SW, 4V6S9J, iPhone16,2): the Shipping program starts at its own base address (0x140000000, "ml985: map_image_view
        # ... base=0x140000000 info_base=0x140000000 map_addr=0"), creates no Direct3D device and puts up a message box with the title "Error" and the text
        # "Disable Mandatory ASLR" (an exploit-protection setting of Windows). The box is the first window of the program (PPV8SW L2879-L2882), nothing
        # is presented, the game waits for OK. Auto-tune spent its starts on the window size (exe-window-mb = 512) and on a program image without a pool
        # copy (X64_IMAGE_NOCOPY) before and after; neither changed the box. The log does not say which check the game makes (no mitigation-policy
        # call is logged: GetProcessMitigationPolicy is a stub that logs a fixme, and there is none), so the cause is open.
        "all": [s("[win-name] #", "Disable Mandatory ASLR")],
        "reason": "The game stopped with its own message \"Disable Mandatory ASLR\" before it opened a window. It is a check in the game's start-up; "
                  "BCD does not know yet what it looks at, and no setting changes it.",
        "evidence": ["demon-slayer-hinokami-chronicles-2-2026-10-23 (PPV8SW, build 592; KG3A3G, N4GR8A, 4V6S9J: the same box on two devices)"],
    },
    {
        "id": "d2r-loader-empty-code-view",
        "lines": ["env.MADEIRA_NTDLL_XSEC = 1", "env.MADEIRA_SHARED_SECTION_EXEC = 1", "env.MADEIRA_NTDLL_DATA_SYNC_RANGE = 1",
                  "env.MADEIRA_FAULT_LOOP_GUEST_PROGRESS = 1", "env.MADEIRA_D2R_PROBE_RECOVERY = 1"],
        "priority": 3,
        # Diablo II: Resurrected (D2R.exe with D2R_loader.dll; bcd-games issue #4; T844FE, XADA1A, build 592, iPhone17,1; sfc.dll, sfc_os.dll and tbs.dll load,
        # so the imports of the first stop are past, though this tester's own file carries no OPTIN_DLLS line): the loader's TLS callback maps ONE section handle twice, two views of 1 MiB side by side
        # (0x7037C20000 and 0x7037D20000; "NtMapViewOfSection handle=0000000000000064 ... size=0000000000100000", T844FE L9989 and L9991), BCD turns the
        # first into JIT-pool pages ("[jit-copy] ml634 #5 PRE-REMAP ... 0/1048576 bytes nonzero", "[jit-pool] anon RWX 0x7037c20000", L9985-L9988) and the
        # notification that tells FEX about executable memory skips both views (a data section: "[map-notify] SKIP (no ArbitraryUserPointer)", L9990 and
        # L9992). The loader then jumps 0x3948 into the first view: FEX answers NOEXEC ("[iOS-xquery] MISS ... addr=0x7037c23948", L10232) and the page is
        # zeros in all three of its views ("[smc-gen] ml636 #2 TAIL VERDICT: highest written 0x0 of 0x100000 - NEVER WRITTEN AT ALL", L10413), so the game
        # gets an execute fault at 0x7037c23948 and "D2R_loader.dll failed to initialize" (L10441). Nothing in the log shows a write to the second view, which
        # is where a loader that maps a section twice puts its code (a write view and a run view); that part is inferred. The same shape is what .NET's
        # write-xor-execute mapping does (list lines with DOTNET_EnableWriteXorExecute = 0). Build 598 (thebcd69/bcd-games#4 by mckenna654): the view that is
        # made executable keeps its shared memory (env.MADEIRA_SHARED_SECTION_EXEC = 1, "[guest-section]") and ntdll-xsec.dll tells the emulator about it
        # (env.MADEIRA_NTDLL_XSEC = 1, "[section-notify]"); the other three lines are the reporter's later findings with the same loader (a sub-page
        # protect of ntdll's .data zeroed live callbacks, a run of breakpoints at different guest addresses was ended as a stuck thread, the emulator's
        # call-target probe leaked a native pc when the target page was unreadable). An attempt, not proven: no device ran these lines, and the
        # reporter's last run ended later still (BlizzardError after an inaccessible code page). The stop itself is the signal: both lines of "all"
        # are printed by the failure paths only.
        "all": [s("loader_init \"D2R_loader.dll\" failed to initialize"), s("NEVER WRITTEN AT ALL")],
        "none": [s("[madeira-env] game MADEIRA_SHARED_SECTION_EXEC=1")],
        "reason": "The game's loader maps one block of memory twice (one view to write its code, one to run it) and jumps into the running view while "
                  "it is still empty; the lines keep the two views one block and tell the emulator that the running one holds code.",
        "evidence": ["diablo-ii-resurrected-2026-10-24 (T844FE, XADA1A: the same session twice)", "bcd-games issue #4 (the reporter's run stopped in D2R_loader.dll's start)",
                     "bcd-games#4 by mckenna654 (the five switches, host checks; device runs of the author's own builds)"],
    },
    {
        "id": "metal-texture-double-release",
        "lines": [],
        "noFix": True,
        "priority": 2,
        # Mina the Hollower (639V00, AZM8XT, build 580): an iOS thread dies in libobjc's objc_release under Metal's completion queue
        # (IOGPUMetalCommandBufferStorageReset), "[mach-deliver] ... NATIVE-THREAD DECLINE pc=... (libobjc.A.dylib`objc_release_x8)", and the log
        # names the object ("x0=... was FREED by our NSObject_release: class AGXG16PFamilyTexture, 50 releases ago"): BCD's winemetal released a
        # texture the GPU's completion callback released again. No game exit code, the app is gone, and auto-tune scored the run "killed" and
        # offered swap files (footprint 2-3.4 GB, far from the limit; three starts spent, 639V00 L29). The cause is open (motor: record who
        # frees what, guard a release that would go below zero); the rule keeps the swap lines away meanwhile.
        "all": [s("NATIVE-THREAD DECLINE", "objc_release")],
        "reason": "An iOS thread released a Metal object that was already freed, and the app ended; this is an open bug in BCD, not a setting "
                  "(a swap file does not help: the game used about 3 GB of the memory it may have).",
        "evidence": ["mina-the-hollower-metal-release-2026-10-22 (639V00, build 580)", "AZM8XT (same game, same phone: three starts spent on swap)"],
    },
    {
        "id": "mf-null-video-off",
        "lines": ["env.MADEIRA_WG_VIDEO = 0"],
        "priority": 20,
        "outcomes": ["crashed"],
        # Albino Lullaby (SF3P8T, 41D565, build 580, Madeira Dock): the 32-bit game plays its intro MP4 through Media Foundation, and a thread
        # writes the NULL page inside Wine's mf.dll (guest eip 0x771a89e0..771a8ac3 = mf.dll+0x18ac3, deterministic in both logs):
        # "wine: Unhandled page fault on write access to 00000000 at address 771A8AC3", "[wow-null32] ... (mf.dll+0x26159)". MADEIRA_WG_VIDEO = 0
        # limits the media parser to MP3 and WAV, so the movie is refused instead of played. An attempt, not a fix: no device has tried it.
        "all": [s("Unhandled page fault on write access to 00000000"), s("[wow-null32]", "(mf.dll+0x")],
        "none": [s("[madeira-env] game MADEIRA_WG_VIDEO=0")],
        "reason": "The game crashed in Media Foundation (mf.dll) while playing a video; the line stops BCD's media parser from decoding MP4 video "
                  "so the game's movie is refused instead of played.",
        "evidence": ["albino-lullaby-mf-null-2026-10-22 (SF3P8T, build 580)", "41D565 (second session, same address)"],
    },
    {
        "id": "sc-pa-pools-missing",
        "lines": ["env.MADEIRA_SC_PA_POOLS = 2"],
        "priority": 8,
        # Grand Theft Auto IV through the Rockstar Launcher (J1J40D, 3JTHRV, build 580, 512 GB map): the Launcher's Social Club browser asks for
        # PartitionAlloc's glued 32 GB pools and BCD says "without env.MADEIRA_SC_PA_POOLS=1 there is none to give", SocialClubHelper.exe
        # exits 0x80000003, the Launcher logs "Browser subprocess no longer running" and respawns it every 5 s until the pool is full. GTA V's
        # and RDR2's lists carry layout 2. Only a 512 GB map: layout 2 needs 28 GB of space the 63 GB and 454 GB maps do not have (there the
        # message is printed too, with the switch in force, and the switch changes nothing: G2G7WA, 4X7N94).
        "all": [s("[sc-cef] SocialClubHelper.exe asks for PartitionAlloc's glued 32 GB pools", "without env.MADEIRA_SC_PA_POOLS=1"),
                s("[device] address-map=", "512GB")],
        "none": [s("[madeira-env] game MADEIRA_SC_PA_POOLS=")],
        "reason": "The Rockstar Launcher's Social Club browser needs address space for its memory pools and BCD keeps none free by default; the "
                  "line reserves it, as for Grand Theft Auto V.",
        "evidence": ["grand-theft-auto-iv-sc-pa-pools-2026-10-22 (J1J40D, build 580)", "3JTHRV"],
    },
    {
        "id": "java-zgc-off",
        "lines": ["env._JAVA_OPTIONS = -XX:-UseZGC"],
        "priority": 20,
        # Project Zomboid (GCN1GC, build 580): its JVM runs ZGC, which reserves its heap at 4 TiB and up ("[jumbo#1] ... size=0x7cda00000
        # (31962 MB) hint=0x40000000000 -> 0x0 st=0xc0000018", then 12,230 refused reservations up to 8 TiB) and the process exits 1; BCD's map
        # ends at 512 GB. _JAVA_OPTIONS is read after the command line, so -XX:-UseZGC turns the collector off and the default one reserves
        # its heap low. An attempt: the JVM's own choice afterwards is not shown by any log.
        "all": [s("[jumbo#1]", "hint=0x40000000000", "st=0xc0000018"), s("jvm.dll")],
        "none": [s("[madeira-env] game _JAVA_OPTIONS=")],
        "reason": "The game's Java runtime uses a collector (ZGC) that needs address space at 4 TiB, which this device does not have; the "
                  "line switches that collector off so the runtime reserves its memory low.",
        "evidence": ["project-zomboid-zgc-2026-10-22 (GCN1GC, build 580)"],
    },
    # lead/smr-wbi (2026-10-23)
    {
        "id": "d3d12-write-buffer-immediate-spider-man",
        "lines": ["write-buffer-immediate = 1"],
        "priority": 6,
        "api": "d3d12",
        "noRetry": True,
        # Marvel's Spider-Man Remastered, build 592, the owner's iPhone18,2 (five logs, 2026-10-23 12:37-13:51): a black screen after the
        # launcher. Every run stops at the same command (queue-trace #89) with the game's "Render Thread" at 100 % of a core, calling
        # GetDeviceRemovedReason ~38 million times a second ("[xp-api-top] ... d3d12.dll+0x4e9c4") while it waits for a value; just
        # before, the game's first WriteBufferImmediate reached the stub ("unimplemented: ID3D12GraphicsCommandList7::WriteBufferImmediate").
        # gpu-sync = 1 let the GPU finish all its work and changed nothing. write-buffer-immediate = 1 (opt-in, build 593) writes the
        # values. The owner (2026-10-23): no other game may see any change, so the rule is this game's alone (its session log's name).
        "all": [s("unimplemented: ID3D12GraphicsCommandList7::WriteBufferImmediate")],
        "any": [s("Session log: logs/Spider-Man.exe-"), s("library launch Spider-Man.exe:")],
        "none": [s("write-buffer-immediate = 1 (opt-in)")],
        "reason": "The game waits for a value it asks the GPU to write (WriteBufferImmediate), which BCD did not write; the line "
                  "turns that write on for this game.",
        "evidence": ["spider-man-remastered-wbi-2026-10-23 (owner, build 592)",
                     "tests/host/check-write-buffer-immediate.py (build 593)"],
    },
    # ajan11/wow64stack ------------------------------------------------------------------------------------------------------------------
    {
        "id": "wow-stack-freed-by-other-process",
        "lines": [],
        "fixedIn": 593,
        "priority": 2,
        # Battle.net (build 592, iPhone18,2): its Chromium helpers are 32-bit processes that lay their 8 MB thread stacks out at the
        # same guest addresses. virtual_free_teb released a dead thread's 32-bit stack at guest + the window of the thread doing the
        # freeing, and exit_thread hands the previous exiting thread's TEB to the next exiting thread, of whatever process: one helper's
        # worker exiting deleted (2 s later, the delayed-release ring) the live stack of another helper's IO thread. The log shows the
        # 8 MB view of a 32-bit window DELETED by a thread of ANOTHER process some seconds before the thread faulted on it
        # ("history: view 0x7705dd0000+0x800000 protect=0x23 DELETED by tid 027c ... ms ago"), PROT_NONE pages that never materialise, 2000
        # identical faults and "[redeliv] terminating process" (the whole app). Build 593 releases the stack in its own window and ends a
        # child process alone when one storms. No config line changes either.
        "all": [s("[redeliv] terminating process"), s("[fault-rgn]   history: view 0x", "+0x800000 protect=0x23 DELETED by tid"),
                s("[wow-window] slot", "bound to peb=")],
        "none": [s("[wow-stack-free]")],
        "reason": "A 32-bit helper process of the program had its thread's stack released by another process's thread exiting, and the app ended "
                  "when the thread kept running on it.",
        "evidence": ["battlenet-network-service-stack-2026-10-23 (build 592, explorer.exe session)"],
    },
    {
        "id": "syscall-protection-fault-uncaught",
        "lines": [],
        "fixedIn": 593,
        "priority": 2,
        # EA app installer (build 592, iPhone18,2, Windows desktop): the 32-bit installer probes its own memory with ReadProcessMemory (self,
        # the lowest page of its thread stack's 8 MB view, which is reserved and never committed, by design) and expects the call to fail,
        # as IsBadReadPtr does. NtReadVirtualMemory's __TRY/__EXCEPT is the way to fail it; Darwin reports a protection fault as SIGBUS and
        # bus_handler never asked handle_syscall_fault for the jmp_buf (only segv_handler did), so the memmove in the system call became a guest
        # exception on the kernel stack: "call_seh_handlers invalid frame", "Exception frame is not in stack limits", NtTerminateProcess with a
        # garbage code, two installer processes gone, the UI on "something is downloading". Build 593 returns to the __EXCEPT block. No line.
        "all": [s("[mach_exc] sym pc=", "_platform_memmove", "NtReadVirtualMemory"), s("NtRaiseException Exception frame is not in stack limits")],
        "none": [s("[syscall-fault]")],
        "reason": "A program probed memory it may not read with ReadProcessMemory; the call should simply have failed, but BCD ended the program's "
                  "process instead.",
        "evidence": ["ea-app-installer-read-virtual-memory-2026-10-23 (build 592, Windows desktop session)"],
    },
    # lead/wow-reap (2026-10-23)
    {
        "id": "wow-window-held-by-exited-process-threads",
        "lines": [],
        "fixedIn": 593,
        "priority": 3,
        # Battle.net (build 592, the owner's iPhone18,2, Windows desktop): from t+395 s Battle.net.exe started its Agent every 3 s and
        # every start failed: "[wow-window] ... REJECTED: the slot is still held by an exited process whose teardown did not complete"
        # (two of the windows) and "BOOT FAILED at stage 'guest-window-reserve'". A worker thread of an exited helper kept running in
        # translated code (99 % of a core), so the live-thread guard kept its window. Build 593 ends such leftover threads, where they
        # hold no host lock, when a 32-bit process would otherwise get no window ("[wow-reap]" lines). No config line changes it.
        "all": [s("REJECTED: the slot is still held by an exited process whose teardown did not complete"),
                s("BOOT FAILED at stage 'guest-window-reserve'")],
        "none": [s("[wow-reap]")],
        "reason": "A 32-bit program could not start because threads of programs that had already exited still held their memory "
                  "windows; from this build BCD ends those leftover threads and gives the windows back.",
        "evidence": ["battlenet-agent-window-slots-2026-10-23 (build 592, explorer.exe session)", "tests/host/check-wow-reap.py (build 593)"],
    },
    {
        "id": "wow-storm-end-worker-teb",
        "lines": [],
        "fixedIn": 594,
        "priority": 2,
        # Battle.net, build 593 (the owner's Wine desktop session, 16:23): a storm in a helper took the child-only path, and its worker thread
        # wrote an [fdtrace] line with GetCurrentThreadId(), which read NULL+0x48 on a thread without a TEB and ended the whole app
        # ("Madeira`ios_fdt_note_close" under "ios_storm_end_thread"). Build 594 writes thread id 0 there. No line changes it.
        "all": [s("[redeliv] ending only the Windows process"), s("Madeira`ios_fdt_note_close")],
        "reason": "A helper program of the launcher went wrong and BCD meant to close only that program, but the closing step itself ended "
                  "the app; from this build it closes only the helper.",
        "evidence": ["battlenet-storm-end-parked-threads-2026-10-23 (build 593)", "tests/host/check-wow-reap.py (build 594)"],
    },
    {
        "id": "wow-window-held-by-unterminable-threads",
        "lines": [],
        "fixedIn": 594,
        "priority": 3,
        # Build 593's [wow-reap] could not end the leftover threads of exited 32-bit processes: iOS refused thread_terminate and left them
        # suspended ("[wow-reap] ... left suspended"), their windows stayed held and 32-bit programs still failed at "guest-window-reserve".
        # Build 594 counts a suspended thread of an exited process as parked, and its window is reclaimed. No line changes it.
        "all": [s("[wow-reap]", "left suspended"), s("BOOT FAILED at stage 'guest-window-reserve'")],
        "none": [s("parked, suspended for good")],
        "reason": "A 32-bit program could not start because threads of programs that had exited still held their memory windows and "
                  "could not be ended; from this build BCD parks them and gives the windows back.",
        "evidence": ["battlenet-storm-end-parked-threads-2026-10-23 (build 593)", "tests/host/check-wow-reap.py (build 594)"],
    },
    # ajan19 (log round 2026-10-24: GTA, RDR2, Halo, Control, Hitman) ---------------------------------------------------------------------
    {
        "id": "easy-anti-cheat-launch-error",
        "lines": [],
        "noFix": True,
        "priority": 2,
        # Halo: The Master Chief Collection (B4874F build 592 iPad16,10; ER8CRF and NEKWXD build 592 iPhone18,4; earlier NNGJSX 573, VJSK5K,
        # F1RNEM): mcclauncher.exe starts Easy Anti-Cheat, whose client reads the environment and ends the launch with its own box, "Easy
        # Anti-Cheat Launch Error" over "Failed to initialize Wine helper buffer", and the process exits with 0xeac9 (a few seconds in, before
        # any game window). Armored Core VI shows the same boxes. It is the anti-cheat refusing to run here, in every session, whatever the
        # settings: no line helps (the first log of the round was tried "once more" with nothing changed and failed the same) and BCD does
        # not touch an anti-cheat (Dock README: "No interaction with anti-cheat"). The signals are the box's text and the exit code, which every
        # session prints.
        "all": [s("Easy Anti-Cheat Launch Error"), s("exit_code=0xeac9")],
        "reason": "The game starts Easy Anti-Cheat, and it refuses to run here (\"Failed to initialize Wine helper buffer\"). No setting changes "
                  "that, and BCD does not work around an anti-cheat. If the game offers a start without Easy Anti-Cheat (an offline mode), use that.",
        "evidence": ["halo-mcc-easy-anti-cheat-2026-10-23 (B4874F, build 592; ER8CRF and NEKWXD the same on iPhone18,4)",
                     "analysis/ajan3-motor.md (VJSK5K, Armored Core VI: confirmed Easy Anti-Cheat, no bypass)"],
    },
    {
        "id": "rdr2-reserve-28gb-totalphys",
        "lines": ["totalphys = 8191"],
        "priority": 6,
        "noRetry": True,
        # Red Dead Redemption 2 on a 16 GB iPad (P8H75J and 91C75Y, build 592, iPad17,4, M5, 365-day profile with the memory rights): the
        # game's own 64-bit RDR2.exe asks for ONE MEM_RESERVE of 0x700000000 = 28,672 MB ("[jumbo#80] ... wtid=03e4 ... size=0x700000000
        # (28672 MB) ... st=0xc0000017", "[nt-fail] ... proc=rdr2.exe ... size=0x700000000"), BCD's map has no such hole (largest gap 3-12 GB),
        # RDR2 shows its own box (here German, "Fehler"; the English "Red Dead Redemption 2 exited unexpectedly!") and ends on an int3 in
        # RDR2.exe (exit 0x80000003). The size follows what BCD tells the game about physical memory ("ml991: => ullTotalPhys=16136646656
        # (15389 MB)"): every RDR2 log with 7455 MB (iPad16,3: 8960 MB asked, st=0x0), 6143 MB (iPhone17,2) or less reserves the 8960 MB the
        # list's MADEIRA_RDR2_VA_HOLD keeps free, and the iPhone18,2 the owner plays on reports 8191 MB (the jetsam clamp, ml992). Only a device
        # that reports 15389 MB asks 28,672 MB (also 1Z27N7, build 579, iPad17,3). `totalphys` (bcd.cfg, ml1095) makes BCD report that
        # many MB; 8191 is the owner's own number. Fires only after the failed 28,672 MB reserve of rdr2.exe, which a session that plays never
        # shows, and only for RDR2.exe. The third log (1Z27N7, build 579, RDR2.exe started by hand on an iPad17,3) ends at the box with no exit
        # code, so the exit is not a signal.
        "all": [s("[nt-fail]", "NtAllocateVirtualMemory status=c0000017", "proc=rdr2.exe", "size=0x700000000")],
        "none": [s("ml993: totalphys ->")],
        "outcomes": ["crashed"],
        "reason": "RDR2 asked for 28 GB of address space, sized from the 16 GB of memory this iPad reports, and BCD has no such room. The line "
                  "reports 8191 MB, as the iPhones RDR2 runs on do; the game then asks for the 9 GB the list keeps free.",
        "evidence": ["rdr2-reserve-28gb-2026-10-23 (P8H75J and 91C75Y, build 592, iPad17,4; 1Z27N7 build 579 iPad17,3)",
                     "the 8 RDR2 logs that reach the game's own reserve: 5 at 6143/7455 MB (NYKYVY, 7PC4Q2, 791FKD, BCDYF7, X00VX2) ask 8960 MB and "
                     "get it, 3 at 15389 MB (1Z27N7, P8H75J, 91C75Y) ask 28672 MB and fail",
                     "build/ntdll-unix/virtual_ios.c ml992/ml993 (totalphys)"],
    },
    {
        "id": "framegen-interpolator-assert",
        "lines": [],
        "noFix": True,
        "priority": 3,
        # GTA V Enhanced (Steam, Dock), build 592, iPhone18,3 (A19, 7.6 GB), one tester, three logs: the game's page had Frame generation
        # (experimental, env.MADEIRA_FRAMEGEN = 1) and MetalFX upscaling 2x on a 2622x1206 desktop, so the drawable was 5244x2412
        # ("[metal-hud] ... first drawable 5244x2412 px"). "[framegen] interpolator ready: 5244x2412" (and 3072x1440 for the launcher's
        # window) was printed 63 to 862 times, and the process died on the first frames: Metal's own assertion "Assertion failed:
        # (MAX(srcSize->width >> *srcMip, 1UL) == MAX(dstSize->width >> *dstMip, 1UL) ..." (03K0PX, S2B3GE) or, one step earlier in the
        # driver, a fault in AGXMetalG18P followed by "failed assertion `commit command buffer with uncommitted encoder'" (AYMMDC), and
        # BCD's abort 0x80000101. The same game on the same build with the picture at 960x540, no MetalFX and no frame generation played
        # (VQ3K21), and neither assertion is in any of the other 700 logs. The signals are the interpolator's ready line, which every
        # session with frame generation prints, and one of the two assertions.
        "all": [s("[framegen] interpolator ready:")],
        "any": [s("Assertion failed: (MAX(srcSize->width >> *srcMip"),
                s("failed assertion `commit command buffer with uncommitted encoder'")],
        "outcomes": ["crashed"],
        "reason": "Frame generation (experimental) was on with a very large picture (MetalFX upscaling on the screen's own resolution) and the "
                  "graphics driver stopped the game. Turn Frame generation off in the game's settings, or use a lower resolution without MetalFX "
                  "upscaling, then start the game again.",
        "evidence": ["gta-v-enhanced-framegen-assert-2026-10-23 (S2B3GE, 03K0PX; AYMMDC the driver variant), build 592, iPhone18,3",
                     "VQ3K21 (build 592, iPhone18,2, 960x540, no frame generation): played"],
    },
    {
        "id": "gta5-enhanced-started-without-launcher",
        "lines": [],
        "noFix": True,
        "priority": 3,
        # GTA V Enhanced started from the program GTA5_Enhanced.exe itself (a non-Steam copy, FNVAQJ, GQ2P52, QS7HRE, build 592, iPhone16,2,
        # three starts in a minute): the game loads its DLLs (DSTORAGE, bink2w64 ...), raises an invalid instruction (c000001d at its own
        # code) and a single-step trap, and calls NtTerminateProcess(0xdeadc0de) 3 seconds in; no window, no launcher process. The Rockstar
        # Games Launcher normally starts the game and hands it its session; started by hand the game protects itself and ends this way. The
        # signal is the library start of GTA5_Enhanced.exe together with that exit code, which no session through PlayGTAV.exe shows.
        "all": [s("[bcd] library launch GTA5_Enhanced.exe"), s("exit_code=0xdeadc0de")],
        "outcomes": ["crashed"],
        "reason": "GTA V Enhanced was started from GTA5_Enhanced.exe and ended itself a few seconds in (exit 0xDEADC0DE). The game needs "
                  "Rockstar's launcher to start it: start PlayGTAV.exe (or the game from your library) instead.",
        "evidence": ["gta5-enhanced-direct-start-2026-10-24 (FNVAQJ, GQ2P52, QS7HRE, build 592, iPhone16,2)"],
    },
    {
        "id": "runonce-stale-waiter-keyed-event",
        "lines": ["env.MADEIRA_KEYED_EVENT_PER_PROCESS = 1"],
        "priority": 5,
        "noRetry": True,
        # Control (Steam 870780; X49WBQ, QJAQK3, P0DERY, 6AG46N, build 592, iPhone16,2; four logs, one crash) and Slay the Spire 2 (GZFVM3,
        # build 579, iPhone19,2): a worker thread (Control: Wwise's "AK::EventManager", 45 to 60 s in, right after it loaded mmdevapi.dll and
        # while the main thread loaded xaudio2/mfplat) dies on "wine: Unhandled page fault on read access to A90453F3D10183FF" (Slay the
        # Spire 2: E9010000000887C6) at the ARM64EC ntdll's +0x68008 (`ldr x19,[x1]`), exit c0000005. The code there is Wine's RtlRunOnceComplete
        # walking the waiters of an InitOnce: each node is a stack slot of a thread that sleeps in NtWaitForKeyedEvent( 0, ... ) until
        # NtReleaseKeyedEvent( 0, ... ) wakes it. The "address" is 8 bytes of a function prologue (Control) or of x86 code (Slay the Spire 2), the
        # content of a stack slot the waiter had left. In Control's register dump the release before the fault had returned x0 = c0000024,
        # STATUS_OBJECT_TYPE_MISMATCH: a NULL keyed-event handle names Wine's process-global `keyed_event`, created once, for the first
        # pseudo-process; every later one (a game started by the Dock's host is one) has another handle table, so the wait returned at once, the
        # waiter spun and left its frame, and the release woke nobody. Build 598 with the line: every child process makes its own keyed event
        # (log: "[keyed-event] ml2200: peb=... own keyed event"). The offset is the ntdll.dll of builds 579 to 597; a rebuilt ntdll may move it.
        "all": [s("[mach_exc] pc PE: ntdll.dll+0x68008"), s("Unhandled page fault on read access to")],
        "none": [s("[madeira-env] game MADEIRA_KEYED_EVENT_PER_PROCESS=1"), s("[keyed-event] ml2200:")],
        "outcomes": ["crashed"],
        "reason": "Two threads of the game raced to run the same one-time initialisation and the one that waited never slept, because a program "
                  "started by another one had no keyed event of its own; the line gives every such program its own.",
        "evidence": ["control-runonce-keyed-event-2026-10-23 (X49WBQ and three more, build 592)",
                     "slay-the-spire-2-runonce-keyed-event-2026-10-22 (GZFVM3, build 579)",
                     "tests/host/check-keyed-event-per-process.py (build 598)"],
    },
    # ajan38/tur600 ---------------------------------------------------------------------------------------------------------------------
    {
        "id": "chakracore-cfg-policy-failfast",
        "lines": ["env.MADEIRA_ZERO_MITIGATION_POLICY = 1"],
        "priority": 5,
        "noRetry": True,
        "maxStage": 2,
        # Glyphica: Typing Survival (Steam 2400160, neosphere.exe, the Sphere engine neoSphere with ChakraCore.dll; 777YHV and JAHWGD, two logs of one
        # start, build 592, iPad16,3): exit c0000602 (STATUS_FAIL_FAST_EXCEPTION) 0.8 s after Wine starts, "crashed at stage 2 after 2 s", before the
        # game's own code runs. The stack (the unwinder's lines of the termination) is ChakraCore.dll's static constructor GlobalSecurityPolicy
        # (lib/Common/Core/GlobalSecurityPolicy.cpp): it loads api-ms-win-core-memory-l1-1-3.dll (ChakraCore's own data shows that name and the
        # handle of kernelbase.dll), calls GetProcessMitigationPolicy( ProcessControlFlowGuardPolicy = 7, local, 4 ) and reads
        # EnableControlFlowGuard from a local it never cleared. Wine's stub answers TRUE and writes nothing (the log: "fixme:process:
        # GetProcessMitigationPolicy (FFFFFFFFFFFFFFFF, 7, <stack>, 4): stub", then, the next line, "Unhandled exception code c0000602"), so the bit is
        # whatever the stack held; set, ChakraCore looks up SetProcessValidCallTargets (not exported by this Wine's kernelbase) and calls
        # RaiseFailFastException( NULL, NULL, FAIL_FAST_GENERATE_EXCEPTION_ADDRESS ). Build 600 with the line: the stub zeroes the structure, no
        # mitigation is enabled, ChakraCore takes its non-CFG path (log: "[madeira-env] game MADEIRA_ZERO_MITIGATION_POLICY=1"). The fixme line is not a
        # signal (a quiet log may drop it); the unwinder's ChakraCore frames and the exit are. Not proven on a device. A fail-fast of ChakraCore at stage
        # 3 or later is another failure and does not match.
        "all": [s("Unhandled exception code c0000602"), s("[unwind] term", "ChakraCore.dll+0x")],
        "none": [s("[madeira-env] game MADEIRA_ZERO_MITIGATION_POLICY=1")],
        "outcomes": ["crashed"],
        "reason": "The game's script engine (ChakraCore) stopped the program while it started, because Windows' answer to its question about "
                  "security features was never filled in; the line makes the answer empty.",
        "evidence": ["glyphica-chakracore-failfast-2026-10-24 (777YHV and JAHWGD, build 592)",
                     "tests/host/check-zero-mitigation-policy.py (build 600)"],
    },
    # ajan12/gta5l ----------------------------------------------------------------------------------------------------------------------
    {
        "id": "gpu-reset-lower-graphics-settings",
        "lines": [],
        "noFix": True,
        "priority": 25,
        # A GPU recovery in one of the heavy games: the GPU stops answering, iOS resets it, and every command buffer in flight comes back with
        # "kIOGPUCommandBufferCallbackErrorInnocentVictim" (DXMT's "Device error at frame N: ..." for Direct3D 9/10/11, madeira-d3d12's
        # "ml1049 COMMAND BUFFER ENDED IN ERROR: ..." for Direct3D 12; both carry Metal's text). GTA V Legacy at North Yankton (a Discord tester's
        # iPhone18,2, builds 574-592, and the owner's build 546 start): the tester's in-game graphics settings were all Very High (Post FX too);
        # set lower, the game played (Discord, 2026-10-23 17:23). ajan12's env.MADEIRA_DXMT_FAULT_INFO = 1 did not help him (Q70SZ2), so no line
        # is added: auto-tune stops and tells the player to set every graphics option to its lowest value (owner: not Normal, or MSAA stays on).
        # Owner, same day: the same advice for GTA V Enhanced, RDR2, Ghost of Tsushima, God of War, both Horizons, the Spider-Man games and the
        # other heavy titles, from Steam, Epic or anywhere else: they are recognised by their program's name, in a library session's log name
        # ("Session log: logs/<exe>-"), a Steam game's Dock line ("[session-log] Steam game <exe>:") or the library title ("library launch
        # <title>:"). Any other game never matches.
        "all": [s("kIOGPUCommandBufferCallbackErrorInnocentVictim", count=4)],
        "any": [s("Session log: logs/" + exe + "-") for exe in HEAVY_GAME_PROGRAMS] + [s("Steam game " + exe + ":") for exe in HEAVY_GAME_PROGRAMS]
               + [s("library launch " + title + ":") for title in HEAVY_GAME_TITLES],
        "reason": "The phone's GPU was reset while the game was drawing: the game's own graphics settings are too high. Set every option "
                  "in the game's Graphics menu to its lowest value (MSAA and reflection MSAA off, Post FX, shadows, grass and all the rest "
                  "at the minimum).",
        "evidence": ["gta-v-legacy-gpu-reset-2026-10-23 (Discord tester, build 592; Very High settings, lower ones played)",
                     "gta-v-legacy-2026-10-15-12-35-46 (owner, build 546: the same recovery at Story Mode)",
                     "tests/host/check-gta5l-gpu-reset.py"],
    },
    # lead/sberrix, ajan15/d3d9-x64 (2026-10-23) --------------------------------------------------------------------------------------
    {
        "id": "d3d9-64bit-dxmt",
        "lines": ["env.MADEIRA_D3D9_X64 = dxmt"],
        "priority": 8,
        "noRetry": True,
        # A 64-bit Direct3D 9 game: a 64-bit program loads Wine's own d3d9.dll from sysx64, which draws through wined3d's OpenGL backend, and
        # BCD has no OpenGL: "wined3d_adapter_gl_init Failed to get a GL context", "[d3d9-probe] ... Direct3DCreate9 called -> d3d9_init
        # FAILED". The game then says what it guesses (Darksiders II Deathinitive Edition, Steam 388410: "Could not initialize the graphics
        # subsystem, probably because the resolution 1112 x 832 is not supported for fullscreen display"; a Discord tester's iPad16,5, build
        # 592, Send Log 3F3TYX; earlier PICO PARK). Build 595 ships d3d9-dxmt.dll, DXMT's Direct3D 9 frontend (the one the 32-bit games run as
        # d3d9-emulated.dll) compiled for ARM64EC (tools/build-d3d9-x64-dll.sh); the line makes it the game's d3d9.dll.
        # The two log lines alone are NOT enough: 78 of the 950 logs of the intake have the failed probe, and most of them are other games
        # (Cult of the Lamb, Counter-Strike, Graveyard Keeper, Moonlighter and Trails in the Sky play fine after it, on Direct3D 11; the Steam
        # overlay or a launcher probes; No Man's Sky, World of Goo and Buckshot Roulette fail on OpenGL or Vulkan; 32-bit games such as Peggle
        # and Hotline Miami have a 64-bit helper that probes). So the rule also needs the game's own graphics error box ("any": the box of
        # Darksiders II; more wordings join with the logs that show them), and "none" the markers of a 32-bit session ([wow-window], wow64cpu:
        # a 32-bit game has DXMT's i386 shim as d3d9.dll and never fails on wined3d). On the 950 logs only the two Darksiders II logs (builds
        # 576 and 592) match. noRetry: the failure is the same on every start. "none": the bridge's link line, so a session that ran with the
        # line gets no second try and d3d9-64bit-needs-opengl below tells what is left.
        "all": [s("wined3d_adapter_gl_init Failed to get a GL context"), s("Direct3DCreate9 called -> d3d9_init FAILED")],
        # Round 2026-10-24: Counter-Strike: Source's 64-bit program (cstrike_win64.exe, Steam 240, 38S4C1, build 592, iPad17,2) has no error box: the Source
        # engine says it through OutputDebugString ("Failed to create D3D9!." and "System (VMaterialSystem082) failed during stage CONNECTION.") and
        # exits with code 0. The first wording joins the box texts below; it is in one log of the 1,232 of the intake, the one that matches.
        "any": [s("[win-name] #", "graphics subsystem"), s("[win-name] #", "Direct3D"), s("[win-name] #", "DirectX 9"), s("[win-name] #", "D3D9"), s("OutputDebugStringA: \"Failed to create D3D9!.\"")],
        "none": [s("MADEIRA_D3D9_X64=dxmt: d3d9.dll -> d3d9-dxmt.dll"), s("[wow-window]"), s("wow64cpu")],
        "reason": "This 64-bit Direct3D 9 game ran on Wine's Direct3D 9, which needs OpenGL, and the phone has none. The line gives it BCD's "
                  "Direct3D 9 for 64-bit games (DXMT, drawing through Metal).",
        "evidence": ["darksiders-2-d3d9-64bit-2026-10-23 (Discord tester, build 592, iPad16,5, Send Log 3F3TYX)", "tests/host/check-d3d9-x64.py (build 595)",
                     "docs/OPENGL.md", "counter-strike-source-d3d9-64bit-2026-10-23 (38S4C1, build 592, iPad17,2)"],
    },
    {
        "id": "d3d9-64bit-needs-opengl",
        "lines": [],
        "noFix": True,
        "priority": 25,
        # The same failure with the line above already in force: the bridge linked d3d9-dxmt.dll as d3d9.dll (its "[WineProc] MADEIRA_D3D9_X64=dxmt:
        # d3d9.dll -> d3d9-dxmt.dll" line) and the session still ended in Wine's d3d9 and wined3d's GL init, so the game loads a Direct3D 9 from
        # somewhere else than the farm. No setting changes that today (docs/OPENGL.md: OpenGL is the other way). A 32-bit game never reaches wined3d's
        # GL init. Without the bridge's line the rule above matches first, so this text shows only once the line has run.
        "all": [s("wined3d_adapter_gl_init Failed to get a GL context"), s("Direct3DCreate9 called -> d3d9_init FAILED"),
                s("MADEIRA_D3D9_X64=dxmt: d3d9.dll -> d3d9-dxmt.dll")],
        "reason": "BCD's Direct3D 9 for 64-bit games was in use for this game, but it still ended in Wine's Direct3D 9, which needs OpenGL: "
                  "the game loads its Direct3D 9 from somewhere else. Its message about the screen resolution is a guess; no setting changes it today.",
        "evidence": ["darksiders-2-d3d9-64bit-line-ran-2026-10-23 (the first fixture with the line's two log lines added; the engine's reading of a session where the line did not take)",
                     "docs/OPENGL.md"],
    },
    # ajan23/loglar (2026-10-24) ------------------------------------------------------------------------------------------------------
    {
        "id": "d3d9-64bit-dxmt-crash",
        "lines": ["env.MADEIRA_D3D9_X64 = dxmt"],
        "priority": 9,
        "noRetry": True,
        "outcomes": ["crashed"],
        # The same 64-bit Direct3D 9 failure as the rule above, for two games that do not show an error box: they use the NULL result of
        # Direct3DCreate9 and crash. MudRunner (Steam 675010, build 580, iPhone15,5, Send Log 96X0F0): d3d9.dll is a static import, Wine's
        # wined3d finds no OpenGL ("Failed to get a GL context", "d3d9_init FAILED"), and MudRunner.exe+0x7e1d24 reads `mov rax,[r14]` with
        # r14 = 0 (c0000005). Indivisible (Steam 421170, build 592, iPhone17,4, Send Log YE8TT0): the same two lines, then a call through a
        # NULL function pointer at Indivisible.exe+0x31c429 ("AV EXEC of 0"). Neither game has ever started on a phone. The rule is these
        # two games' alone ("any": their session-log and launch lines): the failed probe is in 78 of the 950 logs and most of those games
        # play on Direct3D 11 afterwards, so the two Wine lines alone prove nothing. "none": a 32-bit session and the bridge's link line, as
        # in the rule above. UNPROVEN on these two games: the first session with the line decides (Darksiders II Deathinitive plays on it).
        "all": [s("wined3d_adapter_gl_init Failed to get a GL context"), s("Direct3DCreate9 called -> d3d9_init FAILED")],
        "any": [s("[session-log] Steam game MudRunner.exe:"), s("library launch MudRunner:"),
                s("[session-log] Steam game Indivisible.exe:"), s("library launch Indivisible:")],
        "none": [s("MADEIRA_D3D9_X64=dxmt: d3d9.dll -> d3d9-dxmt.dll"), s("[wow-window]"), s("wow64cpu")],
        "reason": "This 64-bit Direct3D 9 game ran on Wine's Direct3D 9, which needs OpenGL, and the phone has none; the game then used the "
                  "empty result and crashed. The line gives it BCD's Direct3D 9 for 64-bit games (DXMT, drawing through Metal).",
        "evidence": ["mudrunner-d3d9-64bit-2026-10-23 (96X0F0, build 580)", "indivisible-d3d9-64bit-2026-10-23 (YE8TT0, build 592)",
                     "tests/host/check-d3d9-x64.py (build 595)", "docs/OPENGL.md"],
    },
    {
        "id": "nls-extra-codepage-dock-sekiro",
        "lines": ["env.MADEIRA_NLS_EXTRA = 1"],
        "priority": 61,
        "noRetry": True,
        "session": "dock",
        "outcomes": ["crashed"],
        # Sekiro: Shadows Die Twice (Steam 814380, BCD Dock; Send Logs 51Y0RM and 9Y7G8T, build 592, iPad17,3; ZGH9MT and A7V9T2 before): about
        # 20 s into the start the game asks twice for the Japanese ANSI code page ("[nls-getptr] type=11 id=932"; BCD's nls folder has no
        # c_932.nls), the next line is a C++ exception ("[exc] ml811 code=e06d7363"), nothing catches it and the runtime aborts with
        # __fastfail(7) at sekiro.exe+0x23a6cc4 ("[fast-fail] ml604 code=7", exit 0xc0000409). Likely a Shift-JIS locale or conversion that
        # throws when the code page is missing (the same chain, with code page 850, ends Ghost of Tsushima). The line serves Wine's c_932 ...
        # (build 591). The general rule nls-extra-codepage leaves Dock sessions out, because Steam's own client asks for 932 in every Dock
        # session and runs without the table; here the tables become visible to the client too, which is accepted for a game that has never
        # run (every Sekiro log fails the same way). The rule is Sekiro's alone ("any": its session-log and launch lines). UNPROVEN: the first
        # session with the line decides. "none": the tables were already in use.
        "all": [s("[nls-getptr] type=11 id=932"), s("[exc] ml811 code=e06d7363"), s("[fast-fail] ml604 code=7")],
        "any": [s("[session-log] Steam game sekiro.exe:"), s("library launch Sekiro:")],
        "none": [s("[nls-extra]")],
        "reason": "The game asked for the Japanese code page, whose table BCD does not load for every game, and ended on an unhandled "
                  "exception right after; the line gives this game Wine's tables for the Japanese, Chinese, Korean and Thai code pages.",
        "evidence": ["sekiro-nls-932-2026-10-23 (51Y0RM and 9Y7G8T, build 592)", "ZGH9MT and A7V9T2 (tur591 C.md T5)",
                     "UNPROVEN: the first session with the line decides"],
    },
    # lead/launchers-desktop (2026-10-23)
    # lead (2026-10-24): Rockstar's launcher of the Epic GTA V / RDR2 ------------------------------------------------------------------
    {
        "id": "rockstar-launcher-epic-steam-identity",
        "lines": ["env.MADEIRA_STEAM_IDENTITY = none"],
        "priority": 6,
        "noRetry": True,
        # Every Epic start of Rockstar's launcher in the testers' logs (GTA V Legacy YZZFYA, DJAMF2, VX8ZZ0, FJ62YC, KRSMZ8, QTDJYC, 457SEM;
        # RDR2 6XG0C2; build 592) carried the fixed Steam identity BCD publishes outside a Dock session ("[iOS env] INCLUDED:
        # SteamAppId=356400") and stopped with "LAUNCHER_ERR_STEAM_FAILED" (113, "Steam failed to initialize"): the launcher took
        # itself for the Steam version. The line publishes no Steam identity for that game (WineProcessBridge.m). Only an Epic session
        # ("[epic-launch] start") with that error matches; a Steam or Dock start of the same launcher never does.
        "all": [s("LAUNCHER_ERR_STEAM_FAILED"), s("[epic-launch] start"), s("INCLUDED: SteamAppId=")],
        "none": [s("MADEIRA_STEAM_IDENTITY=none")],
        "reason": "Rockstar's launcher of the Epic version took itself for the Steam version, because BCD gave it a Steam game identity; "
                  "the line gives this game none.",
        "evidence": ["rockstar-launcher-epic-steam-identity-2026-10-23 (Discord tester, GTA V Legacy from Epic, build 592, Send Log YZZFYA)"],
    },
    {
        "id": "launcher-without-windows-services",
        "lines": [],
        "fixedIn": 594,
        "priority": 4,
        # Battle.net started on its own (build 593's launcher card, log 16:12): with no services.exe, Battle.net's AgentHelper --install found no
        # service manager ("\\pipe\\svcctl" not found, RPC 0x6ba), crashed, the Agent quit and the login window never drew (presents=0 for
        # 3 minutes). Build 594's launcher card starts Battle.net and the EA app in a Wine desktop with Windows services first. A program
        # added by hand can already do it on its game page (Launch: In a Wine desktop, Start Windows services first).
        "all": [s("AgentHelper.exe", "--install"), s("pipe\\svcctl")],
        # lead (2026-10-23, ajan16's note): a build 594 session with services up matched too (its only svcctl failure is at t+1.3 s,
        # before services.exe was spawned), so a session that started services.exe is not this case.
        "none": [s("[phase] spawn:", "services.exe")],
        "reason": "Battle.net needs Windows' services, and BCD started it without them; from this build the launcher starts in a Wine "
                  "desktop with services (or set Launch: In a Wine desktop and Start Windows services first on its game page).",
        "evidence": ["battlenet-direct-no-services-2026-10-23 (build 593)", "tests/host/check-bcd-launchers.py (build 594)"],
    },
    {
        "id": "eaapp-installer-turkish-locale",
        "lines": ["env.LC_ALL = en_US.UTF-8"],
        "priority": 5,
        "noRetry": True,
        # EA app installer, build 593 (the owner's Windows desktop, logs 16:28 and 17:09:52): with the phone's Turkish locale its bootstrapper
        # loaded code page 1254 and died on an uncaught ATL::CAtlException right after its telemetry step (error box C7-2F79). With
        # env.LC_ALL = en_US.UTF-8 it went on to the MSI package (17:15:17). Build 594's launcher card sets the line for the EA app; this
        # rule covers the installer started by hand. Tied to EAappInstaller.exe.
        "all": [s("CAtlException@ATL"), s("[nls-getptr] type=11 id=1254"), s("EAappInstaller.exe")],
        "none": [s("[madeira-env] game LC_ALL=")],
        "reason": "The EA app's installer fails with the phone's Turkish language settings; the line gives it an English locale.",
        "evidence": ["eaapp-installer-turkish-locale-2026-10-23 (build 593)", "tests/host/check-bcd-launchers.py (build 594)"],
    },
    # ajan14/ea-mono (2026-10-23) ------------------------------------------------------------------------------------------------------
    {
        "id": "eaapp-installer-msi-pipe-answer-lost",
        "lines": ["env.MADEIRA_APC_BUSY_QUEUE = 1"],
        "priority": 6,
        "noRetry": True,
        # EA app installer, build 593 (the owner's Windows desktop, log 17:15:17, with the English locale and msiexec in system32): the
        # bundle got to its MSI package and the package stopped at its first custom action, "JunoInitializeSession" (return value 3, "Execution
        # halted, action ... returned 1603", the bundle's INST-14-1603 / 0x80070643). The action is a WiX DTF managed custom action: msiexec's
        # custom action server (syswow64\msiexec.exe -Embedding) runs SfxCA.dll, which starts rundll32.exe, which hosts Wine Mono and asks the
        # server for every MSI call over an overlapped named pipe (request, then a wait for the answer). The rundll32 process has one thread
        # until the CLR starts; the server's first answer arrived while that thread was running, not waiting, so BCD's own Windows server had
        # no thread to give the read's completion to and dropped it ("[apc-requeue] #4 ... status=00000101 ... -> dropped, no waiting thread"):
        # the read ended with STATUS_ALERTED and no data, SfxCA closed the pipe as broken (the server's read: STATUS_PIPE_BROKEN, c000014b),
        # and every later MSI call of the action read its answer through a NULL pointer (SfxCA's Log at msif*.tmp+0x409C, the managed
        # RemotableNativeMethods.ReadInt: NullReferenceException in Mono, "wine: Unhandled page fault on write access to 00000004") and the
        # process ended with c0000005. Not Wine Mono, not FEX. With env.MADEIRA_APC_BUSY_QUEUE = 1 (build 594) the server keeps such a
        # completion on its thread, which runs it at its next wait; the launcher card sets the line for the EA app, this rule covers an installer
        # started by hand. The line is only added after the failure; a program that never drops that completion does not meet it.
        "all": [s("Execution halted, action L\"JunoInitializeSession\" returned 1603")],
        "none": [s("[madeira-env] game MADEIRA_APC_BUSY_QUEUE=1")],
        "reason": "The EA app's installer lost an answer between its setup steps; the line keeps that answer until the installer is ready for it.",
        "evidence": ["eaapp-installer-msi-pipe-answer-lost-2026-10-23 (build 593, log 17:15:17)", "tests/host/check-apc-busy-queue.py (build 594)",
                     "tests/host/check-bcd-launchers.py (build 594)"],
    },
    # ajan16/bnet-apc (2026-10-23) ------------------------------------------------------------------------------------------------------
    {
        "id": "wow64-native-iosb-completion-store",
        "lines": [],
        "fixedIn": 596,
        "priority": 3,
        # Battle.net, build 594 (the owner's iPhone18,2, launcher card, log 19:22:01): the updated Agent (Agent.9824\Agent.exe) and AgentHelper.exe,
        # both 32-bit CONSOLE programs, faulted 8 and 10 times in the first seconds: a WRITE at 0x10004 / 0x10000 from Madeira`invoke_system_apc
        # (+0x57c, `STR W22,[X19,#4]` = IO_STATUS_BLOCK32.Information) under server_select <- server_wait_for_object <- NtWriteFile, then the
        # redelivery guard's "SEGV LOOP FATAL" skipped the instruction. The stored value (0x8e, 0x45) is the byte count of rpmalloc's first
        # two band-selector lines, written by FEX's own WriteFile in libwow64fex.dll with an uninitialised 64-bit IO_STATUS_BLOCK: native
        # code inside the 32-bit process, no guest involved. ntdll takes every call of a 32-bit process for a thunk's and read the status
        # block's first 8 bytes (stale stack, 0x10000) as the address of the guest's IO_STATUS_BLOCK32. The Agent survived (it listened and
        # Battle.net connected), so this is not what keeps Battle.net's window on its spinner (its renderer helpers die on a Chromium int3, a
        # separate cause). Build 596 stores a completion's status block only where it can land and logs "[iosb-dead]" for the one it skips.
        # No config line changes it; MADEIRA_IOSB_DEAD_TARGET=0 only brings the faults back.
        "all": [s("[mach_exc] sym pc=", "invoke_system_apc")],
        "any": [s("[lowalloc] ml966 REFUSED guest 0x1"), s("[av-detail]", "p0=WRITE p1=0x1")],
        "none": [s("[iosb-dead]")],
        "reason": "A 32-bit console program of the launcher (Battle.net's Agent) wrote a result into memory that does not exist and BCD "
                  "recovered by repeating the fault; from this build BCD skips that write.",
        "evidence": ["battlenet-agent-console-iosb-2026-10-23 (build 594, iPhone18,2)", "tests/host/check-iosb-dead-target.py (build 596)"],
    },
    # ajan18/bnet-cef (2026-10-23) ------------------------------------------------------------------------------------------------------
    {
        "id": "chromium-v8-flags-freeze-writecopy",
        "lines": ["env.MADEIRA_PROTECT_WRITECOPY_AS_RW = 1"],
        "priority": 3,
        "noRetry": True,
        # Battle.net, builds 594 and 596 (the owner's iPhone18,2, launcher card): the login window spun for ever; Battle.net's own log said
        # "Render process was terminated and reloaded automatically" every 5 s and stopped after 10. The renderers are `Battle.net.exe
        # --type=renderer`, 32-bit Chromium 108.0.5359.125 (Blizzard's own libcef.dll), and 9 of 12 ended on an int3 at libcef.dll+0x16D00E1
        # ("[int3-guest] guest_rip=0x6de800e2", exit 0x80000003), 0.1 to 0.3 s after "GpuMemoryThread" started. That address is V8's
        # v8::base::OS::SetDataReadOnly: V8::Initialize freezes v8_flags with VirtualProtect( PAGE_READONLY, &old ) and CHECKs old ==
        # PAGE_READWRITE. VirtualProtect succeeded (EAX = 1); BCD's NtProtectVirtualMemory returned the page's recorded protection, PAGE_WRITECOPY
        # (a writable image section, never updated because a store to it raises no fault here). Windows reports PAGE_READWRITE once the program
        # has written the page. With env.MADEIRA_PROTECT_WRITECOPY_AS_RW = 1 (build 597) a 32-bit program that write-protects such a page is
        # told PAGE_READWRITE (the value only; "[wc-old]" in the log shows the old and the reported value). The launcher card sets the line for Battle.net itself; this rule covers a
        # Battle.net started by hand (a plain library entry). Only the renderer can meet it: the GPU and service processes never start V8.
        # The signals are lines every session prints: the renderer's command line, its int3 report and the 0x80000003 exit.
        "all": [s("--type=renderer"), s("Load module libcef.dll"), s("[int3-guest] guest_rip="), s("exit_code=0x80000003")],
        "none": [s("[madeira-env] game MADEIRA_PROTECT_WRITECOPY_AS_RW=1")],
        "reason": "Chromium's JavaScript engine in the launcher's browser asks Windows for a page's old protection when it freezes its flags and ends "
                  "the process with an int3 when it is told the wrong one; the line makes BCD tell it what Windows does.",
        "evidence": ["battlenet-renderer-v8-flags-freeze-2026-10-23 (build 596, iPhone18,2)", "tests/host/check-wc-old-prot.py (build 597)",
                     "tests/host/check-int3-x86-diag.py (build 597)"],
    },
    # 2026-10-24 (main, build 598) ------------------------------------------------------------------------------------------------------
    {
        "id": "explorer-startmenu-user-folder-missing",
        "lines": [],
        "fixedIn": 598,
        "priority": 3,
        # Battle.net, build 597 (the owner's iPhone, launcher card, 2026-10-24): the sign-in page showed (the renderer fix worked), the
        # e-mail went in, and at the password step the whole session ended after 126 s. The root process, Wine's explorer.exe (the prebuilt
        # aarch64 desktop of every session), read address 0 at 0x1400192cc: fill_menu enumerating the user's half of the Start menu,
        # whose folder was NULL. The template ships Start Menu\Programs for its own user only (users/mythic); the registry maps the
        # user's Start Menu to %USERPROFILE%, which for "mobile" had none, while the public half had entries (BCD's own and Battle.net's),
        # so the Start button reached that path. From build 598 the session makes the user's Start Menu\Programs with the other profile
        # folders ("[shell-dir] user folders for ... (Start Menu included)"). No config line changes it.
        "all": [s("Unhandled page fault on read access to 0000000000000000 at address 00000001400192CC")],
        "none": [s("(Start Menu included)")],
        "reason": "The Windows desktop's Start menu had no folder for the user's own entries and ended the session when it opened; "
                  "from this build BCD makes that folder.",
        "evidence": ["explorer-startmenu-user-folder-2026-10-24 (build 597, Battle.net sign-in)", "tests/host/check-bcd-startmenu.py (build 598)"],
    },
    # ajan20 (2026-10-24) ---------------------------------------------------------------------------------------------------------------
    {
        "id": "d3d9-native-null-call",
        "lines": ["env.MADEIRA_D3D9 = emulated"],
        "priority": 6,
        "noRetry": True,
        "outcomes": ["crashed"],
        # Resident Evil 6 (Steam 221040, bh6.exe; builds 579 and 592) and Resident Evil Revelations 2 (Steam 287290, rerev2.exe; build 592),
        # both 32-bit Direct3D 9 games, with Settings > Graphics > "Direct3D 9 (32-bit games)" on "Native ARM64" (bcd.cfg d3d9 = native, log line
        # "D3D9 frontend: MADEIRA_D3D9=native via bcd.cfg d3d9"): the game draws its first frame (the native frontend's census prints
        # "present=1", 6849 and 2131 crossings) and 2 to 4 s later its main thread CALLS ADDRESS 0 through a thunk ("[thunk-slot] WEDGE pc=0x0",
        # x2=0x36, `blr x4` with x4 = 0; FEX: "CompileBlock: REFUSING low/invalid RIP=0x0"; "Unhandled exception code c0000005 flags 0 addr 0x0").
        # The same word 0x36 and the same call site were in Portal's crash (build 579, round 580 N2). The standard frontend, "emulated" (the shim
        # forwards to d3d9-emulated.dll, DXMT's frontend translated by FEX like the game), is what every other 32-bit Direct3D 9 game runs;
        # Resident Evil 6 plays on it at 60 FPS (round 591 B: 211 presents at 30 s, 2023 at 60 s). env.MADEIRA_D3D9 is read by the i386
        # d3d9.dll shim from the process environment; a game's env line is exported after the app's own d3d9 setting and wins. The rule needs
        # the native frontend on in this very log, so no session on the standard frontend can ever match it.
        "all": [s("D3D9 frontend: MADEIRA_D3D9=native via bcd.cfg d3d9"), s("Unhandled exception code c0000005 flags 0 addr 0x0")],
        "none": [s("[madeira-env] game MADEIRA_D3D9=emulated")],
        "reason": "The game's Direct3D 9 was set to the native ARM64 frontend (Settings > Graphics) and the game ended by calling address 0 through "
                  "it a few seconds after its first frame; the line gives this game the standard, emulated frontend.",
        "evidence": ["resident-evil-6-d3d9-native-2026-10-23 (TPATQR, build 592, iPhone18,3)",
                     "resident-evil-revelations-2-d3d9-native-2026-10-23 (04G226, build 592, iPhone18,2)",
                     "round 580 C N5 (RE6 DZ4AZR, E9W05E), round 591 B (RE6 on the standard frontend plays at 60 FPS)"],
    },
    {
        "id": "d3d12-pso-cached-blob-fatal",
        "lines": ["d3d12-pso-cached-blob = 1"],
        "priority": 12,
        "noRetry": True,
        "outcomes": ["crashed"],
        # Resident Evil Requiem's demo (Steam 4459100, re9demo.exe; E952YC, SAG071, 0FEAMR, PTSY5G, build 592, iPhone18,3): 20 s in, after the
        # first two pipelines (the config-rect pair) the engine calls ID3D12PipelineState::GetCachedBlob, the generated stub answers E_NOTIMPL
        # ("[madeira-d3d12] unimplemented: ID3D12PipelineState::GetCachedBlob") and in the same instant the process calls an unimplemented
        # comctl32 ordinal ("wine: Call from ... to unimplemented function COMCTL32.dll.345, aborting": the engine's fatal-error dialog; No Man's
        # Sky shows the same stub line after its Vulkan failure) and ends with 0x80000100. The runtime reports D3D12_FEATURE_SHADER_CACHE as
        # SINGLE_PSO, which promises the call. The 37 other D3D12 sessions of the logs (GTA V Enhanced, RDR2, Ghost of Tsushima, Horizon Zero
        # Dawn, Spider-Man: Miles Morales, ...) never make it, so the default stays; d3d12-pso-cached-blob = 1 (madeira_d3d12.c, build 598;
        # tests/host/check-d3d12-pso-cached-blob.py) answers a 16-byte blob. Not proven on a device. The comctl32 stub line is the discriminator:
        # the GetCachedBlob stub line alone does not say the game stopped on it.
        "all": [s("[madeira-d3d12] unimplemented: ID3D12PipelineState::GetCachedBlob"),
                s("to unimplemented function COMCTL32.dll.345")],
        "none": [s("[madeira-d3d12] d3d12-pso-cached-blob = 1")],
        "reason": "The game asks Direct3D 12 for a pipeline's cached blob, which BCD did not answer, and its error dialog (a Windows "
                  "function BCD lacks) ended it; the line makes BCD answer the call.",
        "evidence": ["resident-evil-requiem-demo-getcachedblob-2026-10-23 (SAG071, build 592, iPhone18,3)", "tests/host/check-d3d12-pso-cached-blob.py (build 598)"],
    },
    {
        "id": "wow64-atom-class-rebased",
        "lines": [],
        "fixedIn": 598,
        "priority": 1,
        # Bioshock Remastered (BioshockHD.exe, library entry; DW1G97, build 592, iPhone18,1) and LEGO Batman 2 (Epic install; XJ7SDS, build 592,
        # iPhone15,2) ended 1.5 s after the start, before any window existed, with exit code 0x696d696c. Both 32-bit programs (SteamStub-style
        # self-modifying code; the "Handled self-modifying code" lines before it are normal) called CreateWindowEx with a class ATOM (the guest
        # value 0x8002). wow64win converted that value like a pointer (guest window base + 0x8002 = 0x7100008002), so win32u's IS_INTRESOURCE()
        # test failed and NtUserCreateWindowEx's debug string of the class ran `ldrh w9,[x8],#2` (wine_dbgstr_wn, instruction 0x78402509) over
        # the reserved low megabyte of the guest window: a fault on the unix stack of a system call, which the SEH dispatcher refuses to
        # deliver ("Exception frame is not in stack limits => unable to dispatch exception"), and the process ended with a garbage status.
        # Need For Speed The Run (0WJTA3, build 579) died the same way at the atom 0xC023 (round 580 left it open: the thunk that adds the base is in
        # aarch64-windows/wow64win.dll, a prebuilt binary no build step compiles). Build 598 gives an atom back on win32u's unix side, which every build
        # compiles: build/win32u-unix/class_ios.c ios_atom_arg() (a value inside [B+1, B+0xffff] of the 32-bit process's window base B is the atom) is
        # called by get_int_atom_value and by dlls/win32u/window.c (NtUserCreateWindowEx's class, Get/Set/RemoveProp); MADEIRA_ATOM_ARG=0 turns it off
        # (tests/host/check-wow64win-atoms.py). The wow64win source is fixed too (Wine 73bfab6be4f) but reaches a device only when that DLL is rebuilt.
        # The instruction word is the build-592..597 binary's; a later build does not print it.
        "all": [s("[av-detail]", "p0=READ", "hinsn=0x78402509"), s("Exception frame is not in stack limits => unable to dispatch exception")],
        "reason": "The game created its first window with a class given as a number (an atom) and BCD's 32-bit conversion treated the number as "
                  "a memory address; the next build gives such a number back as the number.",
        "evidence": ["bioshock-remastered-createwindow-atom-2026-10-23 (DW1G97, build 592, iPhone18,1)", "LEGO Batman 2 XJ7SDS (build 592, the same host function and exit code)",
                     "Need For Speed The Run 0WJTA3 (build 579, atom 0xC023)",
                     "tests/host/check-wow64win-atoms.py (build 598)"],
    },
    {
        "id": "exe-too-large-for-fixed-window",
        "lines": [],
        "noFix": True,
        "priority": 5,
        "exeMinMB": 704,
        # Persona 4 Golden (Steam 1113000, P4G.exe; ten logs from one iPhone14,3 (5.6 GB), builds 579, 580 and 592, six separate sessions
        # 2026-10-21..23): the main program is 0x378b2000 bytes = 888 MiB with its relocations stripped ("ml985: map_image_view size=0x378b2000
        # base=0x140000000 ... charact=0x23", "preferred base 0x140000000+0x378b2000 REFUSED status=0xc0000018", "wine: failed to create main
        # module ... P4G.exe, status c0000018"), so it can only load at 0x140000000, and the free run above that address ends at the main
        # thread's stack, which iOS places once per launch between 0x16b8a0000 and 0x16fa98000 in these logs (697 to 763 MiB from 0x140000000;
        # "ml1135: the placeholder ended at the mapping 0x16d83c000 (VM tag 30, prot 0)"; in 2AXZ1C a malloc region at 0x15bd00000 ends it at
        # 445 MiB). No window the settings can ask for is larger
        # (exe-window-mb is capped there, and the rules above stay away from 704 MiB and more). The session ends in the first second and
        # nothing a player sets changes it; two of the ten logs (profile=none) also lack the memory entitlement, which does not matter here.
        "all": [s("failed to create main module", "c0000018")],
        "none": [s("[exe-window]", "gets the window"),
                 s("failed to create main module", "\\\\Temp\\\\"),
                 s("failed to create main module", "infinst.exe")],
        "reason": "The game's program is one file of 704 MB or more that must load at a fixed address, and the part of the address space iOS leaves "
                  "free there is smaller (at most about 760 MB). No setting changes that, so BCD cannot run this game on this device yet.",
        "evidence": ["persona-4-golden-too-large-exe-2026-10-23 (2AXZ1C, build 592, iPhone14,3)", "round 580 C N1 (2F8ZK6, 9AMZKP, QK7BQP)",
                     "persona-4-golden-2026-10-21 (QK7BQP: 888 MiB, the rules above do not match)"],
    },
    {
        "id": "epic-host-start-lost",
        "lines": ["env.BCD_EPIC_HOST_START = 1"],
        "priority": 14,
        "noRetry": True,
        "outcomes": ["exited-early"],
        # BioShock Infinite: Complete Edition from the Epic store (eight logs of one iPhone18,3, build 592, 2026-10-23/24; the exe is linked to
        # EOSSDK-Win32-Shipping.dll) ends itself with ExitProcess(1) 1.5-3 s after the start ("NtTerminateProcess ... exit_code=0x1" after the game's
        # own "Shutdown handler: cleanup."), with no window, no exception and no message. BCD chose BCD Host for the start ("[epic-launch] start ...
        # signed-in=1 ... host=1") but the session's first program was the game itself with Epic's public arguments only and "[bcd-host] no report"
        # followed: LibraryEntry.applyEnvironment() ran configureLaunch() again on the launch worker and put the game's program back. The same "no
        # report" is in all twelve Epic sessions of the logs. env.BCD_EPIC_HOST_START = 1 (build 598, BCDEpicStart.swift; tests/host/check-epic-host-hold.py)
        # holds the host's values and sets them again, so the host asks Epic for the exchange code and starts the game with the full launcher arguments.
        # An inference: an EOS game that gets no sign-in may well leave at once; not proven on a device. The rule needs the EOS SDK in the process,
        # an exit code 1, the app's own "host=1" start line and the empty report, so it cannot match a game that was started by the host.
        "all": [s("Load module EOSSDK-Win32-Shipping.dll"), s("NtTerminateProcess(handle=0xffffffffffffffff, exit_code=0x1)"),
                s("[epic-launch] start", "host=1"), s("[bcd-host] no report")],
        "none": [s("[epic-launch] host start held")],
        "reason": "The Epic game uses Epic's online services and left at once; BCD meant to start it through its sign-in helper but did not, "
                  "and the line makes the start go through it.",
        "evidence": ["bioshock-infinite-epic-host-lost-2026-10-24 (4Q0RNC, build 592, iPhone18,3)", "tests/host/check-epic-host-hold.py (build 598)"],
    },
    # ajan21/loglar (2026-10-24) --------------------------------------------------------------------------------------------------------
    {
        "id": "game-file-truncated-import",
        "lines": [],
        "noFix": True,
        "priority": 3,
        "outcomes": ["crashed"],
        # Subnautica: Below Zero (a tester's iPhone16,2, build 592, Send Logs 2CPQ3Z and ZVHPH8, 2026-10-24): the program ended 0.5 s after the loader
        # started, status 0xc0000135. The loader's own lines name the cause: "Could not map L"\\??\\C:\\Program Files (x86)\\Subnautica Below Zero\\
        # UnityPlayer.dll" section .text, file probably truncated", then "[img-fmt] ... reject" and "Loading library UnityPlayer.dll ... failed
        # (error c000007b)" (STATUS_INVALID_IMAGE_FORMAT): the DLL on the phone is shorter than its own section table says, so a copy of the game is
        # incomplete. Nothing in BCD maps half a file, and no setting changes that. The line appears in 2 of the ~1,000 logs of the intake, both this
        # game. The process must also have died at the import ("Importing dlls for ... failed, status c0000135"), which a working session never
        # prints, so no game that runs is touched; priority 3 keeps the other c0000135 rules (opt-in DLLs) from spending attempts on it.
        "all": [s("Could not map", "file probably truncated"), s("Importing dlls for", "failed, status c0000135")],
        "reason": "A file of the game is incomplete: the log line \"Could not map ... file probably truncated\" names it (Subnautica: Below Zero, "
                  "UnityPlayer.dll). The program cannot start with half a file. Copy or download the game again so that every file is whole.",
        "evidence": ["subnautica-below-zero-truncated-unityplayer-2026-10-24 (build 592, iPhone16,2, Send Logs 2CPQ3Z and ZVHPH8)"],
    },
    {
        "id": "subnautica-2-steam-client-missing",
        "lines": [],
        "noFix": True,
        "priority": 24,
        "outcomes": ["crashed"],
        "session": "library",
        # Subnautica 2 (Steam 1962700, Unreal Engine 5) started as a plain library program, without Valve's client (a tester's iPhone16,2, build 592,
        # four starts in 5 minutes, Send Logs 89THM6, 0F0JDZ, E1D865/TCPKGM and TYBE3S/XYR5V0, 2026-10-23): the game's Steam module says "Steam API
        # failed to initialize!" and "SteamUtils() failed!" (and the same for the other eight interfaces), and then the game thread reads the NULL
        # interface: Subnautica2-Win64-Shipping.exe+0x81dae6b is `call [rip+..]; mov rcx,[rax]; mov rax,[rcx]; call [rax+0xd0]; test al,al` with
        # RCX = 0 (the bytes read like Steam's inline SteamUtils() accessor and a virtual call on it; inferred, the game has no symbols here), in all
        # three sessions that got that far (89THM6 L30287, 0F0JDZ L30064, E1D865 L30466: the same RVA, each run's own image base). Unreal's crash handler then
        # takes the other threads down (OodleDataCompression "shutting down while in use?"), the CRT error box opens, and the process ends. A
        # session that ran through Madeira Dock (R01CY6, 2026-10-21, a 12 GB phone: "[session-log] Steam game Subnautica2.exe") had Valve's client up
        # and played until the memory limit. Without the client nothing here can make SteamAPI_Init succeed (BCD has no stand-in for it and will
        # not), so no setting changes the start. The rule is tied to this game's own session log; a session that went through Dock never matches
        # ("session": "library").
        "all": [s("LogOnline: Warning: STEAM: Steam API failed to initialize!"), s("Steamworks: SteamUtils() failed!")],
        "any": [s("Session log: logs/Subnautica2.exe-"), s("Session log: logs/Subnautica2-Win64-Shipping.exe-")],
        "reason": "Subnautica 2 was started as a program file, without Valve's Steam client: its Steam module could not start and the game crashes "
                  "on the missing Steam interface. Sign in to Steam in BCD and start the game from your Steam library (BCD Dock), not as a program.",
        "evidence": ["subnautica-2-no-steam-2026-10-23 (build 592, iPhone16,2, Send Logs 0F0JDZ and 89THM6)"],
    },
    {
        "id": "opengl-only-no-context",
        "lines": [],
        "noFix": True,
        "priority": 2,
        # Games that draw with OpenGL only. BCD's opengl32.dll is the "GL-absent stub" ("[unixlib] opengl32 (GL-absent stub table)"): it
        # loads, but no pixel format or context can be made, so the game's window cannot be created and the game stops. The logs show it
        # in the game's own words: GP Bikes (Steam 848050, CCKWDR and 0CE036, build 592) "Dummy window: ChoosePixelFormat failed."; Cry of
        # Fear (YP2SFQ build 576, RT4TB5 build 592) "ChoosePixelFormat failed"; the LOVE games, SDL's "Unable to create OpenGL window"
        # with "This program requires a graphics card and video drivers which support OpenGL 2.1 or OpenGL ES 2.x" (Gravity Circuit,
        # Steam 858710, HESXRX, HW8SSH and NRJMTN, build 592; Moonring YPTVXW, build 574). Geometry Dash (Steam 322170, cocos2d with
        # GLEW; 1H4EFS and XDTZPV, build 592; 5T7THJ, build 574) says nothing: it reads a NULL window object (libcocos2d, `mov rax,[rcx+0x370]`
        # with RCX = 0) after glfwCreateWindow failed, so it is recognised by its own session log. The stub line must be in the log, so a
        # game that only probes OpenGL and draws with Direct3D (a launcher's helper) is not described this way: a dialog or the game is
        # needed too. docs/OPENGL.md has the plan for OpenGL; until it lands no setting changes any of these. Priority 2 puts it ahead of
        # the 596 rule `wow64-native-iosb-completion-store`, which Cry of Fear RT4TB5 also matches through a console helper but which is
        # not why that game stops.
        "all": [s("[unixlib] opengl32 (GL-absent stub table)")],
        "any": [s("[win-name] #", "ChoosePixelFormat failed"), s("[win-name] #", "Unable to create OpenGL window"),
                s("Session log: logs/GeometryDash.exe-"), s("[session-log] Steam game GeometryDash.exe:")],
        "reason": "This game draws with OpenGL, and BCD has no OpenGL yet: the game cannot make its window and stops. This is not a setting; "
                  "Direct3D games are not affected.",
        "evidence": ["gp-bikes-opengl-2026-10-23 (Send Log CCKWDR, build 592)", "gravity-circuit-love-opengl-2026-10-23 (Send Log HESXRX, build 592)",
                     "geometry-dash-opengl-2026-10-23 (Send Log 1H4EFS, build 592)", "cry-of-fear-opengl-2026-10-23 (Send Log RT4TB5, build 592)",
                     "docs/OPENGL.md"],
    },
    {
        "id": "justcause3-world-stall",
        "lines": [],
        "noFix": True,
        "priority": 3,
        "session": "dock",
        "outcomes": ["hung"],
        "minStage": 4,
        # Just Cause 3 (Steam 225540), Send Log Z0YFZ9, build 592, iPad17,4 (13-inch M5, 16 GB, iPadOS 27.2), started through Madeira Dock: the
        # menu runs at 30 fps while the game's threads load the world in the background (5-8 cores for 50 s, then a second wave on all cores,
        # textures 2.0 to 4.1 GB, 94k to 155k live small buffers, footprint 4.4 to 6.8 GB of a 13.9 GB limit, flat afterwards: no leak, no
        # thermal event). At present 2094 the game stops: the process is idle, the main thread and a second game thread (tid 00c0) wait in WaitOnAddress from
        # JustCause3.exe+0x11153c3 and +0x11153d0, dxmt-finish-thr waits in MTLCommandBuffer waitUntilCompleted, and one frame is drawn every
        # 8.2-8.9 s (presents 2094, 2098, 2101, 2105, 2108 in 30 s steps) for the 130 s the log shows. The tester called it a freeze of the
        # whole iPad and force-closed the app; the log shows no GPU error, no memory pressure and no crash. Whether the GPU or a wait inside
        # the game holds each frame is not known (the log has no GPU time after present 2048); build 598's list for the game prints it
        # (MADEIRA_FRAME_STATS, MADEIRA_GPU_TRACE). No setting is known to change it, so the rule only names the stop: it matches a game that
        # stopped (hung: the user said it froze, or the freeze detector or a GPU error was logged, and the app was closed) after it had
        # presented for 20 s or more, and only this game's own session log.
        "all": [s("[session-log] Steam game JustCause3.exe:")],
        "reason": "Just Cause 3 stopped drawing after its world loaded: it showed one frame every few seconds while the game's threads waited. "
                  "No BCD setting is known to change this. Lowering the game's own graphics settings (textures, shadows) may help; the next "
                  "update's report from this game will show whether the GPU or the game is what waits.",
        "evidence": ["just-cause-3-world-stall-2026-10-23 (Send Log Z0YFZ9, build 592, iPad17,4)"],
    },
    # ajan37/tur600 (2026-10-24) -------------------------------------------------------------------------------------------------------
    {
        "id": "cuphead-bc-decode-memory-kill-dock",
        "lines": [],
        "noFix": True,
        "priority": 49,
        "session": "dock",
        "outcomes": ["killed"],
        "memoryMinPct": 85,
        # Cuphead (Steam 268910, 64-bit Unity, Direct3D 11) on an iPhone 14 Pro (A16, 5.6 GB, a 4075 MB limit; Send Logs SX9FB2 and 8PA4FD, build
        # 592, started through Madeira Dock): iOS closed the game 26 s after Cuphead.exe started (footprint 4052 and 4091 MB), with no crash line.
        # The A16 GPU cannot read BC textures ("[device] gpu=... bc=0"; A13 to A16 cannot, A17 Pro and later can), so DXMT decodes every BC
        # texture to RGBA8 ("[bc-decode] ml679": BC7 4096x4096 -> 64 MB each, no mip levels, so d3d11.mipClampBC has nothing to drop). The
        # second load burst (tex-private 1.46 -> 1.73 GB and init-upload staging 260 -> 580 MB, +128 MB every 0.5 s) reaches the limit. The
        # swap file was already in use ("[swap] ml1077 backed", swap-mb = 4096 in bcd.cfg) and held 138 MB at most: the memory is the GPU's,
        # not the guest's, and a larger swap file cannot take it. Priority 49 puts the rule ahead of the swap rules (50, 55) once the swap file
        # has been tried, and the swap line among the signals keeps a game without one on its way to the swap rules' first try. A Dock
        # session also carries the Steam client: the footprint outside Metal's allocations was 1659 MB at the kill against 1317 MB for the same
        # game started from its own program (SX9FB2 against AXP6SQ), so this variant names the other start. Only Cuphead's own identity lines
        # match, and only a memory kill of a phone whose GPU lacks BC: no other game is described.
        "all": [s("[device] gpu=", "bc=0"), s("[bc-decode] ml679"), s("[swap] ml1077 backed")],
        "any": [s("[session-log] Steam game Cuphead.exe:"), s("Session log: logs/Cuphead.exe-"), s("library launch Cuphead:")],
        # A install without the memory entitlements (increased-memory=0, a 3 GB cap: Cuphead SCSPHG, build 574) is another case: its remedy is the
        # entitlement (GetMoreRam), not a newer phone, so it is not described here.
        "none": [s("[device] address-map=", "increased-memory=0")],
        "reason": "Cuphead needs more memory than iOS gives an app on this phone. The phone's GPU (iPhone 15 and older, A16 or older) cannot read "
                  "the game's compressed textures, so they are expanded to four times their size, and iOS closes the game while it loads. "
                  "Starting it from its own program instead of BCD Dock (the game's page, Start with) leaves out the Steam client's share of "
                  "memory, which let it last longer in one tester's logs; phones from the iPhone 15 Pro on, and iPads with an M chip, read "
                  "these textures without expanding them.",
        "evidence": ["cuphead-bc-decode-memory-kill-dock-2026-10-24 (Send Log SX9FB2, build 592, iPhone15,2)",
                     "cuphead-bc-decode-memory-kill-direct-2026-10-24 (Send Log AXP6SQ, the same tester, started from the game's own program)",
                     "~/madeira-local/intake/analysis/tur600/ajan37.md"],
    },
    {
        "id": "cuphead-bc-decode-memory-kill-direct",
        "lines": [],
        "noFix": True,
        "priority": 49,
        "session": "library",
        "outcomes": ["killed"],
        "memoryMinPct": 85,
        # The same game, phone and kill (Send Log AXP6SQ, build 592) started from the game's own program (game page, Start with: no Steam
        # client in the session). It lasted 49 s from the start of the session instead of 40, presented at 60 fps for more than 20 s and was
        # closed by iOS at 4068 MB while the next scene's textures loaded (tex-private 1.41 -> 1.99 GB and init-upload staging 100 -> 676 MB in
        # 3.5 s). Nothing is left to leave out.
        "all": [s("[device] gpu=", "bc=0"), s("[bc-decode] ml679"), s("[swap] ml1077 backed")],
        "any": [s("[session-log] Steam game Cuphead.exe:"), s("Session log: logs/Cuphead.exe-"), s("library launch Cuphead:")],
        # A install without the memory entitlements (increased-memory=0, a 3 GB cap: Cuphead SCSPHG, build 574) is another case: its remedy is the
        # entitlement (GetMoreRam), not a newer phone, so it is not described here.
        "none": [s("[device] address-map=", "increased-memory=0")],
        "reason": "Cuphead needs more memory than iOS gives an app on this phone. The phone's GPU (iPhone 15 and older, A16 or older) cannot read "
                  "the game's compressed textures, so they are expanded to four times their size, and iOS closes the game while a scene loads. "
                  "Phones from the iPhone 15 Pro on, and iPads with an M chip, read these textures without expanding them.",
        "evidence": ["cuphead-bc-decode-memory-kill-direct-2026-10-24 (Send Log AXP6SQ, build 592, iPhone15,2)",
                     "cuphead-bc-decode-memory-kill-dock-2026-10-24 (Send Log SX9FB2, the same tester, started through Madeira Dock)",
                     "~/madeira-local/intake/analysis/tur600/ajan37.md"],
    },
    {
        "id": "virtual-mutex-fault-self-deadlock",
        "lines": [],
        "noFix": True,
        "priority": 4,
        "outcomes": ["hung", "exited-early"],
        # A thread of the game faults inside one of Wine's memory calls and stops for good. The page-fault server of the app is one Mach thread
        # for every guest thread (ios_mach_exception_thread); to serve a fault it takes virtual_mutex (ios_virtual_handle_fault_for_thread). The
        # faulting thread holds that same mutex: it is inside NtFreeVirtualMemory (a memset, build/ntdll-unix/virtual_ios.c decommit_pages) or
        # virtual_map_section (a memmove into the pool's RW alias, x8 = 0x7000000000). The stack dump shows exactly that: the exception thread
        # ("wine-x18-exc") in __psynch_mutexwait under ios_virtual_handle_fault_for_thread, and one thread parked ("run=3 susp=0 cpu=0") in
        # _platform_memset / _platform_memmove under NtFreeVirtualMemory or virtual_map_section. From then on every other thread that needs the
        # mutex waits behind them (Chrome_IOThread in NtAllocateVirtualMemory, workers in NtFreeVirtualMemory, IPC:CSteamEngine in
        # virtual_locked_server_call), and in a game with FEX's allocator lock (ml1044) the threads that wait for the stopped thread's lock spin
        # at 100 % (OMORI, Demons Roots: libwow64fex.dll+0x13be44, fex_ios_rpm_lock). No crash line, no frame: "detach-wait: presents=0" for
        # minutes. Seven logs of four games, in the ~1,330 logs of the intake (checked 2026-10-24): OMORI (N87QMH, 592), Demons Roots (4ZA68R,
        # 579), Saints Row IV (ZZ2S3X, 592) and ONE PIECE Bounty Rush (13FT14, 580; 2HJEVX, NJ989H, WCSJBK, 592). The three signals never occur
        # together in any other log, the player must have called the session frozen or closed it before a frame (hung / exited-early), and
        # priority 4 puts the real stop ahead of the lines other rules try on its symptoms (ONE PIECE's GDI line, 2HJEVX). What the fault is
        # is not in the log (the address is not printed); OMORI's thread is 0x630 bytes below the low end of the stack the registry has for it
        # ("[mach-deliver] ... does NOT contain sp=0x17497f9d0", L16160), which points at a stack overflow. Build 600 prints "[vm-fault-wait]"
        # with the address when a fault has to wait for the mutex.
        "all": [s("Madeira`ios_virtual_handle_fault_for_thread"), s('"wine-x18-exc"', "psynch_mutexwait"),
                s("pc=libsystem_platform.dylib`_platform_mem", "run=3 susp=0 cpu=0")],
        "any": [s("bt[0] Madeira`NtFreeVirtualMemory+"), s("bt[0] Madeira`virtual_map_section+")],
        "reason": "A thread of the game stopped inside one of BCD's memory calls: it holds the lock that the part serving its own page fault "
                  "needs, so neither goes on and no frame is ever drawn. This is an open bug of BCD's; no setting changes it.",
        "evidence": ["omori-vm-fault-self-deadlock-2026-10-24 (Send Log N87QMH, build 592, iPhone17,1)",
                     "demons-roots-vm-fault-self-deadlock-2026-10-21 (Send Log 4ZA68R, build 579, iPhone18,1)",
                     "saints-row-iv-vm-fault-self-deadlock-2026-10-23 (Send Log ZZ2S3X, build 592, iPhone18,2)",
                     "one-piece-bounty-rush-vm-fault-self-deadlock-2026-10-23 (Send Log WCSJBK, build 592, iPhone16,2)",
                     "~/madeira-local/intake/analysis/tur600/ajan37.md"],
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

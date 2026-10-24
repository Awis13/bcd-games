#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""wow-reap (build 593): a 32-bit process that would get no guest window ends the leftover threads of exited processes.

Battle.net (build 592, the owner's install): Battle.net.exe started its Agent every 3 s for 80 s and every start failed at
"guest window reserve returned 0xc0000017". Two windows were "still held by an exited process whose teardown did not
complete": a worker thread of an exited helper kept running in translated code, so the live-thread guard kept its window.

Checks (read from the sources):
  - ios_wow_window_reserve tries once as before; only a STATUS_NO_MEMORY refusal runs the reap, and only when it ended a
    thread is the reservation tried a second time (whose first step, ios_wow_reclaim_dead_windows, reclaims the windows);
  - the reap looks only at windows marked dead (dead == 1) and MADEIRA_WOW_REAP_DEAD=0 turns it off;
  - ios_thread_registry_end_dead_range ends a thread only when the dead PEB is on the dead list, the thread's TEB is in
    the window and its Peb (read with vm_read_overwrite) is the dead process's, never the calling thread; it suspends
    first, reads the pc, and ends the thread only in guest code (the window), translated code (JIT pool / region C) or
    libsystem_kernel; anywhere else the thread is resumed and kept;
  - the live-thread guard itself is unchanged (MADEIRA_WOW_LIVE_WINDOW_GUARD) and the reserve keeps its refusals;
  - auto-tune: the known rule (fixed in 593) and its fixture;
  - CI runs this check.
"""
import re
import sys
from pathlib import Path

R = Path(__file__).resolve().parents[2]
NT = R / "build/ntdll-unix"
fails = []


def check(what, ok, detail=""):
    print(("ok   " if ok else "FAIL ") + what + (f"\n     {detail}" if detail and not ok else ""))
    if not ok:
        fails.append(what)


def body(src, signature):
    i = src.find(signature)
    if i < 0:
        return ""
    j = src.find("\n}\n", i)
    return src[i:j + 3]


v = (NT / "virtual_ios.c").read_text()
sig = (NT / "signal_arm64_ios.c").read_text()

res = body(v, "NTSTATUS ios_wow_window_reserve(void)\n{")
check("reserve: tries once, reaps only after STATUS_NO_MEMORY, retries only when a thread was ended",
      re.search(r"NTSTATUS status = ios_wow_window_reserve_once\(\);\s*if \(status == STATUS_NO_MEMORY && ios_wow_reap_dead_windows\(\) > 0\)\s*\{\s*status = ios_wow_window_reserve_once\(\);",
                res) is not None and res.count("ios_wow_window_reserve_once()") == 2, res[:400])
once = body(v, "static NTSTATUS ios_wow_window_reserve_once(void)\n{")
check("the first try is the old reservation (reclaim first, the same refusals)",
      "ios_wow_reclaim_dead_windows();" in once and once.count("return STATUS_NO_MEMORY;") >= 4 and "ios_wow_window_pick( &guard_owned )" in once)
reap = body(v, "static int ios_wow_reap_dead_windows(void)\n{")
check("reap: only windows marked dead, MADEIRA_WOW_REAP_DEAD=0 turns it off",
      'getenv( "MADEIRA_WOW_REAP_DEAD" )' in reap and 'if (env && !strcmp( env, "0" )) return 0;' in reap
      and "ios_wow_windows[i].dead != 1" in reap and "ios_thread_registry_end_dead_range(" in reap)
recl = body(v, "static void ios_wow_reclaim_dead_windows(void)\n{")
check("the live-thread guard is unchanged", 'getenv( "MADEIRA_WOW_LIVE_WINDOW_GUARD" )' in recl
      and "ios_thread_registry_range_busy( base, IOS_WOW_WINDOW_SIZE )" in recl)

end = body(sig, "int ios_thread_registry_end_dead_range( uintptr_t base, uintptr_t size, void *dead_peb, int *kept )\n{")
check("end: only for a PEB on the dead list", "if (!dead_peb || !ios_peb_is_dead( dead_peb )) return 0;" in end)
check("end: the TEB in the window, its Peb the dead process's, never the calling thread",
      "port == self" in end and "teb < base || teb - base >= size" in end
      and "offsetof(TEB, Peb)" in end and "peb != dead_peb" in end)
i_susp, i_state, i_term = end.find("thread_suspend( port )"), end.find("thread_get_state( port"), end.find("thread_terminate( port )")
check("end: suspend, read the pc, then decide", 0 < i_susp < i_state < i_term)
check("end: only guest code, translated code or a system call",
      'where = "guest code"' in end and "ios_ajan2_pc_is_jit( pc )" in end and 'strstr( di.dli_fname, "libsystem_kernel" )' in end)
k = end.find("if (!where)")
check("end: anywhere else the thread is resumed and kept", 0 < k < i_term and "thread_resume( port );" in end[k:i_term])

check("end: a refused thread_terminate leaves the thread suspended for good and counts it (parked), logging the kr",
      '"parked, suspended for good"' in end and "(int)kr" in end and end.count("ended++;") == 1)
busy = body(sig, "int ios_thread_registry_range_busy( uintptr_t base, uintptr_t size )\n{")
check("range_busy: a suspended thread of an exited process (its Peb on the dead list) does not hold the window",
      "info.suspend_count > 0" in busy and "ios_peb_is_dead( peb )" in busy and "offsetof(TEB, Peb)" in busy
      and busy.index("kr == KERN_INVALID_ARGUMENT") < busy.index("info.suspend_count > 0") < busy.index("return 1;"))
srv = (NT / "server_ios.c").read_text()
fdt = srv[srv.index("static void ios_fdt_reg( int fd, int kind, void *peb )"):srv.index("static void ios_register_proc_socket(void *peb_id, int fd)")]
tidf = body(srv, "static unsigned int ios_fdt_tid(void)\n{")
check("fd ledger: the thread id is 0 without a TEB (the storm-end worker has none: build 593 ended the app there)",
      "TEB *teb = NtCurrentTeb();" in tidf and "return teb ?" in tidf and "GetCurrentThreadId" not in fdt and fdt.count("ios_fdt_tid()") >= 5)

sys.path.insert(0, str(R / "build/tools"))
import autotune_rules  # noqa: E402
rule = next((r for r in autotune_rules.RULES if r["id"] == "wow-window-held-by-exited-process-threads"), None)
check("auto-tune: the known rule (fixed in 593, no lines)", rule is not None and rule.get("fixedIn") == 593 and rule["lines"] == [])
fx = R / "tests/fixtures/autotune/battlenet-agent-window-slots-2026-10-23"
check("auto-tune: its fixture", fx.with_suffix(".log").exists() and fx.with_suffix(".expect").exists())
check("CI runs this check", "tests/host/check-wow-reap.py" in (R / ".github/workflows/build-ipa.yml").read_text())

if fails:
    print(f"FAILED: wow-reap ({len(fails)})")
    sys.exit(1)
print("PASS: wow-reap (leftover threads of exited 32-bit processes end only when a new one would get no window)")

/* VirtualProtect reports PAGE_READWRITE, not PAGE_WRITECOPY, for the page a 32-bit program writes: V8's flag freeze (OPT-IN).
 *
 * SPDX-License-Identifier: GPL-3.0-only
 * Copyright 2026 thebcd69 (BCD)
 * BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
 *
 * WHY THIS EXISTS
 * ---------------
 * Battle.net (builds 594 and 596, the owner's iPhone18,2): the launcher's window spins for ever because its Chromium 108.0.5359.125 renderers
 * (`Battle.net.exe --type=renderer`, 32-bit, Blizzard's own libcef.dll) end on an int3 at libcef.dll+0x16D00E1, 9 times in 12, 0.1 to 0.3 s after
 * GpuMemoryThread starts. The CHECK is named by the evidence in the logs and the public CEF 108.4.13 libcef.dll (same Chromium, same V8,
 * 0x27700 bytes of other code in front of it) with its PDB:
 *
 *   v8::base::OS::SetDataReadOnly  (V8 10.8.168.22, src/base/platform/platform-win32.cc)
 *       unsigned long old_protection;
 *       CHECK(VirtualProtect(address, size, PAGE_READONLY, &old_protection));
 *       CHECK_EQ(PAGE_READWRITE, old_protection);            <- the second int3
 *   called by FlagList::FreezeFlags() from V8::InitializeOncePerProcessImpl() (v8_flags.freeze_flags_after_init, on by default):
 *       base::OS::SetDataReadOnly(&v8_flags, sizeof(v8_flags));       v8_flags: FlagValues, 4 KB aligned, in libcef.dll's .data
 *
 * The match, instruction by instruction, with the register file of the int3 ([int3-guest], builds 594 and 596, every renderer the same):
 *   - the candidate function is `push ebp; mov ebp,esp; sub esp,8; ...; call [VirtualProtect]; test eax,eax; je int3; cmp [ebp-8],4; jne int3`.
 *     The log's EBP - ESP = 0xcff2ac - 0xcff2a4 = 8; EAX = 1 (VirtualProtect returned TRUE: the FIRST check passed, the int3 is the SECOND, the
 *     comparison with PAGE_READWRITE); EDX = 0xfff8e440 = ntdll.dll+0x4e440 (the stub every i386 syscall thunk loads into EDX: the call came
 *     back through NtProtectVirtualMemory); ECX = a stale stack address (the callee clobbered it).
 *   - FEX started the block that ends in the int3 at 0x6de800c5 (host x21): libcef.dll+0x16d00c5 is the instruction after the `call [VirtualProtect]`, and
 *     0x16d00e1 - 0x16d00c5 = 0x1c = 28 bytes, exactly the distance from the return address (0x16a89c5) to the second int3 (0x16a89e1) in the
 *     public libcef.dll, whose function sits 0x27700 bytes earlier.
 *   - the other processes (GPU, network and storage services) never start V8, so none of them reaches it; the renderer is the first.
 *
 * WHAT IS WRONG IN BCD
 * --------------------
 * NtProtectVirtualMemory returns `get_win32_prot( vprot, view->protect )`, the page's RECORDED protection. A writable data section of an
 * image is recorded READ|WRITECOPY (map_image_into_view), and the recorded value never changes on this port: the mapping is writable from the
 * start (get_unix_prot() hands PROT_WRITE to WRITECOPY pages), a store raises no fault, nothing notices the page was copied. So an untouched page
 * and a page the program has written ten thousand times both report PAGE_WRITECOPY (0x08). On Windows the same call reports PAGE_WRITECOPY
 * only until the first store (the kernel then makes the page private and READWRITE), and v8_flags is written during start-up by Chromium's own
 * SetFlagsFromString / EnforceFlagImplications calls well before the freeze. virtual_ios.c's own comment already says it
 * ("get_win32_prot() reports the section's allocation prot (PAGE_WRITECOPY for .data) -- not the actual per-page state").
 *
 * WHAT THIS CHANGES (only with env.MADEIRA_PROTECT_WRITECOPY_AS_RW = 1, which only Battle.net's launcher entry sets)
 * -----------------------------------------------------------------------------------------------------------------
 * The VALUE reported as the old protection, and only when ALL of these hold:
 *   - the switch is on (the exact value 1);
 *   - the calling pseudo-process is a 32-bit (WoW64) process (ios_wow_base() != 0);
 *   - the range is in an image view (SEC_IMAGE): a writable data section of a DLL or exe;
 *   - the recorded old protection is exactly PAGE_WRITECOPY (not the EXECUTE variant);
 *   - the new protection is exactly PAGE_READONLY: a program write-protecting a data page it has been writing (the freeze pattern).
 * Then PAGE_READWRITE is reported. The call itself, the recorded protection after it, the JIT-pool sync and every other call are unchanged: it
 * is the return value only. With the switch unset the function returns `old` untouched, so nothing changes for any other program,
 * whatever it loads (the Rockstar Games Launcher's 64-bit libcef.dll included: not a 32-bit process, and the switch is not set for it).
 *
 * Pure decision, so tests/host/check-wc-old-prot.py can compile this file on the host and run every combination.
 */
#ifndef IOS_WC_OLD_PROT_H
#define IOS_WC_OLD_PROT_H

#include <stdlib.h>

/* env.MADEIRA_PROTECT_WRITECOPY_AS_RW = 1; anything else (or nothing) leaves it off. Read once. */
static inline int ios_wc_old_prot_enabled( void )
{
    static int enabled = -1;

    if (enabled < 0)
    {
        const char *e = getenv( "MADEIRA_PROTECT_WRITECOPY_AS_RW" );

        enabled = e && e[0] == '1' && !e[1];
    }
    return enabled;
}

/* The old protection NtProtectVirtualMemory hands back.
 *   old        get_win32_prot() of the first page
 *   new_prot   the protection the caller asked for
 *   image_view the range lies in a SEC_IMAGE view
 *   wow        the calling pseudo-process is a 32-bit one (ios_wow_base() != 0)
 *   enabled    ios_wc_old_prot_enabled()
 * Returns 0 in `*changed` when it returns `old` as it came. */
static inline unsigned ios_wc_old_prot_report( unsigned old, unsigned new_prot, int image_view, int wow, int enabled, int *changed )
{
    *changed = 0;
    if (enabled && wow && image_view && old == PAGE_WRITECOPY && new_prot == PAGE_READONLY)
    {
        *changed = 1;
        return PAGE_READWRITE;
    }
    return old;
}

#endif

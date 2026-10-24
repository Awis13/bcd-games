/* [ill-guest]: the bytes FEX could not decode, printed when the dispatcher ends in an illegal instruction ("Invalid instruction in entry block").
 *
 * SPDX-License-Identifier: GPL-3.0-only
 * Copyright 2026 thebcd69 (BCD)
 * BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
 *
 * WHY THIS EXISTS
 * ---------------
 * Jetboard Joust (Steam 1181600, MonoGame on .NET Core 3.1, a tester's iPad16,3, build 592, seven Send Logs 1QAXN6, 48E2PC, D859QD, E3C5T3,
 * PA6QRW, QT6V0A and WCC96S, 2026-10-23) now gets through the runtime's compare-and-swap on its own RWX code pages (env.MADEIRA_ANON_CAS_FLUSH
 * = 1: FEX does the CAS, "Handled self-modifying code", "[smc-atomic] ... handled"), loads clrjit.dll and MonoGame.Framework.dll, and ends 2 s
 * later:
 *
 *   E AC Invalid instruction in entry block: 7193D880E0          FEX, translating the block at the start of a fresh RWX page
 *   ILL at pc=0x169ff84e8 ... x6=0x7193d880e0 ... L1 entry: guest=0x7193d880e0 (guest==x6? 1)
 *   The process was terminated due to an internal error in the .NET Runtime at IP 00000071F35E0F8B with exit code c000001d
 *
 * (the page had been created RWX a few hundred lines earlier, "[jit-pool] anon RWX 0x7193d88000 size=0x4000 NEW" and "Add SMC interval").
 * The same message ends Yakuza: Like a Dragon (38E560) and, in build 578, the first Jetboard Joust runs: every one of them is a block that
 * FEX is asked to translate at an address the runtime has just written or is about to write. The logs never say what is AT that address.
 * Whether the page holds zeros (the runtime jumped before it wrote), half a stub, code of another architecture (a pool copy leaked into the
 * guest's view), or valid bytes that FEX read before they landed, decides which of four different bugs it is.
 *
 * WHAT IT PRINTS (log only, at most 3 reports per session, every line starts with "[ill-guest]")
 * ----------------------------------------------------------------------------------------------
 *  1. 64 bytes of the guest's view of the page, 16 before the block's entry address and 48 from it (a byte that cannot be read prints "??");
 *  2. when the page is an anonymous RWX alias of the JIT pool, the same 64 bytes read through the alias pointer the stores go to, and whether
 *     the two views are equal: a difference is a page whose writes never reached the code FEX reads (or the reverse).
 *
 * WHAT IT NEVER DOES
 * ------------------
 * It reads guest memory through ios_av_args_read (a fault-safe Mach read) and ios_jit_anon_alias_lookup (a table lookup), writes only to
 * stderr and only on the SIGILL path that ends the process already; no switch, since there is nothing to change when nothing is wrong.
 * Uses ios_av_args_read (ios_av_args_diag.h), so it is included after that header.
 */
#ifndef IOS_ILL_GUEST_DIAG_H
#define IOS_ILL_GUEST_DIAG_H

#define IOS_ILLG_MAX_REPORTS 3
#define IOS_ILLG_BEFORE      16
#define IOS_ILLG_AFTER       48

extern uintptr_t ios_jit_anon_alias_lookup(uintptr_t fault_addr);

/* len bytes at addr, "xx xx ?? xx": a byte of a range that cannot be read prints ?? (whole range first, then 16 bytes at a time) */
static unsigned ios_illg_bytes( uint64_t addr, char *out, size_t outsz, uint8_t *buf, unsigned len )
{
    uint8_t valid[IOS_ILLG_BEFORE + IOS_ILLG_AFTER];
    unsigned i, got = 0;
    size_t l = 0;

    memset( buf, 0, len );
    memset( valid, 0, len );
    if (ios_av_args_read( addr, buf, len ))
    {
        memset( valid, 1, len );
        got = len;
    }
    else
        for (i = 0; i < len; i += 16)
        {
            unsigned n = len - i < 16 ? len - i : 16;

            if (ios_av_args_read( addr + i, buf + i, n ))
            {
                memset( valid + i, 1, n );
                got += n;
            }
        }
    out[0] = 0;
    for (i = 0; i < len && l + 4 < outsz; i++)
        l += (size_t)(valid[i] ? snprintf( out + l, outsz - l, "%02x ", buf[i] ) : snprintf( out + l, outsz - l, "?? " ));
    if (l) out[l - 1] = 0;
    return got;
}

/* guest: the address of the block FEX could not translate (the L1 dispatch entry's guest field, equal to x6 in the ILL report);
 * host_pc: the dispatcher's faulting instruction. */
static void ios_ill_guest_bytes( uint64_t guest, uint64_t host_pc )
{
    static volatile int reports;
    uint8_t rx[IOS_ILLG_BEFORE + IOS_ILLG_AFTER], rw[IOS_ILLG_BEFORE + IOS_ILLG_AFTER];
    char hx[(IOS_ILLG_BEFORE + IOS_ILLG_AFTER) * 3 + 4], hw[(IOS_ILLG_BEFORE + IOS_ILLG_AFTER) * 3 + 4];
    unsigned got, i;
    uintptr_t alias;
    int num;

    if (guest < IOS_ILLG_BEFORE + 0x10000 || guest >= 0x800000000000ULL) return;           /* not an address of a 64-bit guest */
    if ((num = __sync_add_and_fetch( &reports, 1 )) > IOS_ILLG_MAX_REPORTS) return;

    got = ios_illg_bytes( guest - IOS_ILLG_BEFORE, hx, sizeof(hx), rx, IOS_ILLG_BEFORE + IOS_ILLG_AFTER );
    dprintf( STDERR_FILENO,
             "[ill-guest] #%d FEX could not translate the block at guest 0x%llx (host pc 0x%llx); the guest's view of 16 bytes before it and 48 from it "
             "(%u of %u readable): %s\n",
             num, (unsigned long long)guest, (unsigned long long)host_pc, got, IOS_ILLG_BEFORE + IOS_ILLG_AFTER, hx );
    alias = ios_jit_anon_alias_lookup( (uintptr_t)guest );
    if (!alias)
    {
        dprintf( STDERR_FILENO, "[ill-guest] #%d no anonymous RWX alias covers that address (an image, a file view or plain memory)\n", num );
        return;
    }
    got = ios_illg_bytes( (uint64_t)alias - IOS_ILLG_BEFORE, hw, sizeof(hw), rw, IOS_ILLG_BEFORE + IOS_ILLG_AFTER );
    i = 0;
    while (i < IOS_ILLG_BEFORE + IOS_ILLG_AFTER && rx[i] == rw[i])
        i++;
    if (i == IOS_ILLG_BEFORE + IOS_ILLG_AFTER)
        dprintf( STDERR_FILENO, "[ill-guest] #%d the same bytes through the alias pointer 0x%llx the stores go to: the two views agree\n",
                 num, (unsigned long long)alias );
    else
        dprintf( STDERR_FILENO, "[ill-guest] #%d the alias pointer 0x%llx shows DIFFERENT bytes (first difference at %+d from the block): %s\n",
                 num, (unsigned long long)alias, (int)i - IOS_ILLG_BEFORE, hw );
}

#endif

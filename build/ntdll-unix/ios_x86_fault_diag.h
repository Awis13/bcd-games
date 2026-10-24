/* [x86-fault]: an OPT-IN report for a 32-bit guest's unhandled access violation or int3, with the evidence that names the code.
 *
 * SPDX-License-Identifier: GPL-3.0-only
 * Copyright 2026 thebcd69 (BCD)
 * BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
 *
 * WHY THIS EXISTS
 * ---------------
 * Two Need for Speed games of one engine (non-Steam copies, 32-bit) stop at a fixed place of their own program, and the logs cannot say why:
 *
 *   Need for Speed: Most Wanted, speed.exe (builds 574, 580 and 592, five testers' logs XM2BC1, GEVT5A, NEPBNC, NC4V56, 5C54B7, GTKMGA):
 *     "wine: Unhandled page fault on read access to B514AB6A at address 0081AB7A", the same address in every run. The guest registers at
 *     the fault (x32 mapping, [fault-full]) are EAX = 0x0A6A0110, EDI = 0x0A6A00C0, ECX = 0xB514AB6A, and ECX - EDI = 0xAAAAAAAA in every
 *     log (0x099F00C0 + 0xAAAAAAAA = 0xB449AB6A on another phone, 0x09C700C0 + 0xAAAAAAAA = 0xB471AB6A on a third): the load is
 *     `mov r32,[ecx]` through a pointer the program built from a heap object and a 0xAAAAAAAA fill pattern, i.e. from a field that was never
 *     initialised. The code at 0x81AB7A is not readable by the existing report ([rsp-trunc] reads a guest address as a host address, which
 *     is only right for a 64-bit process).
 *   Need for Speed: Underground 2, SPEED2.EXE (builds 580 and 592, five logs): an int3 at 0x4405A9 after the Enter key of the menu, with the
 *     [stp-split] read-back of build 591 proving the 580 split store is right ("source/guest/alias read equal"), so the int3 is the game's.
 *
 * Both need the same thing the Battle.net renderers needed (ios_int3_x86_diag.h, build 597): the code around the address, the stack, the
 * modules, the objects the registers point at. That report only runs for an int3 and only when env.MADEIRA_INT3_X86_DUMP = 1 (Battle.net's
 * launcher entry, whose checks forbid any other user); this one is a separate switch for the games' own lists, and it also runs for an
 * unhandled access violation.
 *
 * WHAT IT PRINTS (only for a 32-bit guest, at most 3 reports per session, every line starts with "[x86-fault]" except the PE identity rows
 * that the helper of ios_int3_x86_diag.h prints with its "[int3-x86]" prefix)
 * ----------------------------------------------------------------------------------------------
 *  1. the headline: what happened, the faulting address (an access violation: the guest address read or written), the address of the block
 *     (the guest rip of the translated block that faulted: the instruction is within its first bytes) or of the int3, with module+RVA, and
 *     the eight registers; then the registers that point into a module;
 *  2. for an access violation, the fault address minus each register (a 0xAAAAAAAA there names a pattern, a small delta a stale pointer);
 *  3. the thread facts: TEB32 and PEB32, the stack range and depth, LastErrorValue and LastStatusValue, the TLS bitmap; the modules;
 *  4. the PE identity of the module the address is in: TimeDateStamp, SizeOfImage, CheckSum, the sections (identifies the exact build of the
 *     program, whatever the copy);
 *  5. 96 dwords of stack from ESP, and the ones that point into a module; the EBP chain with module+RVA;
 *  6. the code: 256 bytes before the block's address and 160 after it, and 64 bytes before every return address of the chain;
 *  7. 96 bytes at EAX, ECX, EDX, EBX, ESI, EDI and EBP when they point into the stack or heap, as dwords with module+RVA (the objects the
 *     failing function was handed).
 *
 * WHAT IT NEVER DOES
 * ------------------
 * It reads the guest's memory through ios_av_args_read (a fault-safe Mach read; a page that is not there prints "??" or is left out) and
 * writes only to stderr. It stops nothing, writes no memory, changes no protection and does not touch the thread's state. It is off unless
 * env.MADEIRA_X86_FAULT_DUMP = 1 (a game's recommended list, nothing else sets it); with the switch unset the fault paths do exactly what they
 * did before (one call to ios_x86_fault_enabled(), which reads the environment once).
 *
 * The 32-bit register mapping is FEX x32::SRA (EAX x4, ECX x7, EDX x5, EBX x6, ESP x8, EBP x9, ESI x10, EDI x11, window base x19); the
 * access-violation call hands over the host registers in the order RAX RCX RDX RBX RSP RBP RSI RDI of FEX's register file, the int3 call the
 * guest register file itself (the same order).
 *
 * Uses ios_av_args_read (ios_av_args_diag.h), ios_wn32_modules / struct ios_wn32_mod (ios_wow_null_diag.h) and the helpers of
 * ios_int3_x86_diag.h (ios_i3x_read, ios_i3x_where, ios_i3x_hex, ios_i3x_image), so it is included after those.
 */
#ifndef IOS_X86_FAULT_DIAG_H
#define IOS_X86_FAULT_DIAG_H

#define IOS_X86F_MAX_REPORTS  3
#define IOS_X86F_STACK_DWORDS 96
#define IOS_X86F_CODE_BEFORE  256
#define IOS_X86F_CODE_AFTER   160
#define IOS_X86F_OBJ_BYTES    96
#define IOS_X86F_FRAMES       24
#define IOS_X86F_SITES        12

/* env.MADEIRA_X86_FAULT_DUMP = 1 turns the report on; anything else (or nothing) leaves it off. Read once. */
static int ios_x86_fault_enabled( void )
{
    static int enabled = -1;

    if (enabled < 0)
    {
        const char *e = getenv( "MADEIRA_X86_FAULT_DUMP" );

        enabled = e && e[0] == '1' && !e[1];
    }
    return enabled;
}

/* code bytes [at - before, at + after) of the guest, 32 per row (ios_i3x_code of the earlier report is sized for 288 bytes; this one for 416) */
static void ios_x86f_code( uint64_t win, const char *what, uint32_t at, unsigned before, unsigned after, const struct ios_wn32_mod *mods, unsigned nmods )
{
    uint8_t buf[IOS_X86F_CODE_BEFORE + IOS_X86F_CODE_AFTER], valid[IOS_X86F_CODE_BEFORE + IOS_X86F_CODE_AFTER];
    unsigned len = before + after, row, got;
    uint32_t from = at - before;
    char hex[32 * 3 + 4], w[96];

    if (len > sizeof(buf) || at < before) return;
    got = ios_i3x_read( win + from, buf, valid, len );
    ios_i3x_where( mods, nmods, at, w, sizeof(w) );
    dprintf( STDERR_FILENO, "[x86-fault]   code %s: %u bytes before and %u after guest 0x%08x%s%s%s (%u of %u readable); rows are 32 bytes, "
             "the first at 0x%08x\n", what, before, after, at, w[0] ? " (" : "", w, w[0] ? ")" : "", got, len, from );
    for (row = 0; row < len; row += 32)
    {
        unsigned n = len - row < 32 ? len - row : 32;

        ios_i3x_hex( hex, sizeof(hex), buf + row, valid + row, n );
        dprintf( STDERR_FILENO, "[x86-fault]   code %s %+d: %s\n", what, (int)(row - before), hex );
    }
}

/* IOS_X86F_OBJ_BYTES bytes at a register that points into the stack or heap, as dwords with module+RVA */
static void ios_x86f_object( uint64_t win, const char *reg, uint32_t v, const struct ios_wn32_mod *mods, unsigned nmods )
{
    uint8_t buf[IOS_X86F_OBJ_BYTES], valid[IOS_X86F_OBJ_BYTES];
    char line[600], w[96];
    unsigned i, row;

    if (v < 0x10000 || v > 0xfffe0000u) return;
    ios_i3x_where( mods, nmods, v, w, sizeof(w) );
    if (w[0]) return;
    if (!ios_i3x_read( win + v, buf, valid, sizeof(buf) )) return;
    for (row = 0; row < IOS_X86F_OBJ_BYTES / 32; row++)
    {
        size_t l = 0;

        line[0] = 0;
        for (i = 0; i < 8 && l + 60 < sizeof(line); i++)
        {
            uint32_t d;
            unsigned at = row * 32 + i * 4;

            if (!valid[at]) { l += (size_t)snprintf( line + l, sizeof(line) - l, " ??" ); continue; }
            memcpy( &d, buf + at, 4 );
            ios_i3x_where( mods, nmods, d, w, sizeof(w) );
            l += (size_t)snprintf( line + l, sizeof(line) - l, " %x%s%s%s", d, w[0] ? "(" : "", w, w[0] ? ")" : "" );
        }
        dprintf( STDERR_FILENO, "[x86-fault]   [%s=0x%x]+0x%02x:%s\n", reg, v, row * 32, line );
    }
}

/* The report. gregs: the 8 registers in the order RAX RCX RDX RBX RSP RBP RSI RDI (32-bit values in the low halves); eip: the guest address
 * of the int3 or of the block that faulted; fault_addr: the guest address of the access (-1 for an int3); write: 1 for a store, 0 for a
 * load, -1 when not known. */
static void ios_x86_fault_report( const arm_thread_state64_t *st, uintptr_t thread_teb, const uint64_t *gregs, uint64_t eip64,
                                  int64_t fault_addr, int write )
{
    static volatile int reports;
    uint64_t base = st->__x[19], teb32_host = 0;
    uint32_t eax, ecx, edx, ebx, esp, ebp, esi, edi, eip, teb32, peb32 = 0, stack[IOS_X86F_STACK_DWORDS], i;
    int32_t woff = 0;
    struct ios_wn32_mod mods[96];
    unsigned nmods = 0, k, nrefs = 0, nsite = 0;
    char w[96], line[1000];
    int num, in_mod = -1;
    uint32_t sites[IOS_X86F_SITES];

    if (!gregs || eip64 < 0x10000 || eip64 > 0xffffffffULL) return;                           /* a 64-bit guest has its own reports */
    if (!((base & 0xffffffffULL) == 0 && base >= 0x100000000ULL)) return;                     /* not a 4 GB window base in x19 */
    /* x18 is the thread's TEB in JIT code; the registry lookup the caller did can fall back to slot 0, which is another thread's, so x18 wins
     * when it lies in this window */
    if (st->__x[18] >= base && st->__x[18] - base < 0x100000000ULL) thread_teb = (uintptr_t)st->__x[18];
    if (!thread_teb) return;
    if ((num = __sync_add_and_fetch( &reports, 1 )) > IOS_X86F_MAX_REPORTS) return;

    eax = (uint32_t)gregs[0]; ecx = (uint32_t)gregs[1]; edx = (uint32_t)gregs[2]; ebx = (uint32_t)gregs[3];
    esp = (uint32_t)gregs[4]; ebp = (uint32_t)gregs[5]; esi = (uint32_t)gregs[6]; edi = (uint32_t)gregs[7];
    eip = (uint32_t)eip64;

    if (ios_av_args_read( thread_teb + IOS_WN32_TEB_WOWOFF, &woff, 4 ) && woff > 0) teb32_host = thread_teb + (uint64_t)woff;
    teb32 = teb32_host >= base && teb32_host - base < 0x100000000ULL ? (uint32_t)(teb32_host - base) : 0;
    if (teb32)
    {
        uint32_t self = 0;

        if (!ios_av_args_read( teb32_host + IOS_WN32_TEB_SELF, &self, 4 ) || self != teb32) teb32 = 0;
    }
    if (teb32 && ios_av_args_read( teb32_host + IOS_WN32_TEB_PEB, &peb32, 4 ) && peb32 >= 0x10000)
        nmods = ios_wn32_modules( base, peb32, mods, 96 );
    ios_i3x_where( mods, nmods, eip, w, sizeof(w) );

    if (fault_addr >= 0)
        dprintf( STDERR_FILENO,
                 "[x86-fault] #%d a 32-bit guest faulted %s guest 0x%08x in the block that starts at guest 0x%08x (%s), window 0x%llx, %u modules in "
                 "the PEB32 list; EAX=0x%x ECX=0x%x EDX=0x%x EBX=0x%x ESI=0x%x EDI=0x%x EBP=0x%x ESP=0x%x\n",
                 num, write < 0 ? "at" : (write ? "writing" : "reading"), (uint32_t)fault_addr, eip, w[0] ? w : "no module",
                 (unsigned long long)base, nmods, eax, ecx, edx, ebx, esi, edi, ebp, esp );
    else
        dprintf( STDERR_FILENO,
                 "[x86-fault] #%d a 32-bit guest executed int3 at guest 0x%08x (%s), window 0x%llx, %u modules in the PEB32 list; "
                 "EAX=0x%x ECX=0x%x EDX=0x%x EBX=0x%x ESI=0x%x EDI=0x%x EBP=0x%x ESP=0x%x\n",
                 num, eip, w[0] ? w : "no module", (unsigned long long)base, nmods, eax, ecx, edx, ebx, esi, edi, ebp, esp );

    /* registers that point into a module, and (for an access) the fault address minus each register */
    {
        const struct { const char *name; uint32_t v; } regs[] =
            { { "EAX", eax }, { "ECX", ecx }, { "EDX", edx }, { "EBX", ebx }, { "ESI", esi }, { "EDI", edi }, { "EBP", ebp }, { "ESP", esp } };
        size_t l = 0;

        line[0] = 0;
        for (k = 0; k < sizeof(regs) / sizeof(regs[0]); k++)
        {
            ios_i3x_where( mods, nmods, regs[k].v, w, sizeof(w) );
            if (w[0] && l + 100 < sizeof(line)) l += (size_t)snprintf( line + l, sizeof(line) - l, " %s=0x%x(%s)", regs[k].name, regs[k].v, w );
        }
        dprintf( STDERR_FILENO, "[x86-fault]   registers that point into a module:%s\n", l ? line : " none" );
        if (fault_addr >= 0)
        {
            l = 0;
            line[0] = 0;
            for (k = 0; k < sizeof(regs) / sizeof(regs[0]); k++)
                if (l + 40 < sizeof(line))
                    l += (size_t)snprintf( line + l, sizeof(line) - l, " -%s=0x%x", regs[k].name, (uint32_t)fault_addr - regs[k].v );
            dprintf( STDERR_FILENO, "[x86-fault]   fault address minus each register (mod 2^32):%s\n", line );
        }
    }

    /* the thread's TEB32 and PEB32: stack range and depth, last error and status, the TLS bitmap */
    if (teb32)
    {
        uint32_t sbase = 0, slimit = 0, dealloc = 0, lerr = 0, lstat = 0, tlsexp = 0, bits[2] = { 0, 0 };
        int have_bits = peb32 >= 0x10000 && ios_av_args_read( base + peb32 + IOS_WN32_PEB_TLSBITS, bits, 8 );

        (void)ios_av_args_read( teb32_host + IOS_I3X_TEB_STACKBASE, &sbase, 4 );
        (void)ios_av_args_read( teb32_host + IOS_I3X_TEB_STACKLIMIT, &slimit, 4 );
        (void)ios_av_args_read( teb32_host + IOS_I3X_TEB_DEALLOC, &dealloc, 4 );
        (void)ios_av_args_read( teb32_host + IOS_I3X_TEB_LASTERROR, &lerr, 4 );
        (void)ios_av_args_read( teb32_host + IOS_I3X_TEB_LASTSTATUS, &lstat, 4 );
        (void)ios_av_args_read( teb32_host + IOS_WN32_TEB_TLSEXP, &tlsexp, 4 );
        dprintf( STDERR_FILENO,
                 "[x86-fault]   thread TEB32=0x%x PEB32=0x%x StackBase=0x%x StackLimit=0x%x DeallocationStack=0x%x (ESP is 0x%x below the base) "
                 "LastErrorValue=0x%x LastStatusValue=0x%08x TlsBitmapBits=%s0x%08x%08x (%d of 64 taken) TlsExpansionSlots=0x%x\n",
                 teb32, peb32, sbase, slimit, dealloc, sbase > esp ? sbase - esp : 0, lerr, lstat,
                 have_bits ? "" : "(unreadable) ", bits[1], bits[0], __builtin_popcount( bits[0] ) + __builtin_popcount( bits[1] ), tlsexp );
    }

    /* the modules */
    {
        size_t l = 0;

        line[0] = 0;
        for (k = 0; k < nmods; k++)
        {
            l += (size_t)snprintf( line + l, sizeof(line) - l, " %s=0x%x/0x%x", mods[k].name, mods[k].base, mods[k].size );
            if (l + 70 >= sizeof(line) || (k % 6) == 5 || k + 1 == nmods)
            {
                dprintf( STDERR_FILENO, "[x86-fault]   modules (name=base/size):%s\n", line );
                l = 0; line[0] = 0;
            }
            if (eip >= mods[k].base && eip - mods[k].base < mods[k].size) in_mod = (int)k;
        }
    }

    /* the PE identity of the module the address is in (the program's exact build) */
    if (in_mod >= 0) ios_i3x_image( base, &mods[in_mod] );

    /* the stack from ESP: raw rows, then the dwords that point into a module */
    {
        uint8_t sv[IOS_X86F_STACK_DWORDS * 4];
        unsigned got = esp >= 0x10000 ? ios_i3x_read( base + esp, (uint8_t *)stack, sv, sizeof(sv) ) : 0;

        if (!got) dprintf( STDERR_FILENO, "[x86-fault]   guest stack at ESP=0x%x is not readable\n", esp );
        else
        {
            for (i = 0; i < IOS_X86F_STACK_DWORDS; i += 8)
            {
                size_t l = 0;

                line[0] = 0;
                for (k = 0; k < 8; k++)
                    l += (size_t)(sv[(i + k) * 4] ? snprintf( line + l, sizeof(line) - l, " %08x", stack[i + k] ) : snprintf( line + l, sizeof(line) - l, " ????????" ));
                dprintf( STDERR_FILENO, "[x86-fault]   stack ESP+0x%03x:%s\n", (unsigned)(i * 4), line );
            }
            {
                size_t l = 0;

                line[0] = 0;
                for (i = 0; i < IOS_X86F_STACK_DWORDS; i++)
                {
                    if (!sv[i * 4]) continue;
                    ios_i3x_where( mods, nmods, stack[i], w, sizeof(w) );
                    if (!w[0]) continue;
                    nrefs++;
                    if (l + 140 < sizeof(line)) l += (size_t)snprintf( line + l, sizeof(line) - l, " +0x%03x=%08x(%s)", (unsigned)(i * 4), stack[i], w );
                    if (l + 140 >= sizeof(line))
                    {
                        dprintf( STDERR_FILENO, "[x86-fault]   stack words that point into a module:%s\n", line );
                        l = 0; line[0] = 0;
                    }
                }
                dprintf( STDERR_FILENO, "[x86-fault]   stack words that point into a module (%u):%s\n", nrefs, l ? line : (nrefs ? " (listed above)" : " none") );
            }
        }
    }

    /* the EBP chain */
    {
        uint32_t fp = ebp;

        for (k = 0; k < IOS_X86F_FRAMES; k++)
        {
            uint32_t pair[2] = { 0, 0 };

            if (fp < 0x10000 || (fp & 3) || !ios_av_args_read( base + fp, pair, 8 )) break;
            ios_i3x_where( mods, nmods, pair[1], w, sizeof(w) );
            dprintf( STDERR_FILENO, "[x86-fault]   frame #%u ebp=0x%x saved-ebp=0x%x return=0x%08x%s%s%s\n", k, fp, pair[0], pair[1],
                     w[0] ? " (" : "", w, w[0] ? ")" : "" );
            if (w[0] && nsite < IOS_X86F_SITES) sites[nsite++] = pair[1];
            if (pair[0] <= fp || pair[0] - fp > 0x100000) break;
            fp = pair[0];
        }
    }

    /* the code around the address and before the first return addresses (rows of 32 bytes, "[x86-fault]   code <what> <offset>: ...") */
    ios_x86f_code( base, fault_addr >= 0 ? "block" : "int3", eip, IOS_X86F_CODE_BEFORE, IOS_X86F_CODE_AFTER, mods, nmods );
    for (k = 0; k < nsite; k++)
    {
        char tag[24];

        snprintf( tag, sizeof(tag), "ret#%u", k );
        ios_x86f_code( base, tag, sites[k], 64, 8, mods, nmods );
    }

    /* what the registers point at */
    ios_x86f_object( base, "EAX", eax, mods, nmods );
    ios_x86f_object( base, "ECX", ecx, mods, nmods );
    ios_x86f_object( base, "EDX", edx, mods, nmods );
    ios_x86f_object( base, "EBX", ebx, mods, nmods );
    ios_x86f_object( base, "ESI", esi, mods, nmods );
    ios_x86f_object( base, "EDI", edi, mods, nmods );
    ios_x86f_object( base, "EBP", ebp, mods, nmods );
    dprintf( STDERR_FILENO, "[x86-fault]   end of report #%d\n", num );
}

/* The int3 call: gregs is FEX's guest register file (RAX RCX RDX RBX RSP RBP RSI RDI ...); grip the address after the int3. */
static void ios_x86_fault_int3( const arm_thread_state64_t *st, uintptr_t thread_teb, const uint64_t *gregs, uint64_t grip )
{
    if (grip < 2) return;
    ios_x86_fault_report( st, thread_teb, gregs, grip - 1, -1, -1 );
}

/* The access-violation call: the faulting thread's host state (x4 x7 x5 x6 x8 x9 x10 x11 are EAX ECX EDX EBX ESP EBP ESI EDI of a 32-bit
 * guest in JIT code), the guest rip of the translated block (State.rip) and the host address of the access (the window base plus the guest
 * address). */
static void ios_x86_fault_av( const arm_thread_state64_t *st, uintptr_t thread_teb, uint64_t block_rip, uint64_t host_addr, int write )
{
    static volatile uint64_t last_rip, last_addr;
    uint64_t base = st->__x[19], g[8];
    int64_t guest_addr;

    if (!((base & 0xffffffffULL) == 0 && base >= 0x100000000ULL)) return;
    if (host_addr < base || host_addr - base >= 0x100000000ULL) return;                       /* not an access inside this window */
    if (block_rip == last_rip && host_addr == last_addr) return;                              /* the redelivery of the fault just reported */
    last_rip = block_rip; last_addr = host_addr;
    guest_addr = (int64_t)(host_addr - base);
    g[0] = st->__x[4]; g[1] = st->__x[7]; g[2] = st->__x[5]; g[3] = st->__x[6];
    g[4] = st->__x[8]; g[5] = st->__x[9]; g[6] = st->__x[10]; g[7] = st->__x[11];
    ios_x86_fault_report( st, thread_teb, g, block_rip, guest_addr, write );
}

#endif

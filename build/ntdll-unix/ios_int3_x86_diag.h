/* [int3-x86]: an OPT-IN report for a 32-bit guest's int3 (Chromium's CHECK / IMMEDIATE_CRASH), with the evidence that names the CHECK.
 *
 * SPDX-License-Identifier: GPL-3.0-only
 * Copyright 2026 thebcd69 (BCD)
 * BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
 *
 * WHY THIS EXISTS
 * ---------------
 * Battle.net (builds 594 and 596, the owner's iPhone18,2): the launcher's window shows a spinning logo for ever. Its own log says
 * "Render process was terminated and reloaded automatically" every 5 s and, after 10 times, "Stop auto-reload". The renderers are
 * `Battle.net.exe --type=renderer`, 32-bit Chromium 108.0.5359.125 (Blizzard's own libcef.dll), and 9 of the 12 that got far enough die
 * the same way, 0.1 to 0.3 s after "GpuMemoryThread" was created on their main thread (CrRendererMain; RenderThreadImpl::Init is at
 * viz::Gpu::Create / InitializeWebKit):
 *
 *   [task-exc] BREAKPOINT ... insn=0xd4200000       the guest's int3, translated by FEX into a host BRK
 *   [int3-guest] guest_rip=0x6de800e2               libcef.dll (base 0x6c7b0000) + 0x16d00e1
 *   wine: Unhandled exception 0x80000003 ... exit 0x80000003
 *
 * A release Chromium CHECK is `int3; ud2` (cc 0f 0b in the public CEF 108 x86 build) and prints nothing, and CEF 108's public 32-bit
 * libcef.dll (cef_binary_108.4.13+ga98cd4c+chromium-108.0.5359.125_windows32) is NOT Blizzard's: its .text is 0x84cf874 bytes against
 * Blizzard's 0x84f5ca4, its TLS callbacks sit 0x27800 apart, so the address 0x16d00e1 cannot be looked up in the public symbols directly. The
 * existing [int3-guest] line has the registers and nothing else: no stack, no code, no module list, and its memory reads (BackupRefPtr's
 * `CC CC 0F 0B` test) use the guest address as a host address, which is only right for a 64-bit process.
 *
 * (Found by the logs' registers and the public libcef.dll with its PDB: this int3 is V8's CHECK_EQ(PAGE_READWRITE, old_protection) in
 * v8::base::OS::SetDataReadOnly, see ios_wc_old_prot.h and its switch. This report stays because the next int3 of this program, or of any
 * other 32-bit Chromium helper that gets this far, is named the same way: the stack and code bytes find the function in the public libcef.dll
 * (tools/int3x86-match.py), the CodeView record names Blizzard's own symbol file.)
 *
 * WHAT IT PRINTS (only for a 32-bit guest, at most 3 times per session, every line starts with "[int3-x86]")
 * --------------------------------------------------------------------------------------------------------------------------------
 *  1. the registers, EIP and the window base, each register with module+RVA when it points into a module of the process;
 *  2. the process's modules (name base size) from the PEB32 loader list;
 *  3. the PE identity of the module the int3 is in (and of libcef.dll when that is another one): machine, TimeDateStamp, SizeOfImage,
 *     CheckSum, entry point, the sections, and the DEBUG DIRECTORY with its CodeView record (RSDS GUID, age, PDB path): the exact
 *     symbol file Blizzard's libcef.dll belongs to, whatever the owner's device has installed;
 *  4. the thread's TEB32/PEB32 facts: the stack range, LastErrorValue and LastStatusValue (the last failed Win32/NT call, which a CHECK
 *     that follows a failed allocation or thread creation leaves there) and the TLS index bitmap; then 96 dwords of the guest stack from
 *     ESP, and the ones that point into a module, as module+RVA;
 *  5. the EBP chain (return addresses, up to 24 frames), each with module+RVA;
 *  6. the code around the int3: 256 bytes before it and 32 after (the CHECK's own `cc 0f 0b` is the first row after the ones before it), and 64
 *     bytes before every return address of the chain: bytes of this size find the function in the public libcef.dll offline (the
 *     compiler and the Chromium source are the same, relative calls and absolute addresses aside);
 *  7. 32 bytes at ECX, ESI, EDI and EBX when they point into the stack or heap (the objects the failing function was handed).
 * Reading it: the chain names the caller functions; the bytes name the function (match them, with the relocated operands masked, in
 * the public libcef.dll and read the symbol from its PDB), and the RSDS record names Blizzard's own PDB.
 *
 * WHAT IT NEVER DOES
 * ------------------
 * It reads the guest's memory through ios_av_args_read (a fault-safe Mach read; a page that is not there prints "??" or is left out) and
 * writes only to stderr. It stops nothing, writes no memory, changes no protection and does not touch the thread's state. It is off
 * unless env.MADEIRA_INT3_X86_DUMP = 1 (BCDLauncherSpec.battlenet's sessionConfig, nothing else sets it); with the switch unset the
 * int3 path does exactly what it did before (one call to ios_int3_x86_enabled(), which reads the environment once).
 *
 * The 32-bit register mapping is FEX x32::SRA (EAX x4, ECX x7, EDX x5, EBX x6, ESP x8, EBP x9, ESI x10, EDI x11, window base x19);
 * the [int3-guest] block reads FEX's own register file, which is in the order RAX RCX RDX RBX RSP RBP RSI RDI, and passes it here.
 *
 * Uses ios_av_args_read (ios_av_args_diag.h) and ios_wn32_modules / ios_wn32_where (ios_wow_null_diag.h), so it is included after them.
 */
#ifndef IOS_INT3_X86_DIAG_H
#define IOS_INT3_X86_DIAG_H

#define IOS_I3X_MAX_REPORTS   3
#define IOS_I3X_STACK_DWORDS  96
#define IOS_I3X_CODE_BEFORE   256
#define IOS_I3X_CODE_AFTER    32
#define IOS_I3X_SITE_BEFORE   64
#define IOS_I3X_SITE_AFTER    8
#define IOS_I3X_FRAMES        24
#define IOS_I3X_SITES         12
/* TEB32 offsets of the facts printed about the thread (include/winternl.h TEB32: NT_TIB32 StackBase/StackLimit, LastErrorValue,
 * LastStatusValue, DeallocationStack; asserted against the pinned headers by tests/host/check-int3-x86-diag.py) */
#define IOS_I3X_TEB_STACKBASE  0x04
#define IOS_I3X_TEB_STACKLIMIT 0x08
#define IOS_I3X_TEB_LASTERROR  0x34
#define IOS_I3X_TEB_LASTSTATUS 0xbf4
#define IOS_I3X_TEB_DEALLOC    0xe0c

/* env.MADEIRA_INT3_X86_DUMP = 1 turns the report on; anything else (or nothing) leaves it off. Read once. */
static int ios_int3_x86_enabled( void )
{
    static int enabled = -1;

    if (enabled < 0)
    {
        const char *e = getenv( "MADEIRA_INT3_X86_DUMP" );

        enabled = e && e[0] == '1' && !e[1];
    }
    return enabled;
}

/* Read len bytes at addr; a byte that cannot be read gets valid[i] = 0. Whole-range first, then 16 bytes at a time. */
static unsigned ios_i3x_read( uint64_t addr, uint8_t *buf, uint8_t *valid, unsigned len )
{
    unsigned i, got = 0;

    memset( valid, 0, len );
    memset( buf, 0, len );
    if (ios_av_args_read( addr, buf, len ))
    {
        memset( valid, 1, len );
        return len;
    }
    for (i = 0; i < len; i += 16)
    {
        unsigned n = len - i < 16 ? len - i : 16;

        if (ios_av_args_read( addr + i, buf + i, n ))
        {
            memset( valid + i, 1, n );
            got += n;
        }
    }
    return got;
}

/* "name+0xRVA" (no parentheses) for the module that holds va, or "" */
static const char *ios_i3x_where( const struct ios_wn32_mod *mods, unsigned n, uint32_t va, char *out, size_t outsz )
{
    unsigned i;

    out[0] = 0;
    for (i = 0; i < n; i++)
        if (va >= mods[i].base && va - mods[i].base < mods[i].size)
        {
            snprintf( out, outsz, "%s+0x%x", mods[i].name, va - mods[i].base );
            break;
        }
    return out;
}

static int ios_i3x_icmp( const char *a, const char *b )
{
    for (; *a && *b; a++, b++)
    {
        char x = *a >= 'A' && *a <= 'Z' ? (char)(*a + 32) : *a, y = *b >= 'A' && *b <= 'Z' ? (char)(*b + 32) : *b;

        if (x != y) return x < y ? -1 : 1;
    }
    return *a ? 1 : (*b ? -1 : 0);
}

/* one hex row: "xx xx ?? xx" for len bytes */
static void ios_i3x_hex( char *out, size_t outsz, const uint8_t *buf, const uint8_t *valid, unsigned len )
{
    size_t l = 0;
    unsigned i;

    out[0] = 0;
    for (i = 0; i < len && l + 4 < outsz; i++)
        l += (size_t)(valid[i] ? snprintf( out + l, outsz - l, "%02x ", buf[i] ) : snprintf( out + l, outsz - l, "?? " ));
    if (l) out[l - 1] = 0;
}

/* The PE identity of the module at guest address `base`: headers, sections, debug directory (CodeView RSDS). */
static void ios_i3x_image( uint64_t win, const struct ios_wn32_mod *m )
{
    uint8_t hd[0x400], hv[0x400];
    uint32_t lfanew, nt, nsec, optsz, opt, ep, soi, csum, ts, dbg_rva, dbg_size, tls_rva, tls_size, exp_rva, exp_size, i;
    uint16_t machine, magic;
    uint64_t fnv = 0xcbf29ce484222325ull;
    char line[600];

    if (ios_i3x_read( win + m->base, hd, hv, sizeof(hd) ) < 0x200)
    {
        dprintf( STDERR_FILENO, "[int3-x86]   image %s base=0x%x: headers not readable\n", m->name, m->base );
        return;
    }
    memcpy( &lfanew, hd + 0x3c, 4 );
    if (hd[0] != 'M' || hd[1] != 'Z' || lfanew < 0x40 || lfanew > sizeof(hd) - 0x100 || memcmp( hd + lfanew, "PE\0\0", 4 ))
    {
        dprintf( STDERR_FILENO, "[int3-x86]   image %s base=0x%x: no PE header at the base\n", m->name, m->base );
        return;
    }
    for (i = 0; i < sizeof(hd); i++) fnv = (fnv ^ hd[i]) * 0x100000001b3ull;
    nt = lfanew;
    memcpy( &machine, hd + nt + 4, 2 );
    nsec = hd[nt + 6] | ((uint32_t)hd[nt + 7] << 8);
    memcpy( &ts, hd + nt + 8, 4 );
    optsz = hd[nt + 20] | ((uint32_t)hd[nt + 21] << 8);
    opt = nt + 24;
    memcpy( &magic, hd + opt, 2 );
    memcpy( &ep, hd + opt + 16, 4 );
    memcpy( &soi, hd + opt + 56, 4 );
    memcpy( &csum, hd + opt + 64, 4 );
    dbg_rva = dbg_size = tls_rva = tls_size = exp_rva = exp_size = 0;
    if (magic == 0x10b && opt + 96 + 16 * 8 <= sizeof(hd))
    {
        memcpy( &exp_rva, hd + opt + 96, 4 );  memcpy( &exp_size, hd + opt + 100, 4 );
        memcpy( &dbg_rva, hd + opt + 96 + 6 * 8, 4 );  memcpy( &dbg_size, hd + opt + 100 + 6 * 8, 4 );
        memcpy( &tls_rva, hd + opt + 96 + 9 * 8, 4 );  memcpy( &tls_size, hd + opt + 100 + 9 * 8, 4 );
    }
    dprintf( STDERR_FILENO,
             "[int3-x86]   image %s base=0x%x ldr-size=0x%x: machine=0x%x magic=0x%x TimeDateStamp=0x%08x SizeOfImage=0x%x CheckSum=0x%x "
             "EntryPoint=0x%x export=0x%x+0x%x debug=0x%x+0x%x tls=0x%x+0x%x header-fnv=%016llx\n",
             m->name, m->base, m->size, machine, magic, ts, soi, csum, ep, exp_rva, exp_size, dbg_rva, dbg_size, tls_rva, tls_size,
             (unsigned long long)fnv );

    /* sections */
    {
        size_t l = 0;
        uint32_t sec = opt + optsz;

        line[0] = 0;
        for (i = 0; i < nsec && i < 16 && sec + 40 * (i + 1) <= sizeof(hd) && l + 90 < sizeof(line); i++)
        {
            const uint8_t *s = hd + sec + 40 * i;
            char nm[9];
            uint32_t vsz, va, raw;

            memcpy( nm, s, 8 ); nm[8] = 0;
            memcpy( &vsz, s + 8, 4 ); memcpy( &va, s + 12, 4 ); memcpy( &raw, s + 16, 4 );
            l += (size_t)snprintf( line + l, sizeof(line) - l, " %s@0x%x+0x%x(raw 0x%x)", nm, va, vsz, raw );
        }
        dprintf( STDERR_FILENO, "[int3-x86]   image %s sections:%s\n", m->name, line );
    }

    /* the debug directory: type, size, rva; for CodeView (2) the RSDS record */
    if (dbg_rva && dbg_size >= 28 && dbg_size <= 28 * 12)
    {
        uint8_t dd[28 * 12], dv[28 * 12];
        unsigned entries = dbg_size / 28;

        if (ios_i3x_read( win + m->base + dbg_rva, dd, dv, entries * 28 ) == entries * 28)
            for (i = 0; i < entries; i++)
            {
                const uint8_t *e = dd + 28 * i;
                uint32_t dts, type, dsize, drva, draw;

                memcpy( &dts, e + 4, 4 ); memcpy( &type, e + 12, 4 ); memcpy( &dsize, e + 16, 4 ); memcpy( &drva, e + 20, 4 ); memcpy( &draw, e + 24, 4 );
                dprintf( STDERR_FILENO, "[int3-x86]   image %s debug[%u]: type=%u size=0x%x rva=0x%x fileptr=0x%x TimeDateStamp=0x%08x\n",
                         m->name, i, type, dsize, drva, draw, dts );
                if (type == 2 && drva && dsize >= 24 && dsize <= 600)
                {
                    uint8_t rs[600], rv[600];
                    unsigned n = dsize < sizeof(rs) ? dsize : (unsigned)sizeof(rs);

                    if (ios_i3x_read( win + m->base + drva, rs, rv, n ) == n && !memcmp( rs, "RSDS", 4 ))
                    {
                        uint32_t d1, age;
                        uint16_t d2, d3;
                        char path[260];
                        unsigned k;

                        memcpy( &d1, rs + 4, 4 ); memcpy( &d2, rs + 8, 2 ); memcpy( &d3, rs + 10, 2 ); memcpy( &age, rs + 20, 4 );
                        for (k = 0; k < sizeof(path) - 1 && 24 + k < n && rs[24 + k]; k++)
                            path[k] = rs[24 + k] >= 0x20 && rs[24 + k] < 0x7f ? (char)rs[24 + k] : '?';
                        path[k] = 0;
                        dprintf( STDERR_FILENO,
                                 "[int3-x86]   image %s CodeView RSDS: guid={%08X-%04X-%04X-%02X%02X-%02X%02X%02X%02X%02X%02X} age=%u "
                                 "symbol-key=%08X%04X%04X%02X%02X%02X%02X%02X%02X%02X%02X%X pdb=\"%s\"\n",
                                 m->name, d1, d2, d3, rs[12], rs[13], rs[14], rs[15], rs[16], rs[17], rs[18], rs[19], age,
                                 d1, d2, d3, rs[12], rs[13], rs[14], rs[15], rs[16], rs[17], rs[18], rs[19], age, path );
                    }
                    else dprintf( STDERR_FILENO, "[int3-x86]   image %s CodeView record at 0x%x is not readable or not RSDS\n", m->name, drva );
                }
            }
        else dprintf( STDERR_FILENO, "[int3-x86]   image %s debug directory at 0x%x is not readable\n", m->name, dbg_rva );
    }
    else dprintf( STDERR_FILENO, "[int3-x86]   image %s has no usable debug directory (rva 0x%x size 0x%x)\n", m->name, dbg_rva, dbg_size );
}

/* code bytes [from, from + len) of the guest, 32 per row; `tag` names the first row's origin */
static void ios_i3x_code( uint64_t win, const char *what, uint32_t at, unsigned before, unsigned after, const struct ios_wn32_mod *mods, unsigned nmods )
{
    uint8_t buf[IOS_I3X_CODE_BEFORE + IOS_I3X_CODE_AFTER], valid[IOS_I3X_CODE_BEFORE + IOS_I3X_CODE_AFTER];
    unsigned len = before + after, row, got;
    uint32_t from = at - before;
    char hex[32 * 3 + 4], w[96];

    if (len > sizeof(buf) || at < before) return;
    got = ios_i3x_read( win + from, buf, valid, len );
    ios_i3x_where( mods, nmods, at, w, sizeof(w) );
    dprintf( STDERR_FILENO, "[int3-x86]   code %s: %u bytes before and %u after guest 0x%08x%s%s%s (%u of %u readable); rows are 32 bytes, "
             "the first at 0x%08x\n", what, before, after, at, w[0] ? " (" : "", w, w[0] ? ")" : "", got, len, from );
    for (row = 0; row < len; row += 32)
    {
        unsigned n = len - row < 32 ? len - row : 32;

        ios_i3x_hex( hex, sizeof(hex), buf + row, valid + row, n );
        dprintf( STDERR_FILENO, "[int3-x86]   code %s %+d: %s\n", what, (int)(row - before), hex );
    }
}

/* 32 bytes at a register that points into the stack or heap, as dwords with module+RVA */
static void ios_i3x_object( uint64_t win, const char *reg, uint32_t v, const struct ios_wn32_mod *mods, unsigned nmods )
{
    uint8_t buf[32], valid[32];
    char line[400], w[96];
    size_t l = 0;
    unsigned i;

    if (v < 0x10000 || v > 0xfffe0000u) return;
    ios_i3x_where( mods, nmods, v, w, sizeof(w) );
    if (w[0]) return;
    if (!ios_i3x_read( win + v, buf, valid, sizeof(buf) )) return;
    line[0] = 0;
    for (i = 0; i < 8 && l + 60 < sizeof(line); i++)
    {
        uint32_t d;

        if (!valid[i * 4]) { l += (size_t)snprintf( line + l, sizeof(line) - l, " ??" ); continue; }
        memcpy( &d, buf + i * 4, 4 );
        ios_i3x_where( mods, nmods, d, w, sizeof(w) );
        l += (size_t)snprintf( line + l, sizeof(line) - l, " %x%s%s%s", d, w[0] ? "(" : "", w, w[0] ? ")" : "" );
    }
    dprintf( STDERR_FILENO, "[int3-x86]   [%s=0x%x]:%s\n", reg, v, line );
}

/* gregs: FEX's guest register file, RAX RCX RDX RBX RSP RBP RSI RDI (32-bit values in the low halves); grip: the address after the int3. */
static void ios_int3_x86_diag( const arm_thread_state64_t *st, uintptr_t thread_teb, const uint64_t *gregs, uint64_t grip )
{
    static volatile int reports;
    uint64_t base = st->__x[19], teb32_host = 0;
    uint32_t eax, ecx, edx, ebx, esp, ebp, esi, edi, eip, teb32, peb32 = 0, stack[IOS_I3X_STACK_DWORDS], i;
    int32_t woff = 0;
    struct ios_wn32_mod mods[96];
    unsigned nmods = 0, k, nrefs = 0, nsite = 0;
    char w[96], line[1000];
    int num, in_mod = -1;
    uint32_t sites[IOS_I3X_SITES];

    if (!gregs || grip < 2 || grip > 0xffffffffULL) return;                                   /* a 64-bit guest has its own report */
    if (!((base & 0xffffffffULL) == 0 && base >= 0x100000000ULL)) return;                     /* not a 4 GB window base in x19 */
    /* x18 is the thread's TEB in JIT code (the [int3-guest] line shows it); the registry lookup the caller did can fall back to slot 0,
     * which is another thread's, so x18 wins when it lies in this window */
    if (st->__x[18] >= base && st->__x[18] - base < 0x100000000ULL) thread_teb = (uintptr_t)st->__x[18];
    if (!thread_teb) return;
    if ((num = __sync_add_and_fetch( &reports, 1 )) > IOS_I3X_MAX_REPORTS) return;

    eax = (uint32_t)gregs[0]; ecx = (uint32_t)gregs[1]; edx = (uint32_t)gregs[2]; ebx = (uint32_t)gregs[3];
    esp = (uint32_t)gregs[4]; ebp = (uint32_t)gregs[5]; esi = (uint32_t)gregs[6]; edi = (uint32_t)gregs[7];
    eip = (uint32_t)grip - 1;                                                                   /* the int3 itself */

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

    dprintf( STDERR_FILENO,
             "[int3-x86] rev=ajan18-bnet-cef #%d a 32-bit guest executed int3 at guest 0x%08x (%s), window 0x%llx, %u modules in the PEB32 list; "
             "EAX=0x%x ECX=0x%x EDX=0x%x EBX=0x%x ESI=0x%x EDI=0x%x EBP=0x%x ESP=0x%x\n",
             num, eip, w[0] ? w : "no module", (unsigned long long)base, nmods, eax, ecx, edx, ebx, esi, edi, ebp, esp );

    /* registers that point into a module */
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
        dprintf( STDERR_FILENO, "[int3-x86]   registers that point into a module:%s\n", l ? line : " none" );
    }

    /* what the thread's TEB32 and PEB32 say: the stack range, the last Win32 error and NTSTATUS (a CHECK that follows a failed call, such as a
     * VirtualAlloc or CreateThread that returned an error, finds its status here), and how many of the 64 TLS indices are taken */
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
                 "[int3-x86]   thread TEB32=0x%x PEB32=0x%x StackBase=0x%x StackLimit=0x%x DeallocationStack=0x%x (ESP is 0x%x below the base) "
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
                dprintf( STDERR_FILENO, "[int3-x86]   modules (name=base/size):%s\n", line );
                l = 0; line[0] = 0;
            }
            if (eip >= mods[k].base && eip - mods[k].base < mods[k].size) in_mod = (int)k;
        }
    }

    /* the PE identity of the module with the int3, then of libcef.dll when that is a different one */
    if (in_mod >= 0) ios_i3x_image( base, &mods[in_mod] );
    for (k = 0; k < nmods; k++)
        if ((int)k != in_mod && !ios_i3x_icmp( mods[k].name, "libcef.dll" )) { ios_i3x_image( base, &mods[k] ); break; }

    /* the stack from ESP: raw rows, then the dwords that point into a module */
    {
        uint8_t sv[IOS_I3X_STACK_DWORDS * 4];
        unsigned got = esp >= 0x10000 ? ios_i3x_read( base + esp, (uint8_t *)stack, sv, sizeof(sv) ) : 0;

        if (!got) dprintf( STDERR_FILENO, "[int3-x86]   guest stack at ESP=0x%x is not readable\n", esp );
        else
        {
            for (i = 0; i < IOS_I3X_STACK_DWORDS; i += 8)
            {
                size_t l = 0;

                line[0] = 0;
                for (k = 0; k < 8; k++)
                    l += (size_t)(sv[(i + k) * 4] ? snprintf( line + l, sizeof(line) - l, " %08x", stack[i + k] ) : snprintf( line + l, sizeof(line) - l, " ????????" ));
                dprintf( STDERR_FILENO, "[int3-x86]   stack ESP+0x%03x:%s\n", (unsigned)(i * 4), line );
            }
            {
                size_t l = 0;

                line[0] = 0;
                for (i = 0; i < IOS_I3X_STACK_DWORDS; i++)
                {
                    if (!sv[i * 4]) continue;
                    ios_i3x_where( mods, nmods, stack[i], w, sizeof(w) );
                    if (!w[0]) continue;
                    nrefs++;
                    if (l + 140 < sizeof(line)) l += (size_t)snprintf( line + l, sizeof(line) - l, " +0x%03x=%08x(%s)", (unsigned)(i * 4), stack[i], w );
                    if (l + 140 >= sizeof(line))
                    {
                        dprintf( STDERR_FILENO, "[int3-x86]   stack words that point into a module:%s\n", line );
                        l = 0; line[0] = 0;
                    }
                }
                dprintf( STDERR_FILENO, "[int3-x86]   stack words that point into a module (%u):%s\n", nrefs, l ? line : (nrefs ? " (listed above)" : " none") );
            }
        }
    }

    /* the EBP chain */
    {
        uint32_t fp = ebp;

        for (k = 0; k < IOS_I3X_FRAMES; k++)
        {
            uint32_t pair[2] = { 0, 0 };

            if (fp < 0x10000 || (fp & 3) || !ios_av_args_read( base + fp, pair, 8 )) break;
            ios_i3x_where( mods, nmods, pair[1], w, sizeof(w) );
            dprintf( STDERR_FILENO, "[int3-x86]   frame #%u ebp=0x%x saved-ebp=0x%x return=0x%08x%s%s%s\n", k, fp, pair[0], pair[1],
                     w[0] ? " (" : "", w, w[0] ? ")" : "" );
            if (w[0] && nsite < IOS_I3X_SITES) sites[nsite++] = pair[1];
            if (pair[0] <= fp || pair[0] - fp > 0x100000) break;
            fp = pair[0];
        }
    }

    /* the code around the int3 and before the first return addresses */
    ios_i3x_code( base, "int3", eip, IOS_I3X_CODE_BEFORE, IOS_I3X_CODE_AFTER, mods, nmods );
    for (k = 0; k < nsite; k++)
    {
        char tag[24];

        snprintf( tag, sizeof(tag), "ret#%u", k );
        ios_i3x_code( base, tag, sites[k], IOS_I3X_SITE_BEFORE, IOS_I3X_SITE_AFTER, mods, nmods );
    }

    /* what the registers point at */
    ios_i3x_object( base, "ECX", ecx, mods, nmods );
    ios_i3x_object( base, "ESI", esi, mods, nmods );
    ios_i3x_object( base, "EDI", edi, mods, nmods );
    ios_i3x_object( base, "EBX", ebx, mods, nmods );
    dprintf( STDERR_FILENO, "[int3-x86]   end of report #%d\n", num );
}

#endif

/* ajan6-arkham (2026-10-21): [x87-int64], a FAILURE-PATH DIAGNOSTIC for "a 32-bit guest read a 64-bit integer through the x87
 * unit and the emulator's 64-bit x87 changed it".
 *
 * Log only. Every read goes through ios_av_args_read (a fault-safe Mach read); nothing is written,
 * protected or stopped. Called from one place: the UNHANDLED report of ios_mach_exception_thread, so only for a data
 * fault every earlier handler declined, in JIT code, and only when the 1 KB of host code before the fault holds the
 * emulator's FILD qword / FISTP qword conversion (SCVTF Dd,Xn followed by FCVTZS). At most 3 reports per process
 * (a repeat of the same host pc is not a report), at most 7 lines each.
 *
 * Why: BATMAN: ARKHAM CITY GOTY (Steam 200260, build 576 and, with the same host code, build 574) and INJUSTICE: GODS AMONG US
 * (Steam 242700, G5HGVV and ND8YNB, build 574; four logs, two games), both Unreal Engine 3, die the same way and nowhere else: a 4-byte load
 * (`ldapr w4,[x24]`, the emulator's `mov eax,[edi]`) at an address that is 64 KB aligned and the first byte of a hole or of a
 * freed view, right after a function that reads an 8-byte lock-free list head: FILD QWORD [head] / FISTP QWORD [local]
 * (MSVC's way to read an __int64 atomically), then EAX = low half = the first node, EDX = high half = depth/sequence,
 * then `mov eax,[node]` for the node's Next pointer (the log: [fault-full] x4 = x10 = x11 = the fault address,
 * x5 = x7 = 0x389f0005 = depth 5, sequence 0x389f). On x86 FILD and FISTP carry a 64-bit mantissa, so the pair copies the
 * integer exactly. BCD runs 32-bit games with FEX's X87ReducedPrecision on by default (WOW64 Module.cpp, ml950: 64-bit x87
 * for the frame rate of D3D9 titles), and then the pair becomes int64 -> double -> int64: the JIT code in every one of the four
 * logs reads `scvtf d2,x20 / frinti.2d v2,v2 / fcvtzs.2d v2,v2` (9e620282 6ee19842 4ee1b842). A double keeps 53 bits, so a
 * value of 2^61 and more (the sequence counter sits in the high half) loses its low 9 bits: a node pointer that is not a
 * multiple of 512 comes back rounded to one. In Batman (576) the register x13 = 0xf07ff00 is exactly the tie that
 * rounds to the fault address 0xf080000 (round half to even), which is why the log cannot say whether the node
 * was ever at the fault address. This line settles it in the next log:
 *   - the 64-bit value the guest got (EDX:EAX) decoded as an SLIST/tagged head (Next, depth, sequence) and the size of the
 *     rounding step at that magnitude (the window a true Next could have been rounded from);
 *   - every pointer in the host registers and on the guest stack near ESP/EBP whose 8 bytes are exactly EDX:EAX
 *     ("EXACT": the head really held that value, nothing was lost) or whose 8 bytes round to EDX:EAX but differ
 *     ("LOST": the head holds a different value, the emulator changed it; both values and the difference are printed, and
 *     whether the head's real Next, the node the guest should have read, is mapped and readable).
 * Reading it next time: LOST decides for the emulator's 64-bit x87 (env.FEX_X87REDUCEDPRECISION = 0 is the keyed attempt,
 * rule x87-int64-roundtrip); EXACT with a node address that no view holds decides for a freed list node (the game's own
 * lifetime, or a free we make earlier than Windows would); none found means the head is not in the frame, the lines
 * above still say whether the loss is possible here.
 *
 * x86 registers of a 32-bit guest are mapped as FEX's x32::SRA does it (FEXCore Arm64Emitter.cpp, "eax/edx rearranged for
 * cmpxchg"): EAX = x4, ECX = x7, EDX = x5, EBX = x6, ESP = x8, EBP = x9, ESI = x10, EDI = x11, guest window base = x19,
 * the address temporary of a guest access = x24. A fault whose x24 is not (x19 + the fault address) or whose x19 is not a
 * 4 GB aligned window base is not a 32-bit access and prints only the first line, without guest values. The existing
 * [x86_live] dump reads these registers with the 64-bit mapping and so shows nonsense for a 32-bit guest.
 *
 * Uses the helpers of ios_av_args_diag.h (ios_av_args_pc_is_jit, ios_av_args_read), which it follows in signal_arm64_ios.c.
 */
#ifndef IOS_X87_INT64_DIAG_H
#define IOS_X87_INT64_DIAG_H

/* SCVTF Dd, Xn (signed 64-bit integer to double) and the two forms of FCVTZS the emulator turns FISTP into
 * (vector .2D and scalar Xd,Dn). */
static int ios_x87i_is_scvtf( uint32_t w ) { return (w & 0xfffffc00u) == 0x9e620000u; }
static int ios_x87i_is_fcvtzs( uint32_t w ) { return (w & 0xfffffc00u) == 0x4ee1b800u || (w & 0xfffffc00u) == 0x9e780000u; }

/* What FILD qword + FISTP qword do in the emulator's 64-bit x87: round to a double (nearest, ties to even), then truncate
 * (the FRINTI between them keeps the integer a double already is). FCVTZS saturates, a C cast does not. */
static uint64_t ios_x87i_roundtrip( uint64_t raw )
{
    double d = (double)(int64_t)raw;

    if (d >= 9223372036854775808.0) return 0x7fffffffffffffffULL;
    if (d <= -9223372036854775808.0) return 0x8000000000000000ULL;
    return (uint64_t)(int64_t)d;
}

/* The spacing of the doubles at the magnitude of raw: 1 up to 2^53, then doubling every binade. */
static uint64_t ios_x87i_step( uint64_t raw )
{
    uint64_t m = (int64_t)raw < 0 ? 0 - raw : raw;
    unsigned msb = 0;

    while (msb < 63 && (m >> (msb + 1))) msb++;
    return msb <= 52 ? 1 : 1ULL << (msb - 52);
}

/* The nearest SCVTF + FCVTZS pair in the (up to) 1 KB of host code before pc: offsets back from pc, 0 when none. */
static int ios_x87i_find_pair( uint64_t pc, unsigned *scvtf_back, unsigned *fcvtzs_back )
{
    uint32_t code[256];
    size_t len = sizeof(code), words, i, j;

    if (pc < 0x10000 + len || !ios_av_args_read( pc - len, code, len ))
    {
        len = 0x100;
        if (pc < 0x10000 + len || !ios_av_args_read( pc - len, code, len )) return 0;
    }
    words = len / 4;
    for (i = words; i-- > 0; )
    {
        if (!ios_x87i_is_scvtf( code[i] )) continue;
        for (j = i + 1; j < words && j <= i + 6; j++)
            if (ios_x87i_is_fcvtzs( code[j] ))
            {
                *scvtf_back = (unsigned)((words - i) * 4);
                *fcvtzs_back = (unsigned)((words - j) * 4);
                return 1;
            }
    }
    return 0;
}

static void ios_x87_int64_diag( const arm_thread_state64_t *st, int exception, uintptr_t fault_addr )
{
    static volatile int reports;
    static volatile uint64_t seen[3];
    uint64_t pc = __darwin_arm_thread_state64_get_pc( *st ), base = st->__x[19], got, step;
    uint32_t eax, edx, ecx, ebx, esp, ebp, esi, edi, win;
    unsigned scvtf_back = 0, fcvtzs_back = 0, k, nc = 0, nshown = 0, nlost = 0, nexact = 0, i;
    struct { uint32_t p; char label[24]; } cand[96];
    int num, wow32;

    if (exception != EXC_BAD_ACCESS || !ios_av_args_pc_is_jit( pc )) return;
    for (k = 0; k < 3; k++) if (seen[k] == pc) return;
    if (!ios_x87i_find_pair( pc, &scvtf_back, &fcvtzs_back )) return;
    if ((num = __sync_add_and_fetch( &reports, 1 )) > 3) return;
    seen[num - 1] = pc;

    wow32 = (base & 0xffffffffULL) == 0 && base >= 0x100000000ULL && fault_addr >= base &&
            fault_addr - base < 0x100000000ULL && st->__x[24] == fault_addr;
    if (!wow32)
    {
        dprintf( STDERR_FILENO,
                 "[x87-int64] rev=ajan6-arkham #%d fault addr=0x%llx at host pc=0x%llx: the host code before it converts a 64-bit integer through a double "
                 "(SCVTF at pc-0x%x, FCVTZS at pc-0x%x), but this is not a 32-bit guest access (x19=0x%llx x24=0x%llx): no guest values\n",
                 num, (unsigned long long)fault_addr, (unsigned long long)pc, scvtf_back, fcvtzs_back,
                 (unsigned long long)base, (unsigned long long)st->__x[24] );
        return;
    }
    eax = (uint32_t)st->__x[4]; ecx = (uint32_t)st->__x[7]; edx = (uint32_t)st->__x[5]; ebx = (uint32_t)st->__x[6];
    esp = (uint32_t)st->__x[8]; ebp = (uint32_t)st->__x[9]; esi = (uint32_t)st->__x[10]; edi = (uint32_t)st->__x[11];
    win = (uint32_t)(fault_addr - base);
    got = ((uint64_t)edx << 32) | eax;
    step = ios_x87i_step( got );
    dprintf( STDERR_FILENO,
             "[x87-int64] rev=ajan6-arkham #%d guest read of 0x%x at host pc=0x%llx: the host code before it converts a 64-bit integer through a double "
             "(FILD/FISTP qword in the emulator's 64-bit x87: SCVTF at pc-0x%x, FCVTZS at pc-0x%x). EDX:EAX = 0x%016llx "
             "(as an SLIST head: Next=0x%x depth=%u sequence=0x%x); doubles are 0x%llx apart at this size, so a true Next "
             "anywhere in [0x%llx, 0x%llx] would have come out as 0x%x\n",
             num, win, (unsigned long long)pc, scvtf_back, fcvtzs_back, (unsigned long long)got, eax, edx & 0xffff, edx >> 16,
             (unsigned long long)step, (unsigned long long)((long long)eax - (long long)(step / 2)),
             (unsigned long long)((long long)eax + (long long)(step / 2)), eax );

    /* Candidate heads: the registers a head pointer is usually in, then the words of the frame. */
    {
        const struct { const char *name; uint32_t v; } regs[] =
        {
            { "ECX", ecx }, { "EBX", ebx }, { "ESI", esi }, { "EDI", edi }, { "EBP", ebp }, { "EDX", edx }, { "EAX", eax },
        };
        uint32_t w[48];

        for (i = 0; i < sizeof(regs) / sizeof(regs[0]); i++)
        {
            snprintf( cand[nc].label, sizeof(cand[nc].label), "%s", regs[i].name );
            cand[nc++].p = regs[i].v;
        }
        if (ios_av_args_read( base + esp, w, sizeof(w) ))
            for (i = 0; i < 48 && nc < 96; i++)
            {
                snprintf( cand[nc].label, sizeof(cand[nc].label), "[ESP+0x%x]", (unsigned)(i * 4) );
                cand[nc++].p = w[i];
            }
        if (ebp >= 0x10040 && ios_av_args_read( base + ebp - 0x40, w, 24 * 4 ))
            for (i = 0; i < 24 && nc < 96; i++)
            {
                int off = (int)(i * 4) - 0x40;
                snprintf( cand[nc].label, sizeof(cand[nc].label), "[EBP%c0x%x]", off < 0 ? '-' : '+', (unsigned)(off < 0 ? -off : off) );
                cand[nc++].p = w[i];
            }
    }
    for (i = 0; i < nc && nshown < 4; i++)
    {
        uint64_t v = 0, rt;
        uint32_t p = cand[i].p;
        unsigned j;
        int dup = 0;

        if (p < 0x10000 || p >= 0xfffe0000u || (p & 3)) continue;
        for (j = 0; j < i; j++) if (cand[j].p == p) dup = 1;
        if (dup || !ios_av_args_read( base + p, &v, 8 ) || !v) continue;
        rt = ios_x87i_roundtrip( v );
        if (v == got)
        {
            nexact++; nshown++;
            dprintf( STDERR_FILENO, "[x87-int64]   %s = 0x%x: its 8 bytes are 0x%016llx = EDX:EAX exactly; nothing was lost here\n",
                     cand[i].label, p, (unsigned long long)v );
        }
        else if (rt == got)
        {
            uint64_t node = 0;
            uint32_t true_next = (uint32_t)v;
            char nodetxt[96];

            nlost++; nshown++;
            if (true_next >= 0x10000 && ios_av_args_read( base + true_next, &node, 8 ))
                snprintf( nodetxt, sizeof(nodetxt), "the real node 0x%x is readable (its first 8 bytes 0x%016llx)", true_next, (unsigned long long)node );
            else snprintf( nodetxt, sizeof(nodetxt), "the real node 0x%x is not readable either", true_next );
            dprintf( STDERR_FILENO,
                     "[x87-int64]   %s = 0x%x: its 8 bytes are 0x%016llx, which the double round trip turns into 0x%016llx = EDX:EAX: LOST, Next 0x%x -> 0x%x; %s\n",
                     cand[i].label, p, (unsigned long long)v, (unsigned long long)rt, true_next, eax, nodetxt );
        }
    }
    if (nlost) dprintf( STDERR_FILENO, "[x87-int64]   verdict: LOST -- the emulator's 64-bit x87 changed the list head the guest read (try env.FEX_X87REDUCEDPRECISION = 0)\n" );
    else if (nexact) dprintf( STDERR_FILENO, "[x87-int64]   verdict: EXACT -- the head held EDX:EAX as read; the node at Next is what is gone (see [fault-rgn] for the views around 0x%x)\n", eax );
    else dprintf( STDERR_FILENO, "[x87-int64]   verdict: head not found in the registers or the frame (no pointer whose 8 bytes equal or round to EDX:EAX)\n" );
}

#endif

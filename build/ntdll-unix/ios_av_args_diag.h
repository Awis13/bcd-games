/* ajan3-dyinglight (2026-10-21): [av-args], a FAILURE-PATH DIAGNOSTIC for "a guest object pointer was NULL".
 *
 * Log only. Every read goes through mach_vm_read_overwrite (fault-safe), nothing is written, protected or
 * stopped. Called from one place: the UNHANDLED report of ios_mach_exception_thread, i.e. for a fault every
 * earlier handler already declined, and only for a NULL-class read/write (fault address below 0x10000) in
 * JIT code. At most 3 reports per process (a repeat of the same host pc is not a report), at most 9 lines each.
 *
 * Why: DYING LIGHT (Steam 239140, SAHIP4-6 on build 575, three identical runs) ends with
 *   engine_x64_rwdi.dll+0x332ee   mov eax,[rax+0x4c]   with RAX = 0
 * right after `call [rax+0x330]` on a heap singleton: `obj = singleton->vfunc102( &buf, "<name>" )`, no NULL check.
 * The session log shows the registers as plain numbers ([x86_live]) and an RCX dump, but not WHICH name was looked
 * up, nor which class the singleton is, nor which of the other registers is a code/data pointer into a game module.
 * Without the engine DLL on the Mac (it is only on the phone) the numbers cannot be turned into names. This prints,
 * for each x86 register (read from the host registers of the faulting JIT block, the same mapping [x86_live] uses),
 * only what it points at:
 *   - an address inside a registered 64-bit module:   module+0xRVA   (a function, a vtable, a string in .rdata)
 *   - the first qword at that address when it is itself such an address:  [reg]=module+0xRVA   (an object's vtable)
 *   - printable text there (ASCII, or UTF-16 with a zero high byte), 3+ characters, NUL-terminated: "text"
 * Plain integers, stack addresses and heap pointers with none of the above print nothing; a register that equals an
 * earlier one is folded into that line (RDX=RSI). With the engine DLL the module+RVA values name the class (RTTI at
 * the vtable) and the callee; without it, the text alone usually says which lookup failed.
 */
#ifndef IOS_AV_ARGS_DIAG_H
#define IOS_AV_ARGS_DIAG_H

/* 1 for an address inside the JIT pool or region C (the places generated code lives). */
static int ios_av_args_pc_is_jit( uint64_t pc )
{
    extern void *ios_jit_rx_base_global;
    extern size_t ios_jit_pool_size_global;
    uint64_t rx = (uint64_t)(uintptr_t)ios_jit_rx_base_global;

    return (rx && pc >= rx && pc < rx + ios_jit_pool_size_global) || ios_jit_low_contains( (uintptr_t)pc );
}

static int ios_av_args_read( uint64_t addr, void *buf, size_t len )
{
    mach_vm_size_t got = 0;

    return addr >= 0x10000 && addr < 0x800000000000ULL &&
           mach_vm_read_overwrite( mach_task_self(), (mach_vm_address_t)addr, len,
                                   (mach_vm_address_t)buf, &got ) == KERN_SUCCESS && got == len;
}

/* "module+0xRVA" when va lies inside a registered 64-bit module image, else 0. */
static int ios_av_args_module( char *out, size_t outsz, uint64_t va )
{
    extern unsigned long long ios_jit_module_base_for_va( unsigned long long va, unsigned long long *size_out );
    unsigned long long msz = 0, base = 0;

    if (va >= 0x10000 && va < 0x800000000000ULL) base = ios_jit_module_base_for_va( va, &msz );
    if (!base) return 0;
    snprintf( out, outsz, "%.32s+0x%llx", ios_pe_module_name( base ), (unsigned long long)(va - base) );
    return 1;
}

/* Printable text at addr: ASCII, or UTF-16LE of ASCII characters, 3 or more characters, ended by a NUL; or, when
 * the readable window ends inside the run, 8 or more characters. A run that stops at any other byte is data, not
 * text. At most 48 characters are copied. The read never crosses a 4 KB boundary. Returns 1 with the text in out
 * (outsz >= 56). */
static int ios_av_args_text( char *out, size_t outsz, uint64_t addr )
{
    unsigned char b[96];
    size_t len = 0x1000 - (size_t)(addr & 0xfff), i, n = 0, unit;
    int wide = 0, ended, window_ended;

    if (len > sizeof(b)) len = sizeof(b);
    if (len < 4 || outsz < 56 || !ios_av_args_read( addr, b, len )) return 0;
    while (n < len && b[n] >= 0x20 && b[n] < 0x7f) n++;
    if (n < 3)
    {
        wide = 1;
        for (n = 0; 2 * n + 1 < len && b[2 * n] >= 0x20 && b[2 * n] < 0x7f && !b[2 * n + 1]; n++) ;
    }
    unit = wide ? 2 : 1;
    ended = n * unit + unit <= len && !b[n * unit] && (!wide || !b[n * unit + 1]);
    window_ended = n * unit + unit > len;
    if (n < 3 || (!ended && !(window_ended && n >= 8))) return 0;
    for (i = 0; i < n && i < 48; i++) out[i] = (char)b[i * unit];
    out[i] = 0;
    if (n > 48) snprintf( out + i, outsz - i, "..." );
    return 1;
}

static void ios_av_args_diag( const arm_thread_state64_t *st, int exception, uintptr_t fault_addr )
{
    static volatile int reports;
    static volatile uint64_t seen[4];
    uint64_t pc = __darwin_arm_thread_state64_get_pc( *st );
    const struct { const char *name; uint64_t v; } regs[] =
    {
        { "RAX", st->__x[8] },  { "RCX", st->__x[0] },  { "RDX", st->__x[1] },  { "RBX", st->__x[27] },
        { "RSI", st->__x[25] }, { "RDI", st->__x[26] }, { "R8",  st->__x[2] },  { "R9",  st->__x[3] },
        { "R10", st->__x[4] },  { "R11", st->__x[5] },  { "R12", st->__x[19] }, { "R13", st->__x[20] },
        { "R14", st->__x[21] }, { "R15", st->__x[22] },
    };
    char lines[8][320];
    unsigned i, j, n = 0, k;
    int num;

    if (exception != EXC_BAD_ACCESS || fault_addr >= 0x10000 || !ios_av_args_pc_is_jit( pc )) return;
    for (k = 0; k < 4; k++) if (seen[k] == pc) return;
    for (i = 0; i < sizeof(regs) / sizeof(regs[0]) && n < 8; i++)
    {
        char names[40], mod[64], vt[64], txt[64], first[64] = "";
        uint64_t v = regs[i].v, q = 0;
        int in_mod, has_q, has_txt, folded = 0;

        if (v < 0x10000 || v >= 0x800000000000ULL) continue;
        for (j = 0; j < i; j++) if (regs[j].v == v) folded = 1;
        if (folded) continue;
        snprintf( names, sizeof(names), "%s", regs[i].name );
        for (j = i + 1; j < sizeof(regs) / sizeof(regs[0]); j++)
            if (regs[j].v == v && strlen( names ) + strlen( regs[j].name ) + 2 < sizeof(names))
                strcat( strcat( names, "=" ), regs[j].name );
        in_mod = ios_av_args_module( mod, sizeof(mod), v );
        has_q = ios_av_args_read( v, &q, 8 ) && ios_av_args_module( vt, sizeof(vt), q );
        has_txt = ios_av_args_text( txt, sizeof(txt), v );
        if (!in_mod && !has_q && !has_txt) continue;
        if (has_q) snprintf( first, sizeof(first), " [%s]=%s", regs[i].name, vt );
        snprintf( lines[n++], sizeof(lines[0]), "[av-args]   %s=0x%llx%s%s%s%s%s%s\n", names, (unsigned long long)v,
                  in_mod ? " = " : "", in_mod ? mod : "", first,
                  has_txt ? " \"" : "", has_txt ? txt : "", has_txt ? "\"" : "" );
    }
    if (!n) return;
    if ((num = __sync_add_and_fetch( &reports, 1 )) > 3) return;
    seen[num - 1] = pc;
    dprintf( STDERR_FILENO,
             "[av-args] rev=ajan3-dyinglight #%d NULL-class fault addr=0x%llx at host pc=0x%llx; x86 registers that point at a module, an object with a vtable or text:\n",
             num, (unsigned long long)fault_addr, (unsigned long long)pc );
    for (i = 0; i < n; i++) dprintf( STDERR_FILENO, "%s", lines[i] );
}

#endif

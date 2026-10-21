/* ajan7-arkham (2026-10-21): TlsFree reaches into OTHER pseudo-processes' threads. A log line for it, and an opt-in switch.
 *
 * Wine's TlsFree(index) ends in NtSetInformationThread(ThreadZeroTlsCell), whose unix side (virtual_clear_tls_index) clears
 * cell `index` in the TEB of EVERY thread of the process. In Wine a Windows process is a Unix process and teb_list holds only
 * its threads. Here every pseudo-process of a session is a set of threads of one Mach task and teb_list holds them ALL (the
 * Steam client, the Dock host, the game, a launcher the game started). So a TlsFree(i) in one process zeroes TlsSlots[i] of
 * every thread of every other 32-bit process (the WOW_TEB cell) and of every 64-bit one (the TEB cell), including a cell the
 * other process is using right now. Two processes' low TLS indices are the same small numbers, so the collision is not rare
 * in principle; whether it happens depends on when the other process frees one.
 *
 * Why it is written down: BATMAN: ARKHAM CITY GOTY (Steam 200260, build 578, C7DGSN) starts BmLauncher.exe (a .NET Framework
 * launcher, run by Wine Mono) as a child of BatmanAC.exe, and the child dies in libmono, EIP libmono+0x2135C9, on
 * `mov dword [ebx+0x10c], 1` with EBX = 0: Mono's inlined TlsGetValue (mono_native_tls_get_value: TEB->TlsSlots[key]) returned
 * NULL for the key of its SGen thread info, in the middle of mono_init_internal, after the same thread had attached itself.
 * Meanwhile the parent was tearing down (eight worker threads exiting). A cell that was set and is NULL again is exactly what
 * a cross-process ThreadZeroTlsCell does. NOT PROVEN: the log has no TlsFree line. [tls-cross-clear] is the proof or the
 * refutation in the next log, and MADEIRA_TLS_CLEAR_OWN_PROCESS=1 is the keyed attempt.
 *
 * What this header holds (nothing in it writes to a TEB):
 *   - ios_tls_clear_own_process_only(): MADEIRA_TLS_CLEAR_OWN_PROCESS, read once. Unset or anything but "1": the old behaviour, a
 *     TlsFree clears the cell in every thread of the session. "1": only in threads whose PEB is the caller's.
 *   - ios_tls_clear_is_foreign(): the victim thread belongs to another process (both PEBs known and different).
 *   - ios_tls_clear_note(): the [tls-cross-clear] line, for a foreign thread whose cell was NOT zero (a live value). At most 40
 *     lines per run. Printed by default (log only); with the switch on it says "kept" instead of "cleared".
 */
#ifndef IOS_TLS_CLEAR_H
#define IOS_TLS_CLEAR_H

#define IOS_TLS_CROSS_LINES 40

static int ios_tls_clear_own_process_only( void )
{
    static int mode = -1;

    if (mode < 0)
    {
        const char *e = getenv( "MADEIRA_TLS_CLEAR_OWN_PROCESS" );
        mode = e && e[0] == '1' && !e[1];
    }
    return mode;
}

/* 1 when `victim_peb` is the PEB of another process than the caller's. An unknown PEB on either side is never foreign: the old
 * behaviour (clear it) stays. */
static int ios_tls_clear_is_foreign( const void *victim_peb, const void *caller_peb )
{
    return caller_peb && victim_peb && victim_peb != caller_peb;
}

/* kept: 1 when the switch spared the cell, 0 when it was cleared. old: the cell's value (not zero). */
static void ios_tls_clear_note( int kept, unsigned index, unsigned caller_tid, const void *caller_peb, unsigned victim_tid,
                                const void *victim_peb, int victim_32bit, unsigned long long old )
{
    static volatile int lines;
    int n = __sync_add_and_fetch( &lines, 1 );

    if (n > IOS_TLS_CROSS_LINES + 1) return;
    if (n == IOS_TLS_CROSS_LINES + 1)
    {
        dprintf( STDERR_FILENO, "[tls-cross-clear] %d lines printed; the rest of this run's cross-process TlsFree events are not\n",
                 IOS_TLS_CROSS_LINES );
        return;
    }
    dprintf( STDERR_FILENO,
             "[tls-cross-clear] rev=ajan7-arkham TlsFree(%u) by tid %04x (peb %p) %s the live cell of tid %04x (peb %p, %s-bit): was 0x%llx%s\n",
             index, caller_tid, caller_peb, kept ? "SPARED (MADEIRA_TLS_CLEAR_OWN_PROCESS=1)" : "CLEARED", victim_tid, victim_peb,
             victim_32bit ? "32" : "64", old, kept ? "" : " (Windows would not have touched another process)" );
}

#endif

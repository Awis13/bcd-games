/* ajan11-wow64stack (2026-10-23): whose window a dead thread's 32-bit stack lives in.
 *
 * virtual_free_teb() releases the 32-bit stack of a WoW64 thread by adding the window base B to the guest address in the
 * dead thread's TEB32.  exit_thread() does not free its own TEB: it hands the PREVIOUS exiting thread's TEB to the NEXT
 * thread to exit (thread_ios.c, `prev_teb`), and those two threads belong to different pseudo-processes all the time.  B
 * was the FREEING thread's window (ios_wow_base()), not the dead thread's.  Battle.net (build 592, 2026-10-23): a worker
 * of one Chromium child exited, the next exit was a thread of the network-service child, and every Chromium process lays
 * its threads' 8 MB stacks out at the same guest addresses, so the dead worker's guest 0x05dd0000 + the network service's B
 * was the LIVE stack of its Chrome_ChildIOThread.  The release was held 2 s (MADEIRA_FREE_DELAY_MS) and then deleted the
 * view; the thread kept running on pages that had become PROT_NONE and the redelivery guard ended the app.
 *
 * What is decided here is only the address the release is aimed at.  Three inputs, all known to the caller:
 *   caller_base  the freeing thread's window base (0 = it has none)
 *   dead_base    the base of the window that holds the dead thread's TEB (0 = the TEB is in no window)
 *   dead_live    that window is still owned by a running process (0: its owner has exited, teardown pending or done)
 *   old_hits_view  the address the old code would aim at (guest + caller_base) is the base of a live view
 *
 * Default (switch unset): the old address is kept for every case but one -- the release that would delete a live view of
 * another process.  That one is aimed at the dead thread's own stack instead (or dropped when its window has already
 * gone).  A release the old code aimed at nothing stays aimed at nothing (the dead thread's stack is then not freed
 * until its window is torn down, as before).
 * MADEIRA_WOW_STACK_FREE_OWN_WINDOW=1: every release of a dead thread's stack goes to the window it lives in (this also
 *   gives back the stacks the default leaves alone; a live window's address space is then not lost to dead threads).
 * MADEIRA_WOW_STACK_FREE_OWN_WINDOW=0: exactly the old behaviour.
 */
#ifndef IOS_WOW_STACK_FREE_H
#define IOS_WOW_STACK_FREE_H

#include <stdint.h>
#include <stdlib.h>
#include <string.h>

enum
{
    IOS_WSF_OLD  = 0,   /* aim at guest + caller_base, as before */
    IOS_WSF_OWN  = 1,   /* aim at guest + dead_base */
    IOS_WSF_SKIP = 2    /* do not release (the dead thread's window is gone or going) */
};

enum
{
    IOS_WSF_MODE_OFF     = 0,   /* MADEIRA_WOW_STACK_FREE_OWN_WINDOW=0 */
    IOS_WSF_MODE_ALWAYS  = 1,   /* MADEIRA_WOW_STACK_FREE_OWN_WINDOW=1 */
    IOS_WSF_MODE_DEFAULT = 2    /* unset, empty or anything else */
};

static inline int ios_wsf_mode( void )
{
    const char *e = getenv( "MADEIRA_WOW_STACK_FREE_OWN_WINDOW" );

    if (e && e[0] == '0' && !e[1]) return IOS_WSF_MODE_OFF;
    if (e && e[0] == '1' && !e[1]) return IOS_WSF_MODE_ALWAYS;
    return IOS_WSF_MODE_DEFAULT;
}

static inline int ios_wsf_decide( int mode, uintptr_t caller_base, uintptr_t dead_base, int dead_live, int old_hits_view )
{
    if (mode == IOS_WSF_MODE_OFF) return IOS_WSF_OLD;
    if (!dead_base || dead_base == caller_base) return IOS_WSF_OLD;   /* same window, or the TEB is in none: nothing to tell apart */
    if (mode == IOS_WSF_MODE_DEFAULT && !old_hits_view) return IOS_WSF_OLD;
    return dead_live ? IOS_WSF_OWN : IOS_WSF_SKIP;
}

#endif

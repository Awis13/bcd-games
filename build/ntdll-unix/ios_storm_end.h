/* ajan11-wow64stack (2026-10-23): what the redelivery guard ends when it gives up on a fault storm.
 *
 * The guard (signal_arm64_ios.c, "[redeliv] 2000 identical redeliveries") has always ended the WHOLE APP: task_terminate().  BCD
 * runs Wine in-process, so that is also what a storm in a helper of the program's launcher did.  Battle.net (build 592,
 * 2026-10-23): its Chromium network-service helper (a child Windows process) stormed on an unmapped stack and the app closed
 * to the home screen while the launcher and the Wine desktop were fine.
 *
 *   IOS_STORM_WHOLE_APP    as before: task_terminate()
 *   IOS_STORM_END_PROCESS  the faulting thread belongs to a LIVE child process of the session: that process is ended (its master
 *                          socket, child table, JIT allocations and guest window are released the way an exit releases them),
 *                          the faulting thread is ended, and the app keeps running
 *   IOS_STORM_END_THREAD   the faulting thread belongs to a child that has ALREADY ended (a laggard): only that thread is ended,
 *                          as the [zombie] rule does for a fault that is not a storm
 *
 * Everything else keeps the old ending: a fault outside guest code (outside the JIT pool a host lock may be held by the faulting
 * thread), the first process of the session (it has no entry in the child registry), a process of unknown identity, and a child
 * that owns the fixed-base main image (retiring it needs the owning thread's server connection, which a worker thread lacks).
 * MADEIRA_REDELIV_CHILD_ONLY=0 brings the old ending back for every storm.
 */
#ifndef IOS_STORM_END_H
#define IOS_STORM_END_H

#include <stdlib.h>

enum
{
    IOS_STORM_WHOLE_APP   = 0,
    IOS_STORM_END_PROCESS = 1,
    IOS_STORM_END_THREAD  = 2
};

static inline int ios_storm_child_only_enabled( void )
{
    const char *e = getenv( "MADEIRA_REDELIV_CHILD_ONLY" );

    return !(e && e[0] == '0' && !e[1]);
}

static inline int ios_storm_action( int enabled, int have_peb, int peb_dead, int live_child, int owns_fixed_base, int in_guest_code )
{
    if (!enabled || !have_peb || !in_guest_code) return IOS_STORM_WHOLE_APP;
    if (peb_dead) return IOS_STORM_END_THREAD;
    if (!live_child) return IOS_STORM_WHOLE_APP;   /* the session's first process, or a PEB nobody registered */
    if (owns_fixed_base) return IOS_STORM_WHOLE_APP;
    return IOS_STORM_END_PROCESS;
}

#endif

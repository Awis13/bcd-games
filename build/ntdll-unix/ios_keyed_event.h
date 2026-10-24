/* The keyed event a NULL handle means, one per child pseudo-process (OPT-IN).
 *
 * SPDX-License-Identifier: GPL-3.0-only
 * Copyright 2026 thebcd69 (BCD)
 * BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
 *
 * WHY THIS EXISTS
 * ---------------
 * Control (Steam 870780; X49WBQ, QJAQK3, P0DERY, 6AG46N, build 592, iPhone16,2) died 45 to 60 s after the start in the engine's audio thread
 * ("AK::EventManager", Wwise), on a read of 0xa90453f3d10183ff at ntdll.dll+0x68008, in the ARM64EC ntdll's RtlRunOnceComplete. Slay the Spire 2
 * (GZFVM3, build 579) died at the same place on a read of 0xe9010000000887c6. The 8 bytes are not an address: they are the bytes of a function
 * prologue (`sub sp,sp,#0x60; stp x19,x20,[sp,#0x40]`) and of x86 code (`mov byte [rdi+8],1; jmp`).
 *
 * The code is Wine's (dlls/ntdll/sync.c):
 *
 *   RtlRunOnceBeginInitialize, a second caller while the first runs its callback:
 *       next = val & ~3;                                    -- a node on ITS OWN stack
 *       CAS( &once->Ptr, &next | 1, val );  NtWaitForKeyedEvent( 0, &next, FALSE, NULL );   -- sleeps until released
 *   RtlRunOnceComplete:
 *       val = old Ptr & ~3;
 *       while (val) { next = *(ULONG_PTR *)val;  NtReleaseKeyedEvent( 0, (void *)val, FALSE, NULL );  val = next; }
 *
 * The waiter leaves its stack frame only when the release wakes it. In Control's register dump the release before the faulting read had
 * returned x0 = 0xc0000024, STATUS_OBJECT_TYPE_MISMATCH: the NULL handle named an object that is not a keyed event. Stock Wine creates the keyed
 * event of a process in start_main_thread and keeps the handle in the global `keyed_event`. BCD runs every Windows process of a session as a
 * pseudo-process of one unix process, and wine_ios_child_main (loader_ios.c) never made a keyed event for a child: the global holds the FIRST
 * pseudo-process's handle number, which means another object (or none) in a child's handle table. So in a child
 *   - NtWaitForKeyedEvent( 0, ... ) fails at once, the waiter loops, pushes its node again (its own slot, so the node now points to itself) and
 *     spins until the callback ends;
 *   - NtReleaseKeyedEvent( 0, ... ) fails, nothing is woken, and the walk goes on over nodes the waiter has already left behind: a stack slot that
 *     now holds whatever the thread pushed next (here code bytes), read as a pointer.
 * Keyed events matter only to RunOnce in Wine's own code (WaitOnAddress and the critical sections use other primitives), so only a game whose
 * threads race on one InitOnce in a child process meets it: here Wwise's threads starting the audio device (mmdevapi's COM init) in Control.exe, a
 * child of the Dock's host.
 *
 * WHAT THIS CHANGES (only with env.MADEIRA_KEYED_EVENT_PER_PROCESS = 1)
 * ----------------------------------------------------------------------
 * wine_ios_child_main creates the child's own keyed event right after init_thread_stack, where start_main_thread does it, and records it here by
 * the child's PEB. ios_keyed_event() (called by NtWaitForKeyedEvent and NtReleaseKeyedEvent for a NULL handle, in the Wine fork's dlls/ntdll/
 * unix/sync.c) hands the current process's handle back. Without the switch, or for a process with no record (the session's first process, whose
 * global is right), it returns the global: byte for byte the old behaviour. Nothing else reads or writes the table; an explicit handle is never
 * touched.
 *
 * Pure bookkeeping, so tests/host/check-keyed-event-per-process.py can compile this file on the host and run every combination.
 */
#ifndef IOS_KEYED_EVENT_H
#define IOS_KEYED_EVENT_H

#include <stdlib.h>

#define IOS_KEYED_EVENT_SLOTS 64

struct ios_keyed_event_slot
{
    void *peb;       /* NULL = free slot; written last when a record is stored, first when it is dropped */
    void *handle;    /* the child's own keyed event, a handle of THAT process */
};

/* env.MADEIRA_KEYED_EVENT_PER_PROCESS = 1; anything else (or nothing) leaves it off. Read once. */
static inline int ios_keyed_event_enabled( void )
{
    static int enabled = -1;

    if (enabled < 0)
    {
        const char *e = getenv( "MADEIRA_KEYED_EVENT_PER_PROCESS" );

        enabled = e && e[0] == '1' && !e[1];
    }
    return enabled;
}

/* The handle for a NULL handle. `session` is the global keyed_event. A process with no record, no PEB, or the switch off gets `session`. */
static inline void *ios_keyed_event_pick( const struct ios_keyed_event_slot *slots, int count, void *peb, void *session, int enabled )
{
    int i;

    if (!enabled || !peb) return session;
    for (i = 0; i < count; i++)
        if (slots[i].peb == peb && slots[i].handle) return slots[i].handle;
    return session;
}

/* Record `handle` for `peb`. A PEB that is there already (its address was used by a process that has since died) is overwritten; else the first
 * free slot; else, all 64 taken, the slot `*next` points at (round robin: the oldest record belongs to a process long dead). Returns the slot. */
static inline int ios_keyed_event_store( struct ios_keyed_event_slot *slots, int count, int *next, void *peb, void *handle )
{
    int i, idx = -1;

    if (!peb || !handle) return -1;
    for (i = 0; i < count && idx < 0; i++)
        if (slots[i].peb == peb) idx = i;
    for (i = 0; i < count && idx < 0; i++)
        if (!slots[i].peb) idx = i;
    if (idx < 0)
    {
        idx = *next % count;
        *next = (idx + 1) % count;
    }
    slots[idx].peb = NULL;          /* readers match on peb: unpublish first */
    __sync_synchronize();
    slots[idx].handle = handle;
    __sync_synchronize();
    slots[idx].peb = peb;
    return idx;
}

/* Drop the record of `peb` (its pseudo-process is gone and its PEB address may be reused). Returns 1 if there was one. */
static inline int ios_keyed_event_drop( struct ios_keyed_event_slot *slots, int count, void *peb )
{
    int i;

    if (!peb) return 0;
    for (i = 0; i < count; i++)
        if (slots[i].peb == peb)
        {
            slots[i].peb = NULL;
            __sync_synchronize();
            slots[i].handle = NULL;
            return 1;
        }
    return 0;
}

#endif

#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""wineserver: cancel_process_async keeps each async alive while it cancels it (madeira-bcd).

cancel_process_async (wine/server/async.c, the cancel_async request: CancelIo, NtCancelIoFile[Ex]) calls
cancel_async( async ), then goes on to write async->async_cancel and to list_remove( &async->process_entry ).
cancel_async leads to async_terminate, which queues APC_ASYNC_IO to the thread that started the I/O. When
that APC cannot be queued, thread_queue_apc releases it, thread_apc_destroy calls async_set_result on the
spot, and async_set_result drops the queue's reference: for an async whose wait handle is already closed
(every non-blocking one) that is the last reference, so the async is freed INSIDE cancel_async. Wine on
Linux rarely gets there (the APC is queued, and the thread runs it later). On iOS send_thread_signal always
fails (no per-process task port), queue_apc returns 0 for any thread that is not waiting in the server and
only another waiting thread can take the APC over ("[apc-requeue] ... dropped, no waiting thread" in every
log), so it happens whenever the issuing thread cancels its own I/O and is the only one running.

The next lines then run on freed memory. On iOS 26/27 the freed block comes back zeroed, so list_remove
( &async->process_entry ) stores through a NULL link: the whole app dies in req_cancel_async+0x158, thread
ios_doorbell_poll_thread, address 0x8 (BeamNG.drive logs VV1FQ4, DZ6BN4, SRWY27 and SYDJDY: the launcher
cancels I/O after the game process it started died). Where the block is not zeroed the same lines write
into the neighbours of a freed object instead, silently.

Fix, on the cancel path only:
  - every async is grabbed before cancel_async and released after it has been put back on the process list
    (so it is freed only then, when it is on the list and list_remove in async_destroy is valid);
  - an async that cancel_async already completed (async_set_result ran: new flag `completed`) is not
    entered into the cancel group, since nothing would ever decrement the group's count again and the
    waiter of NtCancelIoFileEx would never be woken. It is still counted (the request found something).
An async that is still pending after cancel_async (the APC was queued) goes through exactly the old code.

Named patch-wineserver-*, not patch-wine-*: the CI's i386 farm cache is keyed on tools/patch-wine-*.py, and the farm
builds no server, so this patch must not make it rebuild.

Usage: patch-wineserver-async-cancel.py <wine source dir>. Idempotent; fails by name if an anchor moves.
"""
import pathlib
import sys

MARKER = "madeira-bcd async-cancel-keepalive"

FIELD_OLD = "    unsigned int         is_system :1;    /* background system operation not affecting userspace visible state. */\n"
FIELD_NEW = FIELD_OLD + (
    "    unsigned int         completed :1;    /* async_set_result has completed it (" + MARKER + ") */\n")

INIT_OLD = "    async->is_system     = 0;\n"
INIT_NEW = INIT_OLD + "    async->completed     = 0;\n"

DONE_OLD = """        if (!async->signaled)
        {
            async->signaled = 1;
            wake_up( &async->obj, 0 );
        }

        async_call_completion_callback( async );
        async_complete_cancel( async );
"""
DONE_NEW = """        async->completed = 1;   /* """ + MARKER + """ */

        if (!async->signaled)
        {
            async->signaled = 1;
            wake_up( &async->obj, 0 );
        }

        async_call_completion_callback( async );
        async_complete_cancel( async );
"""

WALK_OLD = """            if (!async->canceled) cancel_async( async );
            if (cancel)
            {
                assert( !async->async_cancel );
                async->async_cancel = cancel;
                cancel->count++;
            }
            list_remove( &async->process_entry );
"""
WALK_NEW = """            /* """ + MARKER + """ (tools/patch-wineserver-async-cancel.py).
             * cancel_async can complete the async on the spot (the APC for its thread could not be
             * queued: on iOS every thread that is not waiting in the server), which drops the last
             * reference and frees it; the lines below then ran on freed memory. Hold it until it is
             * back on the process list. An async that is already completed has no completion left to
             * count the cancel group down, so it does not join the group. */
            grab_object( &async->obj );
            if (!async->canceled) cancel_async( async );
            if (cancel && !async->completed)
            {
                assert( !async->async_cancel );
                async->async_cancel = cancel;
                cancel->count++;
            }
            list_remove( &async->process_entry );
"""

BACK_OLD = """    LIST_FOR_EACH_ENTRY_SAFE( async, next_async, &tracked, struct async, process_entry )
    {
        list_remove( &async->process_entry );
        list_add_tail( &process->asyncs, &async->process_entry );
    }
"""
BACK_NEW = """    LIST_FOR_EACH_ENTRY_SAFE( async, next_async, &tracked, struct async, process_entry )
    {
        list_remove( &async->process_entry );
        list_add_tail( &process->asyncs, &async->process_entry );
        release_object( &async->obj );   /* """ + MARKER + """: the keepalive taken above */
    }
"""

EDITS = ((FIELD_OLD, FIELD_NEW), (INIT_OLD, INIT_NEW), (DONE_OLD, DONE_NEW), (WALK_OLD, WALK_NEW),
         (BACK_OLD, BACK_NEW))


def patch(source):
    """The patched text of server/async.c; raises ValueError naming the anchor that moved."""
    if MARKER in source:
        return source
    for old, _ in EDITS:
        if source.count(old) != 1:
            raise ValueError("async-cancel-keepalive: anchor missing or ambiguous: " + old.strip().splitlines()[0])
    for old, new in EDITS:
        source = source.replace(old, new)
    return source


def main():
    wine = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "wine")
    path = wine / "server/async.c"
    source = path.read_text()
    try:
        result = patch(source)
    except ValueError as error:
        sys.exit(str(error))
    if result == source:
        print("async.c: already patched")
    else:
        path.write_text(result)
        print("async.c: patched")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Keep Windows thread priorities from pulling guest threads onto E-cores.

wineserver's apply_thread_priority (wine/server/thread.c, the __APPLE__
branch) turns every thread's Windows priority into Mach policies on the
thread port when the thread starts and whenever the game changes it: a
precedence importance relative to the task, a throughput and a latency QoS
tier, timeshare. That was written for macOS threads that have no QoS class.
On iOS every guest thread already asks for USER_INTERACTIVE (start_thread,
wine_process_thread), and a NORMAL-priority thread given THROUGHPUT_QOS_TIER_1
and importance 0 ran entirely on the efficiency cores: 32-bit Crysis's main
thread (0024) logged 0 ms on P-cores against ~600 ms/s on E-cores at 2.1-2.6
GHz in both the D3D10 and the -dx9 runs (logs 2026-09-30 13:19 and 14:53,
[xp-t]), while its time-critical threads ran on P-cores at 4.2 GHz.

On WINE_IOS this leaves threads below the realtime band at their pthread QoS
(the realtime band keeps its time-constraint policy, which is what audio
threads need). [thread-prio] lines record the first requests.
MADEIRA_WIN_THREAD_PRIORITY=1 restores the old mapping.

Idempotent; fails by name if the anchor moves. Run from the repository root.
"""
import pathlib
import sys

PATH = pathlib.Path("wine/server/thread.c")
MARKER = "madeira-bcd: thread priority keeps the pthread QoS"

OLD = """    int effective_priority = get_effective_thread_priority( thread );

    if (!process_port) return;
"""
NEW = """    int effective_priority = get_effective_thread_priority( thread );

#ifdef WINE_IOS
    /* madeira-bcd: thread priority keeps the pthread QoS (tools/patch-wine-thread-qos.py).
     * Below the realtime band the Mach precedence/throughput/latency policies only
     * demote a USER_INTERACTIVE guest thread onto the efficiency cores. */
    if (effective_priority < LOW_REALTIME_PRIORITY)
    {
        static int keep = -1, logged;
        if (keep < 0)
        {
            const char *e = getenv( "MADEIRA_WIN_THREAD_PRIORITY" );
            keep = !(e && e[0] == '1');
        }
        if (logged < 24)
        {
            logged++;
            fprintf( stderr, "[thread-prio] madeira-bcd tid=%04x base=%d effective=%d -> %s\\n",
                     thread->id, thread->base_priority, effective_priority,
                     keep ? "kept at pthread QoS" : "Mach policies (MADEIRA_WIN_THREAD_PRIORITY=1)" );
        }
        if (keep) return;
    }
#endif

    if (!process_port) return;
"""


def main():
    s = PATH.read_text()
    if MARKER in s:
        print("thread.c: already patched")
        return 0
    if s.count(OLD) != 1:
        sys.exit(f"patch-wine-thread-qos: anchor not found once in {PATH}")
    PATH.write_text(s.replace(OLD, NEW))
    print("thread.c: patched")
    return 0


if __name__ == "__main__":
    sys.exit(main())

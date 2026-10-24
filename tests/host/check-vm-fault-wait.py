#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""[vm-fault-wait]: a page fault that has to wait for virtual_mutex says which fault it is before it waits (round tur600); no Wine runs.

Seven tester logs of four games (OMORI N87QMH, Demons Roots 4ZA68R, Saints Row IV ZZ2S3X, ONE PIECE Bounty Rush 13FT14, 2HJEVX, NJ989H,
WCSJBK) stop with no crash and no frame: one thread is parked in memset / memmove under NtFreeVirtualMemory or virtual_map_section, and the
Mach exception thread that serves every guest page fault waits in ios_virtual_handle_fault_for_thread for virtual_mutex, which the parked
thread holds itself. The stack dump names both threads but not the fault. ios_vm_fault_lock (build/ntdll-unix/virtual_ios.c) takes the
mutex as mutex_lock did and, when it is busy, prints "[vm-fault-wait] #N a write|read|other fault at ADDR of tid=T teb=TEB waits for
virtual_mutex" the first 16 times, before it waits. Nothing else changes.

1. The source: the helper sits between its two markers, ahead of the handler; the handler calls it instead of mutex_lock( &virtual_mutex ),
   before it reads the page's protection, and still unlocks with mutex_unlock; no other function calls it.
2. The helper is cut out of the source and compiled on the host with stand-ins for the types it uses (a TEB, EXCEPTION_*_FAULT,
   process_exiting): an idle mutex is taken at once and prints nothing; a busy one prints exactly one line with the address, the kind
   and the thread, and returns only after the holder released it; the 17th and later waits print nothing; while the process exits it
   neither locks nor prints; a recursive mutex held by the calling thread itself is taken again (the handler never runs on the thread it
   serves, so this only shows the helper does not change the mutex's type).

Needs python3 and a C compiler.
"""
from pathlib import Path
import os
import re
import shutil
import subprocess
import tempfile

root = Path(__file__).resolve().parents[2]
src = (root / 'build/ntdll-unix/virtual_ios.c').read_text()
ok = True


def check(what, cond, detail=''):
    global ok
    print(('ok   ' if cond else 'FAIL ') + what + (('\n     ' + detail) if (detail and not cond) else ''))
    ok = ok and bool(cond)


# ---------------------------------------------------------------- source shape
BEGIN, END = '/* ios-vm-fault-wait begin */', '/* ios-vm-fault-wait end */'
check('the helper has its two markers, once each', src.count(BEGIN) == 1 and src.count(END) == 1)
block = src[src.index(BEGIN) + len(BEGIN):src.index(END)] if src.count(BEGIN) == 1 and src.count(END) == 1 else ''
handler = 'NTSTATUS ios_virtual_handle_fault_for_thread( EXCEPTION_RECORD *rec, TEB *teb )'
check('the handler is defined once, after the helper', src.count(handler) == 1 and src.index(END) < src.index(handler))
body = src[src.index(handler):]
body = body[:body.index('\n}\n')]
check('the handler takes the mutex through the helper, before it reads the page protection',
      'ios_vm_fault_lock( addr, err, teb );' in body and 'mutex_lock( &virtual_mutex )' not in body
      and body.index('ios_vm_fault_lock(') < body.index('get_host_page_vprot( page )'))
check('the handler still unlocks with mutex_unlock', body.count('mutex_unlock( &virtual_mutex );') == 1)
check('no other function calls the helper', src.count('ios_vm_fault_lock(') == 2)   # its definition and the handler's call
check('the helper uses a trylock and then the blocking lock of the same mutex, and honours process_exiting',
      'if (process_exiting) return;' in block and 'pthread_mutex_trylock( &virtual_mutex )' in block
      and block.index('pthread_mutex_trylock') < block.index('pthread_mutex_lock( &virtual_mutex )'))
check('the line is the first 16 waits only, through dprintf (never a Wine log macro)',
      'wait_logs < 16' in block and 'dprintf( 2, "[vm-fault-wait]' in block and 'TRACE(' not in block and 'WARN(' not in block)

# ---------------------------------------------------------------- the helper, run on the host
cc = shutil.which('cc') or shutil.which('clang') or shutil.which('gcc')
if not cc:
    check('a C compiler is available', False)
else:
    harness = r'''
#define _GNU_SOURCE
#include <pthread.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>
typedef unsigned long ULONG_PTR;
typedef int BOOL;
typedef struct { struct { void *UniqueProcess; void *UniqueThread; } ClientId; } TEB;
#define EXCEPTION_READ_FAULT 0
#define EXCEPTION_WRITE_FAULT 1
static BOOL process_exiting;
static pthread_mutex_t virtual_mutex;
''' + block + r'''
static double now(void) { struct timespec t; clock_gettime(CLOCK_MONOTONIC, &t); return t.tv_sec + t.tv_nsec / 1e9; }
static void *holder(void *arg)
{
    pthread_mutex_lock(&virtual_mutex);
    usleep((useconds_t)(long)arg * 1000);
    pthread_mutex_unlock(&virtual_mutex);
    return NULL;
}
static double wait_for_busy(long ms, unsigned long err, TEB *teb, void *addr)
{
    pthread_t t;
    pthread_create(&t, NULL, holder, (void *)ms);
    usleep(20000);                                  /* the holder has the mutex */
    double t0 = now();
    ios_vm_fault_lock(addr, err, teb);
    double dt = now() - t0;
    pthread_mutex_unlock(&virtual_mutex);
    pthread_join(t, NULL);
    return dt;
}
int main(int argc, char **argv)
{
    pthread_mutexattr_t attr;
    TEB teb = {{(void *)0x1, (void *)0xaa}};
    int mode = atoi(argv[1]);
    pthread_mutexattr_init(&attr);
    pthread_mutexattr_settype(&attr, PTHREAD_MUTEX_RECURSIVE);
    pthread_mutex_init(&virtual_mutex, &attr);
    if (mode == 0)                                  /* idle: taken at once, silent */
    {
        ios_vm_fault_lock((void *)0x1000, EXCEPTION_WRITE_FAULT, &teb);
        pthread_mutex_unlock(&virtual_mutex);
        printf("idle done\n");
    }
    else if (mode == 1)                             /* busy 150 ms: one line, returns after the release */
    {
        double dt = wait_for_busy(150, EXCEPTION_WRITE_FAULT, &teb, (void *)0x7000000000UL);
        printf("waited %s\n", dt >= 0.1 ? "long" : "short");
    }
    else if (mode == 2)                             /* read and other kinds */
    {
        wait_for_busy(30, EXCEPTION_READ_FAULT, &teb, (void *)0x2000);
        wait_for_busy(30, 8, NULL, (void *)0x3000);
        printf("kinds done\n");
    }
    else if (mode == 3)                             /* twenty waits: sixteen lines */
    {
        for (int i = 0; i < 20; i++) wait_for_busy(40, EXCEPTION_WRITE_FAULT, &teb, (void *)(0x1000UL * (i + 1)));
        printf("many done\n");
    }
    else if (mode == 4)                             /* the process is exiting: no lock, no line, no wait */
    {
        pthread_t t;
        process_exiting = 1;
        pthread_create(&t, NULL, holder, (void *)300L);
        usleep(20000);
        double t0 = now();
        ios_vm_fault_lock((void *)0x1000, EXCEPTION_WRITE_FAULT, &teb);
        double dt = now() - t0;
        pthread_join(t, NULL);
        printf("exiting %s\n", dt < 0.1 ? "no wait" : "waited");
    }
    else if (mode == 5)                             /* recursive mutex held by this thread: taken again */
    {
        pthread_mutex_lock(&virtual_mutex);
        ios_vm_fault_lock((void *)0x1000, EXCEPTION_WRITE_FAULT, &teb);
        pthread_mutex_unlock(&virtual_mutex);
        pthread_mutex_unlock(&virtual_mutex);
        printf("recursive done\n");
    }
    return 0;
}
'''
    with tempfile.TemporaryDirectory() as t:
        c = Path(t) / 'h.c'
        exe = Path(t) / 'h'
        c.write_text(harness)
        r = subprocess.run([cc, '-O1', '-Wall', '-Werror', '-Wno-unused-function', '-pthread', '-o', str(exe), str(c)],
                           capture_output=True, text=True)
        check('the helper compiles on the host with -Wall -Werror', r.returncode == 0, r.stderr[-1500:])
        if r.returncode == 0:
            def run(mode):
                p = subprocess.run([str(exe), str(mode)], capture_output=True, text=True, timeout=30)
                return p.returncode, p.stdout, p.stderr
            rc, out, err = run(0)
            check('an idle mutex: taken at once, nothing printed', rc == 0 and 'idle done' in out and err == '', err)
            rc, out, err = run(1)
            lines = [l for l in err.splitlines() if l.startswith('[vm-fault-wait]')]
            check('a busy mutex: exactly one line, then it waits for the holder',
                  rc == 0 and 'waited long' in out and len(lines) == 1, err)
            check('the line names the number, the kind, the address, the thread and the TEB',
                  len(lines) == 1 and re.fullmatch(r'\[vm-fault-wait\] #1 a write fault at 0x7000000000 of tid=00aa teb=0x[0-9a-f]+ waits for virtual_mutex',
                                                  lines[0]) is not None, err)
            rc, out, err = run(2)
            lines = [l for l in err.splitlines() if l.startswith('[vm-fault-wait]')]
            check('a read fault says read, an unknown kind with no TEB says other and tid 0000',
                  rc == 0 and len(lines) == 2 and ' a read fault at 0x2000 of tid=00aa ' in lines[0]
                  and ' a other fault at 0x3000 of tid=0000 teb=(nil)' in lines[1].replace('teb=0x0', 'teb=(nil)'), err)
            rc, out, err = run(3)
            lines = [l for l in err.splitlines() if l.startswith('[vm-fault-wait]')]
            check('twenty waits print sixteen lines, numbered 1 to 16',
                  rc == 0 and len(lines) == 16 and lines[0].startswith('[vm-fault-wait] #1 ') and lines[-1].startswith('[vm-fault-wait] #16 '), err)
            rc, out, err = run(4)
            check('while the process exits: no lock, no line, no wait', rc == 0 and 'exiting no wait' in out and err == '', out + err)
            rc, out, err = run(5)
            check('a recursive mutex held by the calling thread is taken again and prints nothing',
                  rc == 0 and 'recursive done' in out and err == '', out + err)

print('PASS: vm-fault-wait' if ok else 'FAILED: vm-fault-wait')
raise SystemExit(0 if ok else 1)

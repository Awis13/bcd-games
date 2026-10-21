#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""A 32-bit main program that cannot get its 4 GB window ends the session, on the host (round 579).

Tomb Raider: Underworld ZWAA2K (Steam 8140, direct start, build 576, iPhone 11): the 32-bit main program could not get its
guest window ("[wow-window] main-process reserve FAILED 0xc0000017", 6319 MB free of 4096 MB plus MADEIRA_WOW_MIN_FREE_GB left
over). loader_ios.c's start_main_thread called fatal_error(), which on iOS does pthread_exit(): the session's boot thread
(wine_process_thread) ended in the middle of __wine_main, so the end work after __wine_main (g_wine_running = 0, the wineserver
stopped) never ran, and the app showed "running" for three minutes until the user closed it.

Now the failure reports its status to the exit hook and leaves by exit(): the shim (shims/wine_ios_exit.h, -include'd into every
ntdll unit) long-jumps to the setjmp in wine_process_thread on the boot thread (the normal end of a session) and leaves by
pthread_exit on any other thread, as before.

1. C: the real failure branch, extracted from loader_ios.c, compiled with the real shim: on the boot thread it long-jumps back
   with code 1 and the hook has the status; on another thread it ends that thread (as before) and the hook has the status;
   when the window was reserved it does nothing.
2. Auto-tune: the rule wow-window-main-image-hang (fixedIn 579) matches the old log, and not a log of the new exit.
3. Source checks: only that call changed; fatal_error and every other caller are as they were.

Synthetic data and facts read from the log only: no Wine, no game.
"""
from pathlib import Path
import json, os, re, shutil, subprocess, sys, tempfile

root = Path(__file__).resolve().parents[2]
app = root / 'app/Madeira'
SWIFTC = os.environ.get('SWIFTC') or shutil.which('swiftc') or str(Path.home() / '.local/share/swiftly/bin/swiftc')
CC = os.environ.get('CC') or shutil.which('cc') or shutil.which('clang')
failures = 0


def require(condition, label):
    global failures
    print(('PASS: ' if condition else 'FAIL: ') + label)
    if not condition:
        failures += 1


def block(text, header_text):
    p = text.index(header_text)
    a = text.index('{', p)
    n, b = 1, a + 1
    while n:
        n += (text[b] == '{') - (text[b] == '}')
        b += 1
    return text[p:b]


loader = (root / 'build/ntdll-unix/loader_ios.c').read_text(encoding='utf-8')
shim_path = root / 'build/ntdll-unix/shims/wine_ios_exit.h'
shim = shim_path.read_text(encoding='utf-8')
rules_py = (root / 'build/tools/autotune_rules.py').read_text(encoding='utf-8')
workflow = (root / '.github/workflows/build-ipa.yml').read_text(encoding='utf-8')

main_fail = loader[loader.index('NTSTATUS wst = ios_wow_window_reserve();'):]
main_fail = main_fail[:main_fail.index('else\n            dprintf( STDERR_FILENO, "[wow-window] main-process window reserved')]

# ------------------------------------------------------------------ 1. C: the real branch with the real shim
c = r'''
#define WINE_IOS 1
#include <pthread.h>
#include <setjmp.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <unistd.h>
typedef int32_t NTSTATUS;
#include "''' + str(shim_path) + r'''"
_Thread_local jmp_buf wine_ios_exit_jmpbuf;
_Thread_local volatile int wine_ios_exit_code;
_Thread_local pthread_t wine_ios_main_thread;
_Thread_local int wine_ios_exit_initialized;
static NTSTATUS g_reserve = (NTSTATUS)0xc0000017;
static NTSTATUS ios_wow_window_reserve(void) { return g_reserve; }
static int hook_calls; static int hook_status;
static volatile int passed_the_branch;

/* the failure branch of start_main_thread, as it is in loader_ios.c */
static void start_main_thread_branch(void)
{
    ''' + main_fail + r'''
    passed_the_branch = 1;
}
void wine_launched_process_did_exit(int status) { hook_calls++; hook_status = status; }

static int failed;
static void expect(int cond, const char *what) { printf("%s: %s\n", cond ? "PASS" : "FAIL", what); if (!cond) failed++; }
static volatile int thread_finished_normally;
static void *other_thread(void *arg) { (void)arg; start_main_thread_branch(); thread_finished_normally = 1; return NULL; }

int main(void)
{
    pthread_t t;
    /* the session's boot thread: wine_process_thread's setjmp, then __wine_main -> start_main_thread */
    wine_ios_main_thread = pthread_self();
    wine_ios_exit_initialized = 1;
    if (setjmp(wine_ios_exit_jmpbuf) == 0)
    {
        start_main_thread_branch();
        expect(0, "the boot thread must not come back from the branch by returning");
    }
    else
    {
        expect(wine_ios_exit_code == 1, "on the boot thread exit(1) long-jumps back to wine_process_thread with code 1");
        expect(hook_calls == 1 && (uint32_t)hook_status == 0xc0000017u, "and the exit hook got the status (c0000017) first");
        expect(!passed_the_branch, "the code after the failure was not run");
    }
    /* any other thread leaves by pthread_exit, as fatal_error did; the hook still got the status */
    hook_calls = 0; passed_the_branch = 0;
    pthread_create(&t, NULL, other_thread, NULL);
    pthread_join(t, NULL);
    expect(!thread_finished_normally && !passed_the_branch, "another thread ends there (pthread_exit)");
    expect(hook_calls == 1 && (uint32_t)hook_status == 0xc0000017u, "and the hook has the status");
    /* the window was reserved: nothing happens */
    hook_calls = 0; g_reserve = 0;
    start_main_thread_branch();
    expect(passed_the_branch && hook_calls == 0, "a window that was reserved: the branch does nothing");
    return failed ? 1 : 0;
}
'''
with tempfile.TemporaryDirectory() as tmp:
    tmp = Path(tmp)
    (tmp / 'branch.c').write_text(c)
    exe = tmp / 'branch'
    r = subprocess.run([CC, '-std=gnu11', '-Wall', '-Wno-unused-function', '-Wno-int-conversion', '-o', str(exe), str(tmp / 'branch.c'), '-lpthread'],
                       capture_output=True, text=True)
    if r.returncode:
        sys.stdout.write(r.stderr[-3000:])
        require(False, 'the failure branch compiles with the real shim')
    else:
        r = subprocess.run([str(exe)], capture_output=True, text=True)
        sys.stdout.write(r.stdout)
        if r.returncode:
            sys.stdout.write(r.stderr[-1500:])
            failures += 1

# ------------------------------------------------------------------ 2. auto-tune
FIX = root / 'tests/fixtures/autotune'
fixture = FIX / 'tomb-raider-underworld-main-window-2026-10-21.log'
require(fixture.exists(), 'the fixture (ZWAA2K) is there')
if SWIFTC and fixture.exists():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        exe = tmp / 'replay'
        b = subprocess.run([SWIFTC, '-O', '-parse-as-library', '-swift-version', '5', '-o', str(exe), str(app / 'AutoTuneCore.swift'),
                            str(app / 'AutoTuneRules.generated.swift'), str(root / 'tests/host/autotune_replay.swift')], capture_output=True, text=True)
        if b.returncode:
            sys.stdout.write(b.stderr[-3000:])
            require(False, 'the auto-tune replay compiles')
        else:
            def replay(name, lines, end):
                p = tmp / (name + '.log')
                p.write_text(lines)
                r = subprocess.run([str(exe), '--end', json.dumps(end), str(p)], capture_output=True, text=True)
                return json.loads(r.stdout.splitlines()[0]) if r.stdout.strip() else {}
            tr = fixture.read_text()
            f2 = replay('f', tr, {'killed': True})
            require(f2.get('matched') == ['wow-window-main-image-hang'] and f2.get('status') == 'known',
                    'ZWAA2K (the old hang) matches its rule: known, fixed in 579 (%s %s)' % (f2.get('matched'), f2.get('status')))
            g2 = replay('g', tr + '[WineProc] Wine exited with code 1 (caught by longjmp)\n', {'crashStatus': 3221225495})
            require(g2.get('matched') == [], 'a log that shows the new exit ran does not match it (%s)' % g2.get('matched'))
            h2 = replay('h', ''.join(l for l in tr.splitlines(True) if 'main-process reserve FAILED' not in l), {'killed': True})
            require('wow-window-main-image-hang' not in h2.get('matched', []), 'without the failure line the rule does not match')
chunk = rules_py[rules_py.index('"id": "wow-window-main-image-hang"'):]
chunk = chunk[:chunk.index('\n    },\n')]
require('"lines": []' in chunk and '"fixedIn": 579' in chunk and '"session": "library"' in chunk and '"none"' in chunk,
        'rule wow-window-main-image-hang: a code fix (no lines, fixedIn 579) for library sessions, with a guard for the new exit')

# ------------------------------------------------------------------ 3. source checks
require('wine_launched_process_did_exit( (int)wst )' in main_fail and 'exit( 1 );' in main_fail
        and main_fail.index('wine_launched_process_did_exit( (int)wst )') < main_fail.index('exit( 1 );')
        and 'fatal_error' not in re.sub(r'/\*.*?\*/', '', main_fail, flags=re.S),
        'the main image\'s window failure reports its status, then leaves by exit(): not fatal_error')
require('__attribute__((weak))' in main_fail, 'the hook is weak: a build without the bridge (a host test) links')
fatal = block(loader, 'static void fatal_error( const char *err, ... )\n{')
require('os_log_error( OS_LOG_DEFAULT, "[Wine ntdll] FATAL: %{public}s", buf );\n    pthread_exit( NULL );' in fatal, 'fatal_error is as it was (pthread_exit)')
# the declaration, the definition and nine callers: the main image's window failure was the tenth caller
calls = len(re.findall(r'\bfatal_error\(', re.sub(r'/\*.*?\*/', '', loader, flags=re.S)))
require(calls == 11, 'every other caller of fatal_error is untouched (%d: the declaration, the definition and nine callers)' % calls)
require('longjmp(wine_ios_exit_jmpbuf, 1);' in shim and 'pthread_exit(NULL);' in shim and 'pthread_equal(pthread_self(), wine_ios_main_thread)' in shim,
        'the exit shim is as it was (boot thread long-jumps, any other thread leaves by pthread_exit)')
start_fn = block(loader, 'static void start_main_thread(void)\n{')
require(start_fn.index('ios_main_image_i386') < start_fn.index('NTSTATUS wst = ios_wow_window_reserve();') < start_fn.index('virtual_alloc_first_teb()'),
        'the branch runs for a 32-bit main image only, before the first TEB (children go through wine_ios_child_main and are not touched)')
require('tests/host/check-main-window-exit.py' in workflow, 'CI runs this check')

print('check-main-window-exit: ' + ('PASS' if failures == 0 else 'FAIL (%d)' % failures))
sys.exit(1 if failures else 0)

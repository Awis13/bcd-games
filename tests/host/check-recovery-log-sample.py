#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright 2026 le0-VV (the harness and the filter it exercises: thebcd69/bcd-games#16, submitted with a Signed-off-by under CONTRIBUTING.md)
# Copyright 2026 thebcd69 (BCD) (the changes below)
# Modified by BCD (thebcd69), 2026-10-24: ported into the tree behind the public repository (host check, build step, opt-in switch).
# Not BCD Material: LICENSE-BCD.md section 1 keeps the other author's header and licence on a file that mixes in their text.
"""env.MADEIRA_RECOVERY_LOG_SAMPLE = 1: the debug records of a recovered invalid instruction are sampled; LOG ONLY, no Wine runs.

Based on thebcd69/bcd-games#16 by le0-VV (Minecraft Dungeons II). FEX writes a five-record report of every invalid guest
instruction it recovers from ("D <tid> Exception: Code: C000001D Address: ...", "Reconstructing context", "pc: ...", "Rethrowing
onto guest stack: ...", "Passing through exception"); a program that recovers thousands a second spends its time and its log on
them. With the switch (build/ntdll-unix/loader_ios.c, ios_recovery_log_admit, called from the debug-write wrapper) the first 16 of
each kind and every 1024th after that are written; the ILL report of signal_arm64_ios.c (ios_ill_diag_admit) is kept for the first 8
SIGILLs at FEX's shared thunk and every 1024th. Every other record is kept: other exception codes, access violations, loader
errors, a write that is not one whole line, a record from a thread whose C000001D line was not seen, an unknown tag. Off (default,
and for any value but 1): nothing is left out. Nothing is delivered differently and no guard is relaxed.

The production functions are cut out of the sources and compiled on the host (AddressSanitizer + UBSan). Needs python3 and a C
compiler. `check-recovery-log-sample.py LOG` also replays a session log through the filter and prints what it would have kept.
"""
from pathlib import Path
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[2]
loader = (root / 'build/ntdll-unix/loader_ios.c').read_text()
signal = (root / 'build/ntdll-unix/signal_arm64_ios.c').read_text()
ci = (root / '.github/workflows/build-ipa.yml').read_text()
catalog = (root / 'app/Madeira/ConfigCatalog.generated.swift').read_text()
policy = (root / 'app/Madeira/GameRecommendationsList.swift').read_text()
ok = True


def check(what, cond, detail=''):
    global ok
    print(('ok   ' if cond else 'FAIL ') + what + (('\n     ' + detail) if (detail and not cond) else ''))
    ok = ok and bool(cond)


def function(src, signature):
    a = src.index(signature)
    return src[a:src.index('\n}', a) + 2] + '\n'


option = function(loader, 'static int ios_recovery_log_option(')
recovery = function(loader, 'static int ios_recovery_log_admit(')
ill = function(signal, 'static int ios_ill_diag_admit(')
wrap = function(loader, 'static NTSTATUS ios_wrap_2(')
main_fn = loader[loader.index('DECLSPEC_EXPORT void __wine_main('):]
main_fn = main_fn[:main_fn.index('\n}\n')]
ill_handler = signal[signal.index('static void ill_handler('):signal.index('static inline uint64_t ios_get_reg(')]

# ---------------------------------------------------------------- source shape
check('the switch is read once, by name, and only the value 1 turns it on', 'getenv( "MADEIRA_RECOVERY_LOG_SAMPLE" )' in option
      and loader.count('MADEIRA_RECOVERY_LOG_SAMPLE') >= 1 and 'return e && !strcmp( e, "1" );' in option)
check('it is stored before the first guest thread starts (the first statement of __wine_main)',
      main_fn.index('ios_recovery_log_option(), __ATOMIC_RELAXED') < main_fn.index('main_argc = argc;') < main_fn.index('start_main_thread();'))
check('the debug-write wrapper asks the RTCS cap first, then the sampler, and a left-out line reports its whole length',
      wrap.index('ios_rtcs_admit(') < wrap.index('ios_recovery_log_admit(') < wrap.index('ios_wrap_unix_call(2,')
      and 'g_wine_unix_call_count++;\n        return p->len;' in wrap[wrap.index('ios_recovery_log_admit('):])
check('the signal handler reads the cached switch, not the environment', 'getenv' not in ill and 'ios_recovery_log_sampling' in ill)
check('the ILL report is wrapped after the signal is tracked and before the exception is set up, nothing else is skipped',
      ill_handler.index('ios_track_signal(') < ill_handler.index('const int ill_report = ios_ill_diag_admit(')
      < ill_handler.index('if (ill_report)') < ill_handler.index('setup_exception( sigcontext, &rec );')
      and 'ios_make_wine_logging_safe()' in ill_handler[:ill_handler.index('ios_track_signal(')])
check('build-ipa.yml runs this check', 'python3 tests/host/check-recovery-log-sample.py' in ci)
check('the switch is in the settings catalog, the generator and the list policy (0 or 1)',
      'env.MADEIRA_RECOVERY_LOG_SAMPLE' in catalog and 'env.MADEIRA_RECOVERY_LOG_SAMPLE' in (root / 'build/tools/gen-config-catalog.py').read_text()
      and '"MADEIRA_RECOVERY_LOG_SAMPLE"' in policy and 'case "MADEIRA_RECOVERY_LOG_SAMPLE"' in policy)

# ---------------------------------------------------------------- compiled run
unit = r'''
#define _GNU_SOURCE
#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdarg.h>
static unsigned notes;
static int note(int fd, const char *fmt, ...) { (void)fd; (void)fmt; ++notes; return 0; }
#define dprintf note
typedef struct { int valid; } arm_thread_state64_t;
static uint64_t ios_fault_guest_rip(const arm_thread_state64_t *state, uint64_t pc)
{
    return state->valid && pc == 0x160000100 ? 0x140001000 : 0;
}
int ios_recovery_log_sampling;
''' + option + recovery + ill + r'''
static unsigned repeat(const char *s, unsigned count)
{
    unsigned kept = 0;
    for (unsigned i = 0; i < count; ++i) kept += ios_recovery_log_admit(s, (unsigned)strlen(s));
    return kept;
}
int main(int argc, char **argv)
{
    if (argc == 3 && !strcmp(argv[1], "replay"))
    {
        ios_recovery_log_sampling = 1;
        FILE *f = fopen(argv[2], "r"); assert(f);
        char line[16384];
        unsigned long long total = 0, kept = 0, lines = 0;
        while (fgets(line, sizeof(line), f))
        {
            unsigned n = (unsigned)strlen(line); total += n; ++lines;
            if (ios_recovery_log_admit(line, n)) kept += n;
        }
        fclose(f);
        printf("REPLAY: %llu lines, %llu bytes before, %llu retained by the FEX-record filter\n", lines, total, kept);
        return 0;
    }
    assert(argc == 1);
    const char *records[] = {
        "D BC Exception: Code: C000001D Address: 170000100\n",
        "D BC Reconstructing context\n",
        "D BC pc: 170000100 rip: 140001000\n",
        "D BC Rethrowing onto guest stack: 700001000\n",
        "D BC Passing through exception\n"
    };
    const char *off[] = { NULL, "0", "", "invalid", "true", "11" };
    arm_thread_state64_t enabled_state = { 1 };
    for (unsigned mode = 0; mode < sizeof(off) / sizeof(off[0]); ++mode)
    {
        if (off[mode]) setenv("MADEIRA_RECOVERY_LOG_SAMPLE", off[mode], 1); else unsetenv("MADEIRA_RECOVERY_LOG_SAMPLE");
        ios_recovery_log_sampling = ios_recovery_log_option(); assert(!ios_recovery_log_sampling);
        for (unsigned i = 0; i < 5; ++i) assert(repeat(records[i], 1000) == 1000);
        for (unsigned i = 0; i < 1000; ++i) assert(ios_ill_diag_admit(&enabled_state, 0x160000100));
        assert(!notes);
    }
    setenv("MADEIRA_RECOVERY_LOG_SAMPLE", "1", 1);
    ios_recovery_log_sampling = ios_recovery_log_option(); assert(ios_recovery_log_sampling);
    for (unsigned i = 0; i < 5; ++i) assert(repeat(records[i], 10000) == 25);        /* 16 + 9 (the 1024th ... 9216th) */
    assert(repeat("E BC Invalid instruction in entry block: 140001000\n", 1000) == 1000);
    assert(repeat("A assertion failed\n", 1000) == 1000);
    assert(repeat("00bc:err:module:import_dll required library failed\n", 1000) == 1000);
    assert(repeat("D C0 Reconstructing context\n", 1000) == 1000);                 /* a thread that never logged C000001D */
    assert(repeat("D BC Reconstructing context failed: new diagnostic\n", 1000) == 1000);   /* an unknown tail */
    assert(repeat("D BC Exception: Code: C0000005 Address: 170000100 AV WRITE of DEAD\n", 1) == 1);   /* an access violation resets the thread */
    for (unsigned i = 1; i < 5; ++i) assert(repeat(records[i], 1000) == 1000);
    assert(repeat("D BC Exception: Code: C06D007E Address: 170000100\n", 1) == 1);   /* a delay-load error */
    assert(repeat(records[4], 1000) == 1000);
    repeat(records[0], 1);
    repeat("D 1BC Exception: Code: C000001D Address: 170000100\n", 1);
    assert(repeat(records[1], 1000) == 1000);                                       /* a slot collision keeps the unknown context */
    repeat(records[0], 1);
    assert(repeat("D BC Reconstructing context\nD BC Exception: Code: C0000005 Address: DEAD\n", 1) == 1);   /* a mixed write */
    assert(repeat(records[1], 1000) == 1000);
    repeat(records[0], 1);
    assert(repeat("D BC Reconstructing context", 1) == 1);                          /* no newline */
    assert(repeat(records[1], 1000) == 1000);
    const char *odd[] = { "", "D", "D ", "D 0 pc: x\n", "D 123456789 pc: x\n", "D ZZ pc: x\n", "X BC Reconstructing context\n" };
    for (unsigned i = 0; i < sizeof(odd) / sizeof(odd[0]); ++i) assert(repeat(odd[i], 1) == 1);
    char bounded[] = { 'D', ' ', 'B', 'C', ' ', 'x', '\n' };                        /* not NUL-terminated: ASan watches the reads */
    assert(ios_recovery_log_admit(bounded, sizeof(bounded)) == 1);
    arm_thread_state64_t state = { 1 }; unsigned kept = 0;
    for (unsigned i = 0; i < 1000; ++i) assert(ios_ill_diag_admit(&state, 0x100000100));   /* a native SIGILL is always reported */
    for (unsigned i = 0; i < 25000; ++i) kept += ios_ill_diag_admit(&state, 0x160000100);
    assert(kept == 8 + 25000 / 1024);
    state.valid = 0;
    for (unsigned i = 0; i < 1000; ++i) assert(ios_ill_diag_admit(&state, 0x160000100));   /* an unreadable frame is always reported */
    assert(notes > 0);
    puts("PASS");
    return 0;
}
'''

with tempfile.TemporaryDirectory() as td:
    td = Path(td)
    (td / 't.c').write_text(unit)
    cc = subprocess.run(['cc', '-std=gnu11', '-Wall', '-Wextra', '-Werror', '-fsanitize=address,undefined', '-fno-sanitize-recover=all',
                         str(td / 't.c'), '-o', str(td / 't')], capture_output=True, text=True)
    check('the production filters compile on the host', cc.returncode == 0, cc.stderr[-3000:])
    if cc.returncode == 0:
        r = subprocess.run([str(td / 't')], capture_output=True, text=True)
        check('switch off or any value but 1: every record is kept; on: five kinds sampled 16 + every 1024th, every other record, '
              'mixed write, unknown thread or tag, access violation and native SIGILL kept', r.returncode == 0 and 'PASS' in r.stdout,
              (r.stdout + r.stderr)[-1500:])
        if len(sys.argv) > 1:
            r = subprocess.run([str(td / 't'), 'replay', str(Path(sys.argv[1]).resolve())], capture_output=True, text=True)
            print(r.stdout.strip() or r.stderr.strip())

print('PASS' if ok else 'FAIL')
raise SystemExit(0 if ok else 1)

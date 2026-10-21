#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""The x18 repair decodes LDRSW/LDRSB/LDRSH (unsigned offset); no Wine runs.

iOS zeroes x18 when it preempts a thread; the Mach handler (ios_mach_exception_thread in
build/ntdll-unix/signal_arm64_ios.c) then repairs a TEB access that faulted on the small offset
by doing the load itself. It decoded LDR X, LDR W, LDRB and LDRH (unsigned offset) but not the
sign-extending forms, so the access was declined and the thread died:

  Left 4 Dead 2 (RTTGKM, build 574, iPhone18,2): 200 s of play, then
    [x18-emul3] UNRECOGNIZED insn=b9980e5c pc=0x151b801d8 teb+0x180c   (L34541)
    Unhandled exception code c0000005 (L34691), exit c0000005.
  FINAL FANTASY XIV (TY3K5R): the same instruction, the same offset, in the 32-bit WebView2 setup process.

b9980e5c is `ldrsw x28, [x18, #0x180c]`: FEX's read of the 64-bit TEB's WowTebOffset (a LONG) in the
WOW64 entry code. ios_x18_signed_load now handles the five sign-extending unsigned-offset forms.

Compiles the production helper and checks, with encodings from the AArch64 assembler:
  - LDRSW Xt, LDRSB Xt/Wt and LDRSH Xt/Wt give the sign-extended value (a W target is zero-extended
    to 64 bits, as the CPU does), for values with the sign bit set and clear;
  - the zero-extending loads (LDR W/X, LDRB, LDRH), the store, the unscaled and register-offset
    forms are not taken (they have their own cases);
and textually that the call sits after the existing unsigned-offset switch, only when nothing
decoded the access, and before the "UNRECOGNIZED" log; and that Rt = 31 is not written.
Needs python3 and a C compiler.
"""
from pathlib import Path
import subprocess
import tempfile

root = Path(__file__).resolve().parents[2]
src = (root / 'build/ntdll-unix/signal_arm64_ios.c').read_text()
ok = True


def check(what, cond):
    global ok
    print(('ok   ' if cond else 'FAIL ') + what)
    ok = ok and bool(cond)


start = src.index('static int ios_x18_signed_load( uint32_t insn, uintptr_t ea, uint64_t *val )')
func = src[start:src.index('\n}\n', start) + 3]

harness = r'''
#include <stdint.h>
#include <stdio.h>
#include <string.h>
''' + func + r'''
static int failures;
#define EXPECT(c, msg) do { if (!(c)) { printf("FAIL %s\n", msg); failures++; } } while (0)

static int run(uint32_t insn, const void *mem, uint64_t *out)
{
    return ios_x18_signed_load(insn, (uintptr_t)mem, out);
}

int main(void)
{
    uint64_t v;
    int32_t neg32 = -16, pos32 = 0x12345678;
    int8_t neg8 = -3, pos8 = 0x45;
    int16_t neg16 = -300, pos16 = 0x1234;

    /* ldrsw x28, [x18, #0x180c] (the crash) */
    EXPECT(run(0xb9980e5c, &neg32, &v) && v == 0xfffffffffffffff0ull, "ldrsw negative");
    EXPECT(run(0xb9980e5c, &pos32, &v) && v == 0x12345678ull, "ldrsw positive");
    EXPECT(run(0xb9800640, &neg32, &v) && v == (uint64_t)(int64_t)-16, "ldrsw x0 other offset");
    /* ldrsb x1 / w2 */
    EXPECT(run(0x39800e41, &neg8, &v) && v == (uint64_t)(int64_t)-3, "ldrsb x negative");
    EXPECT(run(0x39800e41, &pos8, &v) && v == 0x45, "ldrsb x positive");
    EXPECT(run(0x39c00e42, &neg8, &v) && v == 0xfffffffdull, "ldrsb w negative: upper 32 bits zero");
    EXPECT(run(0x39c00e42, &pos8, &v) && v == 0x45, "ldrsb w positive");
    /* ldrsh x3 / w4 */
    EXPECT(run(0x79800e43, &neg16, &v) && v == (uint64_t)(int64_t)-300, "ldrsh x negative");
    EXPECT(run(0x79800e43, &pos16, &v) && v == 0x1234, "ldrsh x positive");
    EXPECT(run(0x79c00e44, &neg16, &v) && v == (uint32_t)(int32_t)-300, "ldrsh w negative: upper 32 bits zero");
    EXPECT(run(0x79c00e44, &pos16, &v) && v == 0x1234, "ldrsh w positive");
    /* the Rt field is not part of the decision: ldrsw xzr */
    EXPECT(run(0xb980065f, &neg32, &v), "ldrsw xzr decoded (the caller does not write Rt = 31)");

    /* not taken here */
    EXPECT(!run(0xb9400645, &neg32, &v), "ldr w5, [x18, #4]");
    EXPECT(!run(0xf9400646, &neg32, &v), "ldr x6, [x18, #8]");
    EXPECT(!run(0x39400e47, &neg32, &v), "ldrb w7");
    EXPECT(!run(0x79400e48, &neg32, &v), "ldrh w8");
    EXPECT(!run(0xb9000645, &neg32, &v), "str w5, [x18, #4]");
    EXPECT(!run(0xb8404245, &neg32, &v), "ldur w5, [x18, #4]");
    EXPECT(!run(0xb8aa6a49, &neg32, &v), "ldrsw x9, [x18, x10] (register offset: its own case)");
    EXPECT(!run(0xa9400000, &neg32, &v), "ldp");

    if (failures) return 1;
    puts("helper: ok");
    return 0;
}
'''

with tempfile.TemporaryDirectory() as tmp:
    c = Path(tmp) / 'h.c'
    c.write_text(harness)
    exe = Path(tmp) / 'h'
    build = subprocess.run(['cc', '-O1', '-Wall', '-Werror', '-fsanitize=address,undefined', '-o', str(exe), str(c)],
                           capture_output=True, text=True)
    if build.returncode:
        build = subprocess.run(['cc', '-O1', '-Wall', '-Werror', '-o', str(exe), str(c)], capture_output=True, text=True)
    check('the helper compiles', build.returncode == 0)
    if build.returncode:
        print(build.stderr)
    else:
        run = subprocess.run([str(exe)], capture_output=True, text=True)
        print(run.stdout.rstrip())
        check('the helper decodes the five sign-extending forms and nothing else', run.returncode == 0)

# The use site.
switch = src.index('case 0x79000000: /* STRH Wt */')
use = src.index('if (ios_x18_signed_load( insn, ea, &sv ))', switch)
unrec = src.index('[x18-emul3] UNRECOGNIZED insn=%08x', use)
block = src[src.rindex('if (!emulated)', switch, use):use]
check('the call sits after the unsigned-offset switch and before the UNRECOGNIZED log', switch < use < unrec)
check('it runs only when nothing decoded the access', 'if (!emulated)' in block and src.count('ios_x18_signed_load(') == 2)
site = src[use:src.index('emulated = 1;', use) + 14]
check('Rt = 31 (XZR) is not written', 'if (rt != 31) state.__x[rt] = sv;' in site)
sw = src[src.rindex('switch (insn & 0xffc00000)', 0, switch):switch + 40]
check('the existing unsigned-offset cases are unchanged',
      all(f'case {c}:' in sw for c in ('0xf9400000', '0xb9400000', '0x39400000', '0x79400000',
                                      '0xf9000000', '0xb9000000', '0x39000000', '0x79000000')))

wf = (root / '.github/workflows/build-ipa.yml').read_text()
check('CI runs this check', 'run: python3 tests/host/check-x18-signed-load.py' in wf)

raise SystemExit(0 if ok else 1)

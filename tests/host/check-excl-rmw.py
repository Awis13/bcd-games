#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""LDXR/LDAXR ... AND/ORR/EOR ... STXR/STLXR into a JIT-aliased page: emulated as an atomic
read-modify-write; no Wine runs.

Graveyard Keeper 2 (Unity, three logs of one iPad, build 574) died in ntdll's
    ldaxr w11, [x8] ; and w11, w11, w10 ; stlxr w12, w11, [x8] ; cbnz w12, retry
(a bit cleared in a critical-section word that lives in the RWX memory of its Mono runtime).
The page's physical protection is RX, so the STLXR faults; ml1284's emulation replays an
LL/SC pair as a compare-and-swap on the value the load saw, which it recovers by undoing an
in-place ADD/SUB immediate, and AND loses that value: the pair was refused, the fault was
redelivered until the guard ended the app. build/ntdll-unix/signal_arm64_ios.c now also
understands AND/ORR/EOR/BIC/ORN/EON of another register (ios_excl_decode_op) and applies the
operations to the memory's current value in a compare-and-swap loop (ios_excl_rmw), the result
a retry of the guest's loop would reach. Pairs the emulation already handled (ADD/SUB
immediate only) keep their code path.

Compiles the three helpers from the source and checks, with encodings from the AArch64 assembler:
  - AND, ORR, EOR, BIC, ORN, EON (32 and 64 bit) decode with Rm's value (inverted for the N forms);
    ANDS, a shifted Rm, Rm == the loaded register, another Rd/Rn and a width other than the
    access's are refused; ADD/SUB immediate still decode to what they add;
  - the operations apply in order and are masked to the width;
  - the compare-and-swap loop writes what the operations give for the current value, for every
    access size, and four threads doing it at once lose no update (adds) and clear every bit
    they were given (AND);
and textually that the emulation takes the new path only for a logical operation with the stored
register being the loaded one, keeps the old compare-and-swap for the rest, undoes nothing it
cannot, and refuses an Rm written after its operation.
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


start = src.index('enum { IOS_EXCL_ADD, IOS_EXCL_AND, IOS_EXCL_ORR, IOS_EXCL_EOR };')
code = src[start:src.index('/* x18-derived base register.', start)]

harness = r'''
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <pthread.h>
''' + code + r'''
static int failures;
#define EXPECT(c, msg) do { if (!(c)) { printf("FAIL %s\n", msg); failures++; } } while (0)

static int dec(uint32_t insn, int ld, int size, uint64_t x10, ios_excl_op *op)
{
    uint64_t x[31]; memset(x, 0, sizeof x); x[10] = x10; x[ld] = 0x1111;
    return ios_excl_decode_op(insn, ld, size, x, op);
}

typedef struct { uint32_t *p; int n; int kind; uint64_t arg; } job_t;
static void *adder(void *a)
{
    job_t *j = a; ios_excl_op op = { j->kind, j->arg, -1 }; uint64_t r;
    for (int i = 0; i < j->n; i++) ios_excl_rmw(j->p, 2, &op, 1, 0xffffffffull, &r);
    return NULL;
}

int main(void)
{
    ios_excl_op op;
    const uint64_t M32 = 0xffffffffull;

    EXPECT(dec(0x0a0a016b, 11, 2, 0xfffffffb, &op) && op.kind == IOS_EXCL_AND && op.arg == 0xfffffffb && op.rm == 10, "and w11,w11,w10");
    EXPECT(dec(0x2a0a016b, 11, 2, 0x10, &op) && op.kind == IOS_EXCL_ORR && op.arg == 0x10, "orr");
    EXPECT(dec(0x4a0a016b, 11, 2, 0x10, &op) && op.kind == IOS_EXCL_EOR && op.arg == 0x10, "eor");
    EXPECT(dec(0x0a2a016b, 11, 2, 0x10, &op) && op.kind == IOS_EXCL_AND && op.arg == ~0x10ull, "bic is and with the inverse");
    EXPECT(dec(0x2a2a016b, 11, 2, 0x10, &op) && op.kind == IOS_EXCL_ORR && op.arg == ~0x10ull, "orn");
    EXPECT(dec(0x4a2a016b, 11, 2, 0x10, &op) && op.kind == IOS_EXCL_EOR && op.arg == ~0x10ull, "eon");
    EXPECT(dec(0x8a0a016b, 11, 3, 0x10, &op) && op.kind == IOS_EXCL_AND, "and x11,x11,x10 on a 64-bit access");
    EXPECT(dec(0x0a1f016b, 11, 2, 0x10, &op) && op.kind == IOS_EXCL_AND && op.arg == 0 && op.rm == 31, "Rm = wzr is zero");
    EXPECT(!dec(0x6a0a016b, 11, 2, 0x10, &op), "ands sets flags: refused");
    EXPECT(!dec(0x0a0a056b, 11, 2, 0x10, &op), "a shifted Rm: refused");
    EXPECT(!dec(0x0a0b016b, 11, 2, 0x10, &op), "Rm == the loaded register: refused");
    EXPECT(!dec(0x0a0a014b, 11, 2, 0x10, &op), "Rn != the loaded register: refused");
    EXPECT(!dec(0x0a0a016b, 11, 3, 0x10, &op), "a 32-bit operation on a 64-bit access: refused");
    EXPECT(!dec(0x8a0a016b, 11, 2, 0x10, &op), "a 64-bit operation on a 32-bit access: refused");
    EXPECT(dec(0x11000529, 9, 2, 0, &op) && op.kind == IOS_EXCL_ADD && op.arg == 1, "add w9,w9,#1 as before");
    EXPECT(dec(0x51000529, 9, 2, 0, &op) && op.kind == IOS_EXCL_ADD && op.arg == (uint64_t)-1, "sub w9,w9,#1 as before");
    EXPECT(dec(0x71000529, 9, 2, 0, &op) && op.kind == IOS_EXCL_ADD && op.arg == (uint64_t)-1, "subs w9,w9,#1 as before");
    EXPECT(!dec(0x91000529, 9, 2, 0, &op), "add x9,x9,#1 on a 32-bit access: refused");

    ios_excl_op ops[3] = { { IOS_EXCL_ADD, 5, -1 }, { IOS_EXCL_AND, 0xfffffff0, 10 }, { IOS_EXCL_ORR, 0x100, 9 } };
    EXPECT(ios_excl_apply(ops, 3, 0x1f, M32) == ((0x1f + 5) & 0xfffffff0 | 0x100), "operations apply in order");
    ios_excl_op neg = { IOS_EXCL_ADD, (uint64_t)-1, -1 };
    EXPECT(ios_excl_apply(&neg, 1, 0, M32) == 0xffffffffull, "a 32-bit result wraps at its width");

    uint8_t b8 = 0xff; uint16_t b16 = 0xffff; uint32_t b32 = 0x00000007; uint64_t b64 = ~0ull, r = 0;
    ios_excl_op and_op = { IOS_EXCL_AND, ~0x4ull, 10 };
    ios_excl_rmw(&b8, 0, &and_op, 1, 0xff, &r);          EXPECT(b8 == 0xfb && r == 0xfb, "rmw byte");
    ios_excl_rmw(&b16, 1, &and_op, 1, 0xffff, &r);       EXPECT(b16 == 0xfffb && r == 0xfffb, "rmw halfword");
    ios_excl_rmw(&b32, 2, &and_op, 1, M32, &r);          EXPECT(b32 == 0x3 && r == 0x3, "rmw word: the crash's bit clear");
    ios_excl_rmw(&b64, 3, &and_op, 1, ~0ull, &r);        EXPECT(b64 == ~0x4ull && r == ~0x4ull, "rmw doubleword");

    uint32_t counter = 0; pthread_t t[4]; job_t jobs[4];
    for (int i = 0; i < 4; i++) { jobs[i] = (job_t){ &counter, 50000, IOS_EXCL_ADD, 1 }; pthread_create(&t[i], NULL, adder, &jobs[i]); }
    for (int i = 0; i < 4; i++) pthread_join(t[i], NULL);
    EXPECT(counter == 200000, "four threads adding at once lose no update");
    uint32_t bits = 0xf; pthread_t u[4]; job_t clr[4];
    for (int i = 0; i < 4; i++) { clr[i] = (job_t){ &bits, 20000, IOS_EXCL_AND, ~(1ull << i) }; pthread_create(&u[i], NULL, adder, &clr[i]); }
    for (int i = 0; i < 4; i++) pthread_join(u[i], NULL);
    EXPECT(bits == 0, "four threads clearing their own bit leave none");

    printf(failures ? "HARNESS FAILED\n" : "HARNESS OK\n");
    return failures != 0;
}
'''

with tempfile.TemporaryDirectory(prefix='madeira-excl-rmw-') as d:
    d = Path(d)
    (d / 'h.c').write_text(harness)
    cc = subprocess.run(['cc', '-std=gnu11', '-O1', '-Wall', '-Wno-parentheses', '-o', str(d / 'h'), str(d / 'h.c'), '-lpthread'],
                        capture_output=True, text=True)
    check('the helpers compile from the source', cc.returncode == 0)
    if cc.returncode == 0:
        run = subprocess.run([str(d / 'h')], capture_output=True, text=True)
        out = run.stdout.strip().splitlines()
        check('the decoder, the operations and the atomic loop pass (see the harness)', run.returncode == 0 and out[-1] == 'HARNESS OK')
        for line in out:
            if line.startswith('FAIL'):
                print('     ' + line)
    else:
        print(cc.stderr[-1500:])

# ---- how the emulation uses them --------------------------------------------
blk = src[src.index('/* ml1284: STXR / STLXR'):]
blk = blk[:blk.index('/* iOS-Madeira ml626: SWP')]
check('the loop collects the operations between the load and the store with ios_excl_decode_op',
      'ios_excl_decode_op( prev, ld_rt, size, (const uint64_t *)state.__x, &ops[nops] )' in blk and 'nops < 8' in blk)
check('an Rm written after its operation refuses the emulation',
      'ops[j].rm >= 0 && (int)(prev & 0x1f) == ops[j].rm) clobbered = 1' in blk)
check('only a logical operation takes the new path, and only when the stored register is the loaded one',
      'if (nlogical)' in blk and 'if (rt == ld_rt)' in blk and 'ios_excl_rmw( rw_addr, size, ops, nops, mask, &written );' in blk and
      'if (nlogical && !rmw) emulated = 0;' in blk)
check('everything else keeps the compare-and-swap on the value the load saw, with the same result register',
      'const uint64_t expect = (state.__x[ld_rt] - delta) & mask' in blk and
      'if (rs != 31) state.__x[rs] = ok ? 0 : 1;' in blk and
      '__atomic_compare_exchange_n( (uint32_t *)rw_addr, &e, (uint32_t)val' in blk)
check('the log line says which way it went', 'emulated: read-modify-write on the current value' in blk)

print('PASS: exclusive pairs with a logical operation' if ok else 'FAILED: exclusive pairs with a logical operation')
raise SystemExit(0 if ok else 1)

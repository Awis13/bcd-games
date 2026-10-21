#!/usr/bin/env python3
"""A MAP_FIXED over a guest window that the kernel refuses is retried at the next free range (small address maps only).

Shade: Wrath of Angels (39Z345, C534F0, Z5F6MG; build 576, iPhone13,4, iOS 18.6.2, 63 GB address map, 32-bit program):
a 0x11000-byte section map inside the guest window [0x400000000,0x500000000) ended in STATUS_NO_MEMORY with a 2 GB gap
free ("[laa] ml1070 SPILL", "[va-scan] FAILED window=0x400110000..0x500000000 ... errno=17"), and ucrtbase's init
failed (c0000142). The errno=17 is only map_free_area's no-clobber probe finding our own PROT_NONE reservation;
the placement itself is map_reserved_area's MAP_FIXED over that reservation, which returned NULL in silence, either
because its search found no range or because the MAP_FIXED failed; the log cannot tell which. The same logs show the
window refusing the program's image at its preferred base 0x410000000 (c0000017: the MAP_FIXED's ENOMEM or
create_view's allocation) and taking the relocated map at 0x401310000.

virtual_ios.c now retries the next free range after an ENOMEM from that MAP_FIXED (ios_reserved_retry), only when
ios_wow_small_va_slots is set (a small address map), and says why on the failing path: [reserved-area] (the refusal),
[va-scan] window room (what the free-range table holds), and STAGE= on the [map-sec] and ml985 refusals, so the next
log tells the refusal from the search from create_view. The retry is a recovery for the first reading, not a proof.

Source checks (every host) hold the shape; the retry loop is then compiled and run against fakes.
"""
from pathlib import Path
import os
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[2]
source = (root / 'build/ntdll-unix/virtual_ios.c').read_text()
ok = True


def check(what, cond, detail=''):
    global ok
    print(('ok   ' if cond else 'FAIL ') + what + (('\n     ' + detail) if (detail and not cond) else ''))
    ok = ok and bool(cond)


def function(signature):
    start = source.index(signature)
    return source[start:source.index('\n}', start) + 2] + '\n'


retry_fn = function('static void *ios_reserved_retry(')
typedefs = source[source.index('typedef void *(*ios_reserved_once_fn)'):source.index('static void *ios_reserved_retry(')]
inner_fn = function('static void *map_reserved_area_inner( void *limit_low, void *limit_high, size_t size, int top_down,\n'
                    '                                      int unix_prot, size_t align_mask )\n{\n    struct ios_reserved_ctx')
once_fn = function('static void *map_reserved_area_once( void *limit_low, void *limit_high, void *ctx_, void **refused, int *err )\n{')
room_fn = function('static void ios_log_window_room(')

check('the retry loop only calls the placement again; it maps, protects and unmaps nothing itself',
      all(m not in retry_fn for m in ('mmap(', 'munmap(', 'mprotect(', 'anon_mmap', 'memset(', 'memcpy(')))
check('a retry happens only after the kernel said ENOMEM, only up to the given number of tries',
      'while (!ptr && refused && err == ENOMEM && n < max_tries)' in retry_fn)
check('each retry lies strictly above (bottom-up) or below (top-down) the refused address',
      'if (top_down) high = (char *)refused + size - 1;' in retry_fn
      and 'else low = (char *)refused + align_mask + 1;' in retry_fn)
check('the retry is on only on a small address map (ios_wow_small_va_slots), zero tries elsewhere',
      'ios_wow_small_va_slots ? IOS_RESERVED_RETRY_MAX : 0' in inner_fn and '#define IOS_RESERVED_RETRY_MAX 32' in source)
check('the placement is still one MAP_FIXED over the reservation, and a failure of it is reported, not hidden',
      once_fn.count('anon_mmap_fixed(') == 1 and '*refused = ptr;' in once_fn and '*err = errno;' in once_fn)
check('a search that finds no range still returns NULL with no refusal recorded (nothing is retried)',
      once_fn.count('return NULL;') == 2 and 'refused' not in once_fn[:once_fn.index('if (top_down)')].replace('void **refused', ''))
check('the refusal is logged once-capped, with what Mach says about the address',
      '++ra_n <= 8' in inner_fn and 'ios_va_describe( first_refused' in inner_fn)
check('the free-range census reads only, and runs only on the failure path of a request that cannot relax',
      all(m not in room_fn for m in ('mmap(', 'munmap(', 'mprotect(', 'anon_mmap')) and 'room_n++ >= 8' in room_fn
      and 'if (!ptr && !ceiling_relaxable) ios_log_window_room( start, end, host_size, align_mask );' in source)
check('map_fixed_area (a fixed address) is untouched', source.count('static NTSTATUS map_fixed_area(') == 1
      and 'ios_reserved_retry' not in function('static NTSTATUS map_fixed_area('))

harness = r'''
#include <assert.h>
#include <errno.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
''' + typedefs + retry_fn + r'''
/* a window of candidate 64 KB-aligned addresses; `bad` ones are refused with `bad_err` */
#define NCAND 8
static uintptr_t cand[NCAND];
static int bad[NCAND], bad_err = ENOMEM;
static unsigned calls;

static void *once_fake( void *low, void *high, void *ctx, void **refused, int *err )
{
    size_t size = *(size_t *)ctx;
    int i;
    (void)size; calls++;
    /* bottom-up: the lowest candidate in [low, high - size]; top-down is tested through `topdown` below */
    for (i = 0; i < NCAND; i++)
    {
        uintptr_t a = cand[i];
        if (a < (uintptr_t)low || a + size > (uintptr_t)high) continue;
        if (bad[i]) { *refused = (void *)a; *err = bad_err; return NULL; }
        return (void *)a;
    }
    return NULL;
}
static void *once_fake_td( void *low, void *high, void *ctx, void **refused, int *err )
{
    size_t size = *(size_t *)ctx;
    int i;
    calls++;
    for (i = NCAND - 1; i >= 0; i--)
    {
        uintptr_t a = cand[i];
        if (a < (uintptr_t)low || a + size > (uintptr_t)high) continue;
        if (bad[i]) { *refused = (void *)a; *err = bad_err; return NULL; }
        return (void *)a;
    }
    return NULL;
}

int main(void)
{
    size_t size = 0x14000, align_mask = 0xffff;
    unsigned n; void *first; int err, i;
    void *p;
    for (i = 0; i < NCAND; i++) { cand[i] = 0x400110000UL + (uintptr_t)i * 0x100000; bad[i] = 0; }
    void *lo = (void *)0x400110000UL, *hi = (void *)0x500000000UL;

    /* nothing refused: placed at the first range, no retry, nothing recorded */
    calls = 0;
    p = ios_reserved_retry( lo, hi, size, 0, align_mask, 32, once_fake, &size, &n, &first, &err );
    assert( p == (void *)cand[0] && n == 0 && first == NULL && calls == 1 );

    /* two refusals bottom-up: the third range is taken; the first refusal is the one reported */
    bad[0] = bad[1] = 1; calls = 0;
    p = ios_reserved_retry( lo, hi, size, 0, align_mask, 32, once_fake, &size, &n, &first, &err );
    assert( p == (void *)cand[2] && n == 2 && first == (void *)cand[0] && err == ENOMEM && calls == 3 );

    /* the same on a 512 GB map (zero tries): the refusal is reported and nothing is retried, as before */
    calls = 0;
    p = ios_reserved_retry( lo, hi, size, 0, align_mask, 0, once_fake, &size, &n, &first, &err );
    assert( p == NULL && n == 1 && first == (void *)cand[0] && calls == 1 );

    /* top-down: the highest candidates are refused, the next lower is taken */
    memset( bad, 0, sizeof bad ); bad[NCAND - 1] = bad[NCAND - 2] = 1; calls = 0;
    p = ios_reserved_retry( lo, hi, size, 1, align_mask, 32, once_fake_td, &size, &n, &first, &err );
    assert( p == (void *)cand[NCAND - 3] && n == 2 && first == (void *)cand[NCAND - 1] && calls == 3 );

    /* only ENOMEM is retried: EEXIST (someone else's mapping) is not */
    memset( bad, 0, sizeof bad ); bad[0] = 1; bad_err = EEXIST; calls = 0;
    p = ios_reserved_retry( lo, hi, size, 0, align_mask, 32, once_fake, &size, &n, &first, &err );
    assert( p == NULL && n == 1 && err == EEXIST && calls == 1 );
    bad_err = ENOMEM;

    /* the cap holds */
    for (i = 0; i < NCAND; i++) bad[i] = 1; calls = 0;
    p = ios_reserved_retry( lo, hi, size, 0, align_mask, 3, once_fake, &size, &n, &first, &err );
    assert( p == NULL && calls == 4 && n == 4 );

    /* every candidate refused and nothing left: the loop ends with NULL */
    calls = 0;
    p = ios_reserved_retry( lo, hi, size, 0, align_mask, 32, once_fake, &size, &n, &first, &err );
    assert( p == NULL && calls == NCAND + 1 && n == NCAND );

    /* a search that finds no range at all is not a refusal */
    memset( bad, 0, sizeof bad ); calls = 0;
    p = ios_reserved_retry( (void *)0x600000000UL, (void *)0x700000000UL, size, 0, align_mask, 32, once_fake, &size, &n, &first, &err );
    assert( p == NULL && n == 0 && first == NULL && calls == 1 );

    puts( "PASS: retry after ENOMEM (bottom-up, top-down), none on a 512 GB map, none for other errors, capped, none without a refusal" );
    return 0;
}
'''
with tempfile.TemporaryDirectory(prefix='reserved-area-retry-') as directory:
    temp = Path(directory)
    unit = temp / 'retry.c'
    unit.write_text(harness)
    exe = temp / 'retry'
    subprocess.run([os.environ.get('CC', 'cc'), '-std=c11', '-D_DARWIN_C_SOURCE', '-D_GNU_SOURCE', '-Wall', '-Wextra',
                    '-Werror', str(unit), '-o', str(exe)], check=True)
    r = subprocess.run([str(exe)], capture_output=True, text=True)
    sys.stdout.write(r.stdout)
    check('the retry loop behaves against fake placements', r.returncode == 0, r.stderr)
sys.exit(0 if ok else 1)

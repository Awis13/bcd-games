#!/usr/bin/env python3
"""A child's ntdll copy is never run on pool pages that lost EXEC, and a section cannot reach past its copy.

GTA Vice City (Steam 12110, AE6NQZ build 576 and S0TH0N build 574, same device) ended every start at its
second process: the unnamed 32-bit image the game loads (SizeOfImage 0x56000) has VirtualSize = rva +
0x10000000 in each of its sections, and mprotect_exec's mprotect(RW) of the writable one (.data rva
0x43000, vsize 0x10043000) covered 256 MB of pool from inside the copy. On blessed pool memory a
mprotect(RW) lowers max_prot for good, so the next head range, the child's private ntdll copy, was born
dead ("[pool-poison] HANDED OUT NON-EXEC ... 70 poisoned pages", "[copy-verify] ntdll.dll: 29 of 29 .text
pages BORN NON-EXEC", then [exec-recover] mprotect_rx=-1 errno=13 and 2000 redeliveries).

virtual_ios.c now (1) gives up a head range that is already dead when ntdll's per-child copy is made and
takes the next one that can execute (default on: such a copy always dies), and (2) can keep a writable
section's mprotect inside its copy (env.MADEIRA_POOL_CLAMP_SECTIONS = 1, default off).

Source checks (every host) hold the shape; the helpers are then compiled and run against fakes, and, on a
Darwin host, against real Mach memory whose max protection was lowered to RW (a lost-EXEC page).
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


clamp_fn = function('static size_t ios_pool_section_clamp(')
on_fn = function('static int ios_pool_clamp_sections_on(')
skip_fn = function('static size_t ios_pool_skip_dead_ranges(')
typedefs = source[source.index('typedef size_t (*ios_pool_take_fn)'):source.index('static size_t ios_pool_skip_dead_ranges(')]
first_nonexec = function('static int ios_pool_range_first_nonexec(')

# ---- source shape ----------------------------------------------------------------------------------------------
check('the helpers never write, map or protect memory',
      all(m not in clamp_fn + skip_fn for m in ('mprotect(', 'vm_protect(', 'mmap(', 'memset(', 'memcpy(', 'madvise(')))
check('the clamp key is read once and is off unless it is "1"',
      'getenv( "MADEIRA_POOL_CLAMP_SECTIONS" )' in on_fn and "on = (e && e[0] == '1');" in on_fn
      and 'static int on = -1;' in on_fn)

parent = source[source.index('static inline int mprotect_exec('):source.index('int ios_jit_copy_module_for_child(')]
site = parent[parent.index('size_t sec_page_size = sec_abs_end - (uintptr_t)target;'):]
site = site[:site.index('ERR("iOS JIT:     → mprotect RW FAILED')]
check('parent section loop: the bound is the copy end rounded up to the page (what a normal image can reach)',
      '(char *)jit_rx_base + offset + image_size' in site and 'page_size - 1) & ~(uintptr_t)(page_size - 1);' in site)
check('parent section loop: the range is cut only when the key is on, and only past the bound',
      'if ((uintptr_t)target + sec_page_size > sec_limit)' in site and 'int clamp = ios_pool_clamp_sections_on();' in site
      and 'if (clamp)\n' in site and 'sec_page_size = ios_pool_section_clamp( (uintptr_t)target, sec_page_size, sec_limit );' in site)
check('parent section loop: a section left with no bytes skips the mprotect, a normal one is called as before',
      'if (!sec_page_size)' in site and 'else if (mprotect(target, sec_page_size, PROT_READ | PROT_WRITE) == 0)' in site)

child_fn = source[source.index('int ios_jit_copy_module_for_child('):]
child_fn = child_fn[:child_fn.index('\n}\n#endif  /* WINE_IOS */')]
check('child copy: same bound, same key, cut only past the bound',
      'uintptr_t c_lim = ((uintptr_t)rx_dest + m->size + pg - 1) & ~(uintptr_t)(pg - 1);' in child_fn
      and 'if (c_beg + p_sz > c_lim)' in child_fn and 'if (clamp) p_sz = ios_pool_section_clamp( c_beg, p_sz, c_lim );' in child_fn
      and 'if (p_sz && mprotect(rx_dest + p_off, p_sz, PROT_READ | PROT_WRITE) != 0)' in child_fn)

alloc_at = child_fn.index('offset = ios_pool_alloc_range(alloc_size, head_limit);')
skip_at = child_fn.index('ios_pool_skip_dead_ranges(')
delta_at = child_fn.index('if (offset != (size_t)-1 && data_delta) offset += data_delta;')
check('child copy: a dead range is skipped right after the allocation, before the data-align shift and any write',
      alloc_at < skip_at < delta_at and 'memcpy(rw_dest, src, m->size);' in child_fn
      and child_fn.index('memcpy(rw_dest, src, m->size);') > delta_at)
check('child copy: the skip logs only when it skipped or found nothing', 'if (skipped || still_dead)' in child_fn)
check('the skip is made nowhere else (the parent image path and the anon ranges are unchanged)',
      source.count('= ios_pool_skip_dead_ranges(') == 1)   # the one call; its definition is `static size_t ...`
check('at most 512 further ranges', '#define IOS_POOL_DEAD_SKIP_MAX 512' in source)
check('the dead test is the existing read-only first-non-exec walk over the whole copy',
      'return ios_pool_range_first_nonexec( off, size, size, bad_at, cur, max );' in source)

# ---- compiled helpers against fakes ------------------------------------------------------------------------------
fakes = r'''
#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#define MB ((size_t)1 << 20)
''' + clamp_fn + on_fn + typedefs + skip_fn + r'''
static size_t cursor, swath_lo, swath_hi, takes, pool_end;
static size_t take_fake( size_t size, size_t limit )
{
    size_t o = cursor;
    (void)limit; takes++;
    if (cursor + size > pool_end) return (size_t)-1;
    cursor += size;
    return o;
}
static int dead_fake( size_t off, size_t size, size_t *bad_at, unsigned int *cur, unsigned int *max )
{
    if (off + size <= swath_lo || off >= swath_hi) return 0;
    *bad_at = off < swath_lo ? swath_lo - off : 0; *cur = 3; *max = 3;
    return 1;
}

int main( int argc, char **argv )
{
    unsigned int skipped = 99, bcur = 0, bmax = 0;
    int still = 99;
    size_t bad = 0, got;

    if (argc > 1)   /* key probe: prints the cached value of the env key */
    {
        if (strcmp( argv[1], "unset" )) setenv( "MADEIRA_POOL_CLAMP_SECTIONS", argv[1], 1 );
        printf( "%d\n", ios_pool_clamp_sections_on() );
        return 0;
    }

    /* the clamp: ranges that fit stay whole, ranges that run past the limit are cut, ranges past it are empty */
    assert( ios_pool_section_clamp( 0x1000, 0x2000, 0x10000 ) == 0x2000 );
    assert( ios_pool_section_clamp( 0x1000, 0xf000, 0x10000 ) == 0xf000 );          /* ends exactly at the limit */
    assert( ios_pool_section_clamp( 0x1000, 0x10000, 0x10000 ) == 0xf000 );
    assert( ios_pool_section_clamp( 0x10000, 0x4000, 0x10000 ) == 0 );
    assert( ios_pool_section_clamp( 0x20000, 0x4000, 0x10000 ) == 0 );
    /* the numbers of the Vice City log (AE6NQZ L8708-8712): copy 0x12b325000, SizeOfImage 0x56000, 16 KB pages,
     * .data rva 0x43000 vsize 0x10043000 */
    {
        uintptr_t copy = 0x12b325000UL, pg = 0x4000, image_size = 0x56000, rva = 0x43000, vsz = 0x10043000UL;
        uintptr_t sec_abs = copy + rva;
        uintptr_t end = (sec_abs + vsz + pg - 1) & ~(pg - 1);
        uintptr_t target = sec_abs & ~(pg - 1);
        uintptr_t limit = (copy + image_size + pg - 1) & ~(pg - 1);
        size_t want = end - target;
        assert( target == 0x12b368000UL && end == 0x13b3ac000UL && want == 0x10044000UL );
        assert( target + want > limit );                               /* the overrun the log named */
        assert( limit == 0x12b37c000UL && limit <= 0x12b380000UL );    /* the first poisoned page is 0x12b380000 */
        assert( ios_pool_section_clamp( target, want, limit ) == 0x14000 );
        /* a well-formed .data (ends at the image end) is never touched by the bound */
        vsz = image_size - rva;
        end = (sec_abs + vsz + pg - 1) & ~(pg - 1);
        assert( end <= limit );
        assert( ios_pool_section_clamp( target, end - target, limit ) == end - target );
    }

    /* the skip: Vice City's shape. A 0x118000 copy at 0xa474000 lies inside a dead run [0xa46c000, 0x1a46c000) */
    pool_end = 0x1cff8000; cursor = 0xa474000; swath_lo = 0xa46c000; swath_hi = 0x1a46c000; takes = 0;
    got = ios_pool_skip_dead_ranges( take_fake( 0x118000, 0 ), 0x118000, 0x1cff8000, 512, take_fake, dead_fake,
                                     &skipped, &still, &bad, &bcur, &bmax );
    assert( !still && got >= swath_hi && got + 0x118000 <= pool_end );
    assert( skipped >= 200 && skipped <= 260 );          /* ~0x10000000 / 0x118000 = 232 */
    assert( takes == skipped + 1 );
    assert( bad == 0 && bcur == 3 && bmax == 3 );

    /* a live first range is used as it is: no further range is taken */
    cursor = 0x1b000000; takes = 0;
    got = ios_pool_skip_dead_ranges( take_fake( 0x118000, 0 ), 0x118000, 0x1cff8000, 512, take_fake, dead_fake,
                                     &skipped, &still, &bad, &bcur, &bmax );
    assert( got == 0x1b000000 && skipped == 0 && !still && takes == 1 );

    /* nothing live before the pool ends: the range in hand comes back, dead, as before this existed */
    pool_end = 0x14000000; cursor = 0xa474000; takes = 0;
    got = ios_pool_skip_dead_ranges( take_fake( 0x118000, 0 ), 0x118000, 0x14000000, 512, take_fake, dead_fake,
                                     &skipped, &still, &bad, &bcur, &bmax );
    assert( still == 1 && got < pool_end && dead_fake( got, 0x118000, &bad, &bcur, &bmax ) );

    /* the try cap holds */
    pool_end = 0x1cff8000; cursor = 0xa474000; takes = 0;
    got = ios_pool_skip_dead_ranges( take_fake( 0x118000, 0 ), 0x118000, 0x1cff8000, 10, take_fake, dead_fake,
                                     &skipped, &still, &bad, &bcur, &bmax );
    assert( skipped == 10 && still == 1 && takes == 11 );

    /* an exhausted allocator (-1 from the start) is passed through untouched */
    got = ios_pool_skip_dead_ranges( (size_t)-1, 0x118000, 0x1cff8000, 512, take_fake, dead_fake,
                                     &skipped, &still, &bad, &bcur, &bmax );
    assert( got == (size_t)-1 && skipped == 0 && !still );

    puts( "PASS: clamp arithmetic (Vice City numbers), dead-range skip (swath, live first range, exhausted pool, cap)" );
    return 0;
}
'''
with tempfile.TemporaryDirectory(prefix='pool-dead-range-') as directory:
    temp = Path(directory)
    unit = temp / 'fakes.c'
    unit.write_text(fakes)
    exe = temp / 'fakes'
    subprocess.run([os.environ.get('CC', 'cc'), '-std=c11', '-D_DARWIN_C_SOURCE', '-D_GNU_SOURCE', '-Wall', '-Wextra',
                    '-Werror', str(unit), '-o', str(exe)], check=True)
    r = subprocess.run([str(exe)], capture_output=True, text=True)
    sys.stdout.write(r.stdout)
    check('the clamp and the skip behave on the numbers of the Vice City logs', r.returncode == 0, r.stderr)
    for arg, want in (('unset', '0'), ('0', '0'), ('1', '1'), ('true', '0')):
        r = subprocess.run([str(exe), arg], capture_output=True, text=True)
        check('env.MADEIRA_POOL_CLAMP_SECTIONS = %s -> clamp %s' % (arg, 'on' if want == '1' else 'off'),
              r.returncode == 0 and r.stdout.strip() == want, r.stderr)

if sys.platform != 'darwin':
    print('skip host run against Mach memory: needs macOS')
    sys.exit(0 if ok else 1)

# ---- the same skip against real Mach memory with lowered max protection ------------------------------------------
mach = r'''
#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <mach/mach.h>
#include <mach/mach_vm.h>
#define MB ((size_t)1 << 20)
static void *ios_jit_rx_base_global;
''' + first_nonexec + typedefs + skip_fn + r'''
static size_t cursor;
static size_t take_real( size_t size, size_t limit ) { size_t o = cursor; (void)limit; cursor += size; return o; }
static int dead_real( size_t off, size_t size, size_t *bad_at, unsigned int *cur, unsigned int *max )
{
    return ios_pool_range_first_nonexec( off, size, size, bad_at, cur, max );
}

int main(void)
{
    mach_vm_address_t base = 0;
    const size_t size = 96 * MB, pg = vm_page_size, copy = 0x118000;
    unsigned int skipped = 99, cur = 0, max = 0;
    size_t bad = 99, got;
    int still = 99;

    assert( mach_vm_allocate( mach_task_self(), &base, size, VM_FLAGS_ANYWHERE ) == KERN_SUCCESS );
    ios_jit_rx_base_global = (void *)base;

    /* a dead run: max_prot without EXEC from +20 MB for 24 MB (what the mprotect(RW) of the overrun leaves) */
    assert( vm_protect( mach_task_self(), base + 20 * MB, 24 * MB, TRUE, VM_PROT_READ | VM_PROT_WRITE ) == KERN_SUCCESS );

    cursor = 20 * MB + 0x8000;      /* the first range handed out lies inside the run */
    got = ios_pool_skip_dead_ranges( take_real( copy, 0 ), copy, size, 512, take_real, dead_real,
                                     &skipped, &still, &bad, &cur, &max );
    assert( !still && skipped > 15 );
    assert( got >= 44 * MB && got + copy <= size );
    assert( !ios_pool_range_first_nonexec( got, copy, copy, &bad, &cur, &max ) );
    /* the dead run itself is reported as the first dead page of the first range */
    cursor = 20 * MB + 0x8000;
    got = ios_pool_skip_dead_ranges( take_real( copy, 0 ), copy, size, 512, take_real, dead_real,
                                     &skipped, &still, &bad, &cur, &max );
    assert( bad == 0 && cur == (VM_PROT_READ | VM_PROT_WRITE) );

    /* a range before the run is live and stays as it is */
    cursor = 4 * MB;
    got = ios_pool_skip_dead_ranges( take_real( copy, 0 ), copy, size, 512, take_real, dead_real,
                                     &skipped, &still, &bad, &cur, &max );
    assert( got == 4 * MB && skipped == 0 && !still );
    (void)pg;
    puts( "PASS: a dead run in real Mach memory is skipped, a live range is kept" );
    return 0;
}
'''
with tempfile.TemporaryDirectory(prefix='pool-dead-range-mach-') as directory:
    temp = Path(directory)
    unit = temp / 'mach.c'
    unit.write_text(mach)
    exe = temp / 'mach'
    subprocess.run([os.environ.get('CC', 'cc'), '-std=c11', '-Wall', '-Wextra', '-Werror', str(unit), '-o', str(exe)],
                   check=True)
    r = subprocess.run([str(exe)], capture_output=True, text=True)
    sys.stdout.write(r.stdout)
    check('the skip steps over a lost-EXEC run in real Mach memory', r.returncode == 0, r.stderr)
sys.exit(0 if ok else 1)

#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright 2026 mckenna654 (the harness ideas and the policy cases: thebcd69/bcd-games#4, submitted with a Signed-off-by under CONTRIBUTING.md)
# Copyright 2026 thebcd69 (BCD) (the changes below)
# Modified by BCD (thebcd69), 2026-10-24: ported into the tree behind the public repository (opt-in switch, the real mprotect_exec text, a reference model).
# Not BCD Material: LICENSE-BCD.md section 1 keeps the other author's header and licence on a file that mixes in their text.
"""env.MADEIRA_SHARED_SECTION_EXEC = 1: an executable view of a pagefile section keeps its shared memory (Diablo II: Resurrected).

Based on thebcd69/bcd-games#4 by mckenna654. D2R's loader maps ONE section twice, writes its code through one view and runs the other.
BCD copied the executable view into the JIT pool (the anonymous-RWX alias path of mprotect_exec, "[jit-copy] ml634 PRE-REMAP"), which
cuts it off from the writer ("0/1048576 bytes nonzero", then the jump into zeros). With the switch mprotect_exec drops PROT_EXEC for a
view of a pagefile section in an x64 process (the ml1030 contract: the host never runs x86 bytes, it only reads them) so the view stays the
shared mapping. Off (default): the same code as before.

What this runs, on the host (AddressSanitizer + UBSan; needs python3 and a C compiler):
  - the production text of ios_x64_image_view_covers, ios_shared_section_exec_enabled and ios_guest_section_is_host_data, and the WINE_IOS
    prelude of mprotect_exec (every branch above the pool path), cut out of build/ntdll-unix/virtual_ios.c, with fakes for the view table and
    the three other "is host data" predicates;
  - a reference model of that prelude, and the prelude WITHOUT this change (cut out of the same text): with the switch off the production prelude
    answers exactly like the old one for 73,728 inputs (12 view kinds, the three older predicates, EC allocation, 32-bit window, missing view,
    missing EC bitmap view, resource-only map, 8 protections, 3 request shapes); with the switch on it differs from the old one only where the view is a pagefile
    section of an x64 process and the request is executable, and there it returns the same protection without EXEC (PROT_READ when nothing is left);
  - the switch: unset, empty, 0, 2, 01, true are off; 1 is on (separate processes, the switch is read once);
  - the views the change must leave alone: images, file-backed sections, native allocations, ARM64EC code (the allocation in flight and the view after),
    system views, 32-bit guest windows, a process without the EC bitmap view, and a request that runs past the view's last host page.
It does not run FEX or iOS. tests/x64/shared-section-x64.c is the end-to-end program for a device; it is compiled here when a mingw clang is found.
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


def function(text, signature):
    start = text.index(signature)
    return text[start:text.index('\n}\n', start) + 3]


# ---------------------------------------------------------------- source shape
switch_fn = function(src, 'static int ios_shared_section_exec_enabled( void )')
policy_fn = function(src, 'static int ios_guest_section_is_host_data( const void *base, size_t size )')
covers_fn = function(src, 'static int ios_x64_image_view_covers( uintptr_t view_base, size_t view_size, uintptr_t base, size_t size )')
check('the switch is read once, by name, and is on only for the value 1',
      src.count('getenv( "MADEIRA_SHARED_SECTION_EXEC" )') == 1 and "cached = (s && s[0] == '1' && !s[1]) ? 1 : 0;" in switch_fn
      and 'static int cached = -1;' in switch_fn)
check('the policy asks the switch FIRST and then excludes native allocations, EC code, 32-bit windows, images, files and system views',
      policy_fn.index('ios_shared_section_exec_enabled()') < policy_fn.index('arm64ec_view') < policy_fn.index('ios_alloc_ec_code')
      < policy_fn.index('ios_wow_in_window( base )') < policy_fn.index('find_view( base, 0 )')
      and '(SEC_COMMIT | SEC_RESERVE)' in policy_fn and '(SEC_FILE | SEC_IMAGE | VPROT_ARM64EC | VPROT_SYSTEM)' in policy_fn
      and 'ios_x64_image_view_covers( (uintptr_t)view->base, view->size, (uintptr_t)base, size )' in policy_fn
      and policy_fn.count('return 1') == 0)
mp_head = 'static inline int mprotect_exec( void *base, size_t size, int unix_prot )\n{\n#ifdef WINE_IOS\n'
mp_start = src.index(mp_head)
prelude_start = mp_start + len(mp_head)
prelude_end = src.index('\n#endif\n\n    /* ml247:', prelude_start)
prelude = src[prelude_start:prelude_end]
hook = ('    if ((unix_prot & PROT_EXEC) && ios_guest_section_is_host_data( base, size ))\n'
        '    {\n'
        '        static unsigned int section_n;\n'
        '        if (section_n++ < 24 && !ios_in_mach_exc)\n'
        '            dprintf( 2, "[guest-section] %p+0x%lx: preserving shared backing for translated code\\n",\n'
        '                     base, (unsigned long)size );\n'
        '        unix_prot &= ~PROT_EXEC;\n'
        '        if (!unix_prot) unix_prot = PROT_READ;\n'
        '    }\n')
check('mprotect_exec drops EXEC for such a view (and falls back to PROT_READ), logs [guest-section] 24 times at most, and does so after the three older cases and the resource-only refusal (so no older answer changes)',
      prelude.count(hook) == 1
      and prelude.index('ios_x64_image_is_host_data( base, size )') < prelude.index('ios_map_resource_view') < prelude.index(hook))
check('the section hook is the only reader of the new predicate',
      src.count('ios_guest_section_is_host_data(') == 2 and src.count('ios_shared_section_exec_enabled(') == 2)
bridge = (root / 'app/Madeira/WineProcessBridge.m').read_text()
check('a later game of the same app run does not inherit the switch (WineProcessBridge.m unsets it at the session end)',
      'unsetenv("MADEIRA_SHARED_SECTION_EXEC")' in bridge)

# ---------------------------------------------------------------- compiled run: production text against fakes
defs = {}
for m in re.finditer(r'^#define (VPROT_\w+)\s+(0x[0-9a-fA-F]+)', src, re.M):
    defs.setdefault(m.group(1), m.group(2))
macros = '\n'.join('#define %s %s' % kv for kv in defs.items())
host_page = re.search(r'^#define IOS_X64_IMAGE_HOST_PAGE .*$', src, re.M)
assert host_page, 'IOS_X64_IMAGE_HOST_PAGE moved'
old_prelude = prelude.replace(hook + '\n', '') if (hook + '\n') in prelude else prelude.replace(hook, '')
# the comment block above the hook belongs to the hook, not to the old text
cmt_start = old_prelude.find('    /* madeira-bcd: see ios_guest_section_is_host_data')
if cmt_start >= 0:
    cmt_end = old_prelude.index('*/', cmt_start) + 3
    old_prelude = old_prelude[:cmt_start] + old_prelude[cmt_end:]
check('the old prelude (the same text without the hook) is shorter and still has the three older cases and the resource refusal',
      len(old_prelude) < len(prelude) and 'ios_guest_image_is_host_data' in old_prelude and 'ios_x64_image_is_host_data' in old_prelude
      and 'ios_map_resource_view' in old_prelude and 'ios_guest_section_is_host_data' not in old_prelude)

harness = r'''
#define _GNU_SOURCE
#include <assert.h>
#include <errno.h>
#include <stdint.h>
#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#define WINE_IOS 1
#define SEC_FILE    0x00800000
#define SEC_IMAGE   0x01000000
#define SEC_RESERVE 0x04000000
#define SEC_COMMIT  0x08000000
@MACROS@
struct file_view { void *base; size_t size; unsigned int protect; };
static struct file_view view = { (void *)0x10000, 0x11000, SEC_COMMIT };
static struct file_view *arm64ec_view = &view;
static int ios_alloc_ec_code, wow, found = 1, printed;
static volatile int ios_in_mach_exc;
static int ios_wow_in_window( const void *p ) { (void)p; return wow; }
static struct file_view *find_view( const void *p, size_t s ) { (void)p; (void)s; return found ? &view : NULL; }
static int fake_guest_image, fake_anon_rwx, fake_x64_image;
static int ios_guest_image_is_host_data( const void *b, size_t s ) { (void)b; (void)s; return fake_guest_image; }
static int ios_guest_anon_rwx_is_host_data( const void *b, size_t s ) { (void)b; (void)s; return fake_anon_rwx; }
static int ios_x64_image_is_host_data( const void *b, size_t s ) { (void)b; (void)s; return fake_x64_image; }
static _Thread_local int ios_jit_copy_refused, ios_map_resource_view;
static int fake_mprotect( void *b, size_t s, int p ) { (void)b; (void)s; (void)p; return 0; }
#define mprotect fake_mprotect
#define dprintf(...) (printed++)
@HOSTPAGE@
@COVERS@
@SWITCH@
@POLICY@
static int prelude( void *base, size_t size, int unix_prot )
{
@PRELUDE@
    return unix_prot;
}
static int prelude_old( void *base, size_t size, int unix_prot )
{
@OLDPRELUDE@
    return unix_prot;
}
/* the reference model of the change itself */
static int qualifies( void *base, size_t size )
{
    if (!found || !arm64ec_view || ios_alloc_ec_code || wow) return 0;
    if (!(view.protect & (SEC_COMMIT | SEC_RESERVE))) return 0;
    if (view.protect & (SEC_FILE | SEC_IMAGE | VPROT_ARM64EC | VPROT_SYSTEM)) return 0;
    uintptr_t b = (uintptr_t)base, vb = (uintptr_t)view.base, ve = vb + view.size, he = (ve + 0x3fff) & ~(uintptr_t)0x3fff;
    return b >= vb && b < ve && b + size >= b && b + size <= he;
}
static const unsigned kinds[] = { SEC_COMMIT, SEC_RESERVE, SEC_COMMIT | SEC_FILE, SEC_RESERVE | SEC_FILE, SEC_IMAGE, SEC_COMMIT | SEC_IMAGE,
                                  0, VPROT_READ | VPROT_WRITE | VPROT_EXEC, SEC_COMMIT | VPROT_ARM64EC, SEC_COMMIT | VPROT_SYSTEM,
                                  SEC_COMMIT | VPROT_X64DATA, SEC_COMMIT | VPROT_READ | VPROT_WRITE | VPROT_EXEC };
int main( int argc, char **argv )
{
    int on, changed = 0, same = 0, dropped = 0;
    assert( argc == 2 );
    on = !strcmp( argv[1], "on" );
    if (on) setenv( "MADEIRA_SHARED_SECTION_EXEC", "1", 1 ); else unsetenv( "MADEIRA_SHARED_SECTION_EXEC" );
    for (unsigned k = 0; k < sizeof(kinds) / sizeof(kinds[0]); k++)
    for (int image = 0; image < 2; image++) for (int anon = 0; anon < 2; anon++) for (int x64 = 0; x64 < 2; x64++)
    for (int ec = 0; ec < 2; ec++) for (int w = 0; w < 2; w++) for (int absent = 0; absent < 2; absent++)
    for (int ecview = 0; ecview < 2; ecview++) for (int res = 0; res < 2; res++)
    for (int prot = 0; prot < 8; prot++)
    for (int tail = 0; tail < 3; tail++)
    {
        void *base = (void *)0x10000; size_t size = tail == 0 ? 0x4000 : tail == 1 ? 0x11000 : 0x15000;  /* inside / to the end / one host page past */
        int a, b;
        view.protect = kinds[k]; fake_guest_image = image; fake_anon_rwx = anon; fake_x64_image = x64;
        ios_alloc_ec_code = ec; wow = w; found = !absent; arm64ec_view = ecview ? &view : NULL;
        ios_map_resource_view = res; ios_jit_copy_refused = 0;
        a = prelude( base, size, prot ); b = prelude_old( base, size, prot );
        if (!on)
        {
            assert( a == b );   /* off: the old text's answer, for every input */
            same++;
            continue;
        }
        if (a == b) { same++; continue; }
        /* on: it may differ only where the model says the view qualifies, EXEC was still requested after the older cases, and
         * the resource refusal did not take the request */
        if (!(b != -1 && (b & PROT_EXEC) && qualifies( base, size )))
            fprintf( stderr, "kind=%#x image=%d anon=%d x64=%d ec=%d wow=%d absent=%d ecview=%d res=%d prot=%d tail=%d a=%d b=%d\n",
                     kinds[k], image, anon, x64, ec, w, absent, ecview, res, prot, tail, a, b );
        assert( b != -1 && (b & PROT_EXEC) && qualifies( base, size ) );
        assert( a == ((b & ~PROT_EXEC) ? (b & ~PROT_EXEC) : PROT_READ) );
        dropped++; changed++;
    }
    if (on) assert( dropped > 0 && changed == dropped );
    printf( "%s: %d identical answers, %d changed (only EXEC dropped)\n", argv[1], same, changed );
    return 0;
}
'''
policy_harness = r'''
#define _GNU_SOURCE
#include <assert.h>
#include <stdint.h>
#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#define WINE_IOS 1
#define SEC_FILE    0x00800000
#define SEC_IMAGE   0x01000000
#define SEC_RESERVE 0x04000000
#define SEC_COMMIT  0x08000000
@MACROS@
struct file_view { void *base; size_t size; unsigned int protect; };
static struct file_view view = { (void *)0x10000, 0x11000, SEC_COMMIT };
static struct file_view *arm64ec_view = &view;
static int ios_alloc_ec_code, wow, found = 1;
static int ios_wow_in_window( const void *p ) { (void)p; return wow; }
static struct file_view *find_view( const void *p, size_t s ) { (void)p; (void)s; return found ? &view : NULL; }
@HOSTPAGE@
@COVERS@
@SWITCH@
@POLICY@
int main( int argc, char **argv )
{
    const char *want = argc > 1 ? argv[1] : "";
    if (!strcmp( want, "unset" )) unsetenv( "MADEIRA_SHARED_SECTION_EXEC" ); else setenv( "MADEIRA_SHARED_SECTION_EXEC", want, 1 );
    int on = !strcmp( want, "1" );
    assert( ios_shared_section_exec_enabled() == on );
    setenv( "MADEIRA_SHARED_SECTION_EXEC", on ? "0" : "1", 1 );
    assert( ios_shared_section_exec_enabled() == on );        /* read once */
    /* the helper itself: with the switch on, the shape of the views */
    assert( ios_guest_section_is_host_data( (void *)0x10000, 0x4000 ) == on );
    if (!on) { printf( "switch %s: off\n", want ); return 0; }
    assert( ios_guest_section_is_host_data( (void *)0x10000, 0x11000 ) );
    assert( ios_guest_section_is_host_data( (void *)0x10000, 0x14000 ) );     /* the last host page of the view */
    assert( !ios_guest_section_is_host_data( (void *)0x10000, 0x18000 ) );    /* past it */
    assert( !ios_guest_section_is_host_data( (void *)0xf000, 0x1000 ) );      /* before the view */
    assert( !ios_guest_section_is_host_data( (void *)0x10000, (size_t)-1 ) ); /* wraps */
    view.protect = SEC_RESERVE; assert( ios_guest_section_is_host_data( (void *)0x10000, 0x1000 ) );
    unsigned excluded[] = { SEC_FILE, SEC_IMAGE, VPROT_ARM64EC, VPROT_SYSTEM };
    for (unsigned i = 0; i < 4; i++) { view.protect = SEC_COMMIT | excluded[i]; assert( !ios_guest_section_is_host_data( (void *)0x10000, 0x1000 ) ); }
    view.protect = VPROT_READ | VPROT_WRITE | VPROT_EXEC; assert( !ios_guest_section_is_host_data( (void *)0x10000, 0x1000 ) );   /* a native allocation */
    view.protect = SEC_COMMIT;
    ios_alloc_ec_code = 1; assert( !ios_guest_section_is_host_data( (void *)0x10000, 0x1000 ) ); ios_alloc_ec_code = 0;
    wow = 1; assert( !ios_guest_section_is_host_data( (void *)0x10000, 0x1000 ) ); wow = 0;
    arm64ec_view = NULL; assert( !ios_guest_section_is_host_data( (void *)0x10000, 0x1000 ) ); arm64ec_view = &view;
    found = 0; assert( !ios_guest_section_is_host_data( (void *)0x10000, 0x1000 ) ); found = 1;
    printf( "switch %s: on, the views checked\n", want );
    return 0;
}
'''

cc = os.environ.get('CC', 'cc')
flags = ['-std=gnu11', '-O1', '-Wall', '-Wextra', '-Werror', '-Wno-unused-parameter', '-Wno-unused-variable', '-Wno-unused-function',
         '-Wno-unused-but-set-variable', '-fsanitize=address,undefined', '-fno-sanitize-recover=all', '-fno-omit-frame-pointer']
env = dict(os.environ, ASAN_OPTIONS='detect_leaks=0')


def fill(text):
    return (text.replace('@MACROS@', macros).replace('@HOSTPAGE@', host_page.group(0)).replace('@COVERS@', covers_fn)
            .replace('@SWITCH@', switch_fn).replace('@POLICY@', policy_fn).replace('@PRELUDE@', prelude).replace('@OLDPRELUDE@', old_prelude))


def build(tmp, name, text):
    path = Path(tmp) / (name + '.c')
    path.write_text(fill(text))
    exe = Path(tmp) / name
    r = subprocess.run([cc] + flags + [str(path), '-o', str(exe)], capture_output=True, text=True)
    check('the production text compiles on the host (%s)' % name, r.returncode == 0, r.stderr[-3000:])
    return exe if r.returncode == 0 else None


def run(exe, *args):
    r = subprocess.run([str(exe)] + list(args), capture_output=True, text=True, env=env)
    return r.returncode == 0, (r.stdout + r.stderr).strip()[-700:]


with tempfile.TemporaryDirectory() as tmp:
    exe = build(tmp, 'policy', policy_harness)
    if exe:
        for value in ('unset', '', '0', '2', '01', 'true', '1 ', ' 1', '1'):
            good, out = run(exe, value)
            check('switch %r: %s' % (value, 'on, the views the change takes and the ones it leaves alone' if value == '1' else 'off'), good, out)
    exe = build(tmp, 'prelude', harness)
    if exe:
        good, out = run(exe, 'off')
        print('     ' + out)
        check('switch off: the production prelude answers exactly like the old one for every input (73,728 combinations)', good, out)
        good, out = run(exe, 'on')
        print('     ' + out)
        check('switch on: it differs from the old one only for an executable request on a pagefile-section view of an x64 process, and only by EXEC', good, out)

# ---------------------------------------------------------------- the end-to-end program compiles
prog = root / 'tests/x64/shared-section-x64.c'
check('tests/x64/shared-section-x64.c is there with the contributor credit', prog.is_file() and 'mckenna654' in prog.read_text()
      and 'SPDX-License-Identifier: GPL-3.0-or-later' in prog.read_text())
mingw = shutil.which('x86_64-w64-mingw32-clang')
for cand in sorted((root / 'toolchains').glob('llvm-mingw-*/bin/x86_64-w64-mingw32-clang')) if (root / 'toolchains').is_dir() else []:
    mingw = mingw or str(cand)
if not mingw:
    for cand in sorted(Path.home().glob('madeira-bcd/toolchains/llvm-mingw-*/bin/x86_64-w64-mingw32-clang')):
        mingw = str(cand)
if mingw:
    with tempfile.TemporaryDirectory() as tmp:
        r = subprocess.run([mingw, '-O2', '-Wall', '-Wextra', '-Werror', str(prog), '-o', str(Path(tmp) / 'shared-section-x64.exe')],
                           capture_output=True, text=True)
        check('the end-to-end program builds as an x64 PE with -Wall -Wextra -Werror', r.returncode == 0, r.stderr[-1500:])
else:
    print('skip the end-to-end program build: no x86_64-w64-mingw32-clang found')

# ---------------------------------------------------------------- list, policy, catalog, CI
lst = (root / 'recommended/recommendations.json').read_text()
policy = (root / 'app/Madeira/GameRecommendationsList.swift').read_text()
catalog = (root / 'app/Madeira/ConfigCatalog.generated.swift').read_text()
gen = (root / 'build/tools/gen-config-catalog.py').read_text()
ci = (root / '.github/workflows/build-ipa.yml').read_text()
check('only the D2R list carries the line, from build 598', lst.count('"env.MADEIRA_SHARED_SECTION_EXEC = 1"') == 1
      and '{"line": "env.MADEIRA_SHARED_SECTION_EXEC = 1", "minBuild": 598}' in lst)
check('the list policy allows the name and checks 0 or 1', '"MADEIRA_SHARED_SECTION_EXEC"' in policy and 'case "MADEIRA_NTDLL_XSEC", "MADEIRA_SHARED_SECTION_EXEC"' in policy)
check('the settings catalog has it (generator and generated file)', 'env.MADEIRA_SHARED_SECTION_EXEC' in catalog and 'env.MADEIRA_SHARED_SECTION_EXEC' in gen)
check('build-ipa.yml runs this check', 'python3 tests/host/check-shared-section-exec.py' in ci)

print('PASS' if ok else 'FAIL')
raise SystemExit(0 if ok else 1)

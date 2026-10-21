#!/usr/bin/env python3
"""[av-args]: failure-path-only diagnostic for NULL-class guest faults (ios_av_args_diag.h); no Wine runs.

Log: Dying Light (Steam 239140), the owner's SAHIP4/5/6 on build 575. Both access violations are in
engine_x64_rwdi.dll; the fatal one is `call [rax+0x330]` on a heap singleton followed by `mov eax,[rax+0x4c]`
with RAX = 0. The session log prints the registers as numbers ([x86_live]) and an RCX dump, but not the name
that was looked up, the class of the singleton (the vtable's module+RVA) or which register is a module pointer.

Source checks (every host): one header, included once before the Mach exception thread; one call, inside the
UNHANDLED report and right after the ajan2 diagnostic; no write/protect/exit/stop call; the only memory reads are
mach_vm_read_overwrite; NULL-class + JIT-pc gate; 3 reports per process, a repeated host pc is not a report.

Compiled runs (macOS only, the helper reads real Mach memory): the production header is compiled with a fake
thread state over constructed memory. The Dying Light shape (RCX = heap object whose first word is a vtable inside
a module, RDI = R8 = a name string in the module, R13 = a function in the module, RDX = RSI = a stack buffer) prints
the object's vtable as module+RVA, the string with both register names folded, the function, and nothing for the
plain buffer, the small integers, -1 or RAX = 0; UTF-16 names print; an unterminated run of 8+ characters at the
end of a page prints, a short one and a run ended by another byte do not; unreadable pointers print nothing;
canonical (>= 0x10000) fault addresses, other exception kinds, host pcs outside the pool and registers with nothing
to say print nothing at all (no header); a repeated pc is silent and the 4th distinct report is capped.
"""
from pathlib import Path
import os
import re
import shutil
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[2]
signal_src = (root / 'build/ntdll-unix/signal_arm64_ios.c').read_text()
header_path = root / 'build/ntdll-unix/ios_av_args_diag.h'
header = header_path.read_text()
ok = True


def check(what, cond, detail=''):
    global ok
    print(('ok   ' if cond else 'FAIL ') + what + (('\n     ' + detail) if (detail and not cond) else ''))
    ok = ok and bool(cond)


# ---------------------------------------------------------------- source shape
inc = '#include "ios_av_args_diag.h"'
check('the header is included exactly once, before the Mach exception thread and outside the ajan2 helper block',
      signal_src.count(inc) == 1 and signal_src.index(inc) < signal_src.index('/* ajan2-tani (2026-10-21): FAILURE-PATH DIAGNOSTICS')
      < signal_src.index('static void *ios_mach_exception_thread( void *arg )'))
check('the header is included after the declarations it uses (ios_pe_module_name, ios_jit_low_contains)',
      signal_src.index('static const char *ios_pe_module_name( uint64_t base );') < signal_src.index(inc)
      and signal_src.index('extern int ios_jit_low_contains( uintptr_t a );') < signal_src.index(inc))
call = 'ios_av_args_diag( &state, req->exception, fault_addr );'
check('one call site', signal_src.count(call) == 1)
site = signal_src.index(call)
ajan2_call = 'ios_ajan2_fault_diag( &state, thread_teb, req->exception, fault_addr );'
unhandled = signal_src.rindex('"[mach_exc] UNHANDLED #%d', 0, site)
check('the call is inside the UNHANDLED report, right after the ajan2 diagnostic and before [fault-full]',
      unhandled < signal_src.index(ajan2_call) < site < signal_src.index('madeira-bcd [fault-full]: every register', site))
check('the UNHANDLED report keeps its own rate limit around the call',
      'if (cnt <= 5 || terminal_pc ||' in signal_src[unhandled - 3000:unhandled])

forbidden = ('thread_set_state', 'thread_suspend', 'thread_terminate', 'vm_protect', 'mprotect(', 'mach_vm_write',
             'vm_write(', 'memset(', 'memcpy(', 'mmap(', 'free(', 'malloc(', 'abort(', 'exit(', 'sigaction', 'fopen(', 'open(')
check('no write, protect, stop, exit or file call', not [f for f in forbidden if f in header],
      str([f for f in forbidden if f in header]))
raw_deref = re.findall(r'\*\s*\(\s*(?:const\s+)?(?:volatile\s+)?(?:u?int\d+_t|void|char|unsigned\w*|uintptr_t)\s*\*\s*\)', header)
check('no raw dereference of a guest address (reads are mach_vm_read_overwrite only, one place)',
      not raw_deref and header.count('mach_vm_read_overwrite(') == 1, str(raw_deref))
check('gated on a NULL-class access fault in JIT code',
      'exception != EXC_BAD_ACCESS || fault_addr >= 0x10000 || !ios_av_args_pc_is_jit( pc )' in header)
check('capped at 3 reports, a repeated host pc is not a report, at most 8 register lines + 1 header',
      '> 3) return;' in header and 'if (seen[k] == pc) return;' in header and 'n < 8' in header and 'lines[8]' in header)
check('printed only when at least one register has something to say', 'if (!n) return;' in header)

# ---------------------------------------------------------------- compiled runs
cc = os.environ.get('CC', 'cc')
PRELUDE = r'''
#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stddef.h>
#include <unistd.h>
#include <mach/mach.h>
#include <mach/mach_vm.h>

#define arm_thread_state64_t fake_arm_thread_state64_t
typedef struct { uint64_t __x[29]; uint64_t __fp, __lr, __sp, __pc; } fake_arm_thread_state64_t;
#undef __darwin_arm_thread_state64_get_pc
#define __darwin_arm_thread_state64_get_pc(ts) ((ts).__pc)
static uint64_t fake_mod_base, fake_mod_size, fake_pool, fake_pool_size;
unsigned long long ios_jit_module_base_for_va( unsigned long long va, unsigned long long *size_out )
{
    if (va >= fake_mod_base && va < fake_mod_base + fake_mod_size) { if (size_out) *size_out = fake_mod_size; return fake_mod_base; }
    return 0;
}
static const char *ios_pe_module_name( uint64_t base ) { return base == fake_mod_base ? "engine_x64_rwdi.dll" : "?"; }
void *ios_jit_rx_base_global;
size_t ios_jit_pool_size_global;
int ios_jit_low_contains( uintptr_t a ) { (void)a; return 0; }
'''

MAIN = r'''
static uint64_t alloc(size_t n)
{
    mach_vm_address_t a = 0;
    assert(mach_vm_allocate(mach_task_self(), &a, n, VM_FLAGS_ANYWHERE) == KERN_SUCCESS);
    memset((void *)a, 0, n);
    return a;
}
static void w64(uint64_t a, uint64_t v) { memcpy((void *)a, &v, 8); }

struct world { uint64_t pool, mod, heap, stack; fake_arm_thread_state64_t st; };
static struct world make(void)
{
    struct world w;
    memset(&w, 0, sizeof(w));
    w.pool = alloc(0x10000); w.mod = alloc(0x40000); w.heap = alloc(0x10000); w.stack = alloc(0x10000);
    fake_pool = w.pool; fake_pool_size = 0x10000; ios_jit_rx_base_global = (void *)w.pool; ios_jit_pool_size_global = 0x10000; fake_mod_base = w.mod; fake_mod_size = 0x40000;
    w.st.__pc = w.pool + 0xe70;
    return w;
}

int main(int argc, char **argv)
{
    const char *s = argc > 1 ? argv[1] : "";
    struct world w = make();
    uint64_t obj = w.heap + 0x60, vtbl = w.mod + 0x8000, name = w.mod + 0xc000, buf = w.stack + 0x380;

    if (!strcmp(s, "engine") || !strcmp(s, "dedupe") || !strcmp(s, "cap")) {
        memcpy((void *)name, "VideoSettings", 14);
        w64(obj, vtbl);                                   /* the singleton: first word = vtable in the module */
        memset((void *)buf, 0xaa, 0x40);                  /* a stack buffer: no module, no text */
        w.st.__x[8] = 0;                                  /* RAX */
        w.st.__x[0] = obj;                                /* RCX */
        w.st.__x[1] = buf; w.st.__x[25] = buf;            /* RDX = RSI */
        w.st.__x[2] = name; w.st.__x[26] = name;          /* R8 = RDI */
        w.st.__x[27] = ~0ULL;                             /* RBX = -1 */
        w.st.__x[20] = w.mod + 0x1d90;                    /* R13 = a function */
        w.st.__x[21] = 1;                                 /* R14 */
        w.st.__x[22] = 0x6007e;                           /* R15 */
        ios_av_args_diag(&w.st, EXC_BAD_ACCESS, 0x4c);
        if (!strcmp(s, "dedupe")) {
            ios_av_args_diag(&w.st, EXC_BAD_ACCESS, 0x4c);                 /* same host pc: nothing */
            w.st.__pc += 0x40;
            ios_av_args_diag(&w.st, EXC_BAD_ACCESS, 0);                    /* new pc: report #2 */
        }
        if (!strcmp(s, "cap")) {
            for (int i = 1; i < 40; i++) { w.st.__pc += 0x40; ios_av_args_diag(&w.st, EXC_BAD_ACCESS, 0x4c); }
        }
        fprintf(stdout, "mod=%llx obj=%llx buf=%llx name=%llx\n", (unsigned long long)w.mod, (unsigned long long)obj,
                (unsigned long long)buf, (unsigned long long)name);
    } else if (!strcmp(s, "text")) {
        /* R9 UTF-16 "Input"; R10 unterminated 10-char run at the end of a page; R11 short unterminated run;
         * R12 a printable run ended by a 0x01 byte; R14 pointer into a different (unreadable) place; RBX a 60-char name */
        static const unsigned char wide[] = { 'I',0,'n',0,'p',0,'u',0,'t',0,0,0 };
        uint64_t page_end = w.heap + 0x2000 - 10, short_end = w.heap + 0x4000 - 5, data = w.heap + 0x5000;
        memcpy((void *)(w.heap + 0x1000), wide, sizeof(wide));
        memcpy((void *)page_end, "ABCDEFGHIJ", 10);
        memcpy((void *)short_end, "ABCDE", 5);
        memcpy((void *)data, "abcdefgh\x01xyz", 12);
        memcpy((void *)(w.heap + 0x6000), "0123456789012345678901234567890123456789012345678901234567890123456789", 71);
        w.st.__x[3] = w.heap + 0x1000;      /* R9 */
        w.st.__x[4] = page_end;             /* R10 */
        w.st.__x[5] = short_end;            /* R11 */
        w.st.__x[19] = data;                /* R12 */
        w.st.__x[21] = 0x20000;             /* R14: low, unmapped */
        w.st.__x[27] = w.heap + 0x6000;     /* RBX: long name */
        ios_av_args_diag(&w.st, EXC_BAD_ACCESS, 0);
    } else if (!strcmp(s, "benign")) {
        memcpy((void *)name, "VideoSettings", 14);
        w64(obj, vtbl);
        w.st.__x[0] = obj; w.st.__x[2] = name;
        ios_av_args_diag(&w.st, EXC_BAD_ACCESS, 0x10000);                  /* not NULL-class */
        ios_av_args_diag(&w.st, EXC_BAD_ACCESS, 0x7fff0000ULL);
        ios_av_args_diag(&w.st, EXC_BAD_INSTRUCTION, 0);                   /* another exception kind */
        ios_av_args_diag(&w.st, 3 /* EXC_ARITHMETIC */, 0);
        w.st.__pc = 0x1000;                                                /* host pc outside the pool */
        ios_av_args_diag(&w.st, EXC_BAD_ACCESS, 0);
        w.st.__pc = w.pool + 0xe70;
        memset(w.st.__x, 0, sizeof(w.st.__x));                             /* nothing to say */
        w.st.__x[1] = 0x7e; w.st.__x[2] = 6; w.st.__x[19] = 0x2005c; w.st.__x[22] = 0x6007e; w.st.__x[8] = 1;
        w.st.__x[0] = 0x20000; w.st.__x[26] = 0x30000;                     /* unreadable pointers */
        w.st.__x[23] = w.stack + 0x100;                                    /* a stack pointer with plain zeros */
        ios_av_args_diag(&w.st, EXC_BAD_ACCESS, 0x4c);
        fputs("benign-done\n", stdout);
    }
    return 0;
}
'''


def kv(text):
    out = {}
    for token in text.split():
        if '=' in token:
            k, _, v = token.partition('=')
            out[k] = v
    return out


if sys.platform != 'darwin' or not shutil.which(cc):
    print('skip the compiled runs: macOS and a C compiler are needed (the helper reads real Mach memory)')
else:
    with tempfile.TemporaryDirectory(prefix='avargs-') as d:
        d = Path(d)
        (d / 'h.c').write_text(PRELUDE + header + MAIN)
        subprocess.run([cc, '-std=gnu11', '-Wall', '-Wextra', '-Werror', '-Wno-unused-parameter', '-Wno-unused-function',
                        str(d / 'h.c'), '-o', str(d / 'h')], check=True)

        def run(scenario):
            return subprocess.run([str(d / 'h'), scenario], capture_output=True, text=True)

        r = run('engine')
        info = kv(r.stdout)
        lines = [l for l in r.stderr.splitlines() if l.startswith('[av-args]')]
        obj, name, buf = info['obj'], info['name'], info['buf']
        check('Dying Light shape: a header and 3 register lines', len(lines) == 4, r.stderr)
        check('header names the report number, fault address and host pc',
              bool(lines) and lines[0].startswith('[av-args] rev=ajan3-dyinglight #1 NULL-class fault addr=0x4c at host pc=0x'),
              lines[0] if lines else '')
        rcx = [l for l in lines if 'RCX=' in l]
        check('RCX: the object, its first word as module+RVA (the vtable)',
              len(rcx) == 1 and f'RCX=0x{obj} [RCX]=engine_x64_rwdi.dll+0x8000' in rcx[0] and '"' not in rcx[0], str(rcx))
        r8 = [l for l in lines if 'RDI=' in l]
        check('RDI=R8 folded into one line: module+RVA and the name text',
              len(r8) == 1 and f'RDI=R8=0x{name} = engine_x64_rwdi.dll+0xc000 "VideoSettings"' in r8[0], str(r8))
        r13 = [l for l in lines if 'R13=' in l]
        check('R13: a function inside the module', len(r13) == 1 and 'R13=0x' in r13[0] and '= engine_x64_rwdi.dll+0x1d90' in r13[0], str(r13))
        check('nothing for the plain buffer (RDX=RSI), RAX=0, RBX=-1, R14=1, R15',
              not any(x in l for l in lines for x in ('RDX', 'RSI', 'RAX', 'RBX', 'R14', 'R15', buf)), r.stderr)

        r = run('dedupe')
        n = len([l for l in r.stderr.splitlines() if l.startswith('[av-args] rev=')])
        check('the same host pc twice is one report, a new pc is the second', n == 2, r.stderr)
        r = run('cap')
        reports = [l for l in r.stderr.splitlines() if l.startswith('[av-args] rev=')]
        check('40 distinct pcs: 3 reports, then silence; at most 12 lines in all',
              len(reports) == 3 and len([l for l in r.stderr.splitlines() if l.startswith('[av-args]')]) <= 12, r.stderr)

        r = run('text')
        t = r.stderr
        check('UTF-16 name is printed', 'R9=0x' in t and '"Input"' in t, t)
        check('an unterminated 10-character run at the end of a page is printed', 'R10=0x' in t and '"ABCDEFGHIJ"' in t, t)
        check('a 5-character unterminated run at the end of a page is not text', 'R11=' not in t, t)
        check('a printable run ended by a 0x01 byte is data, not text', 'R12=' not in t, t)
        check('an unreadable pointer prints nothing', 'R14=' not in t, t)
        check('a long name is cut at 48 characters with an ellipsis',
              'RBX=0x' in t and '"012345678901234567890123456789012345678901234567..."' in t, t)

        r = run('benign')
        check('canonical fault addresses, other exception kinds, a pc outside the pool and registers with nothing to say print nothing',
              r.stderr == '' and r.stdout.strip() == 'benign-done', r.stderr + r.stdout)

# ---------------------------------------------------------------- the real file still compiles for iOS (when the wine headers exist)
wine = Path(os.environ.get('WINE_SRC', root / 'wine'))
config = wine / 'build-macos/include/config.h'
if shutil.which('xcrun') and config.exists():
    try:
        sdk = subprocess.run(['xcrun', '--sdk', 'iphoneos', '--show-sdk-path'], check=True, capture_output=True, text=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        sdk = ''
    if sdk:
        build = root / 'build/ntdll-unix'
        flags = ['-fsyntax-only', '-arch', 'arm64', '-isysroot', sdk, '-miphoneos-version-min=17.0', '-O2', '-fPIC',
                 '-fno-strict-aliasing', '-Wno-implicit-function-declaration', '-Wno-int-conversion',
                 '-include', str(config), '-include', str(build / 'shims/wine_ios_exit.h'), f'-I{build / "shims"}',
                 f'-I{wine / "build-macos/dlls/ntdll"}', f'-I{wine / "dlls/ntdll"}', f'-I{wine / "dlls/ntdll/unix"}',
                 f'-I{wine / "build-macos/include"}', f'-I{wine / "include"}', '-D__WINESRC__', '-DLTC_NO_PROTOTYPES',
                 '-DLTC_SOURCE', '-D_NTSYSTEM_', '-D_ACRTIMP=', '-DWINBASEAPI=', '-DBINDIR="/usr/local/bin"',
                 '-DLIBDIR="/usr/local/lib"', '-DDATADIR="/usr/local/share"', '-DSYSTEMDLLPATH=""', '-DWINE_UNIX_LIB',
                 '-DWINE_IOS=1', '-Dget_thread_context=ntdll_get_thread_context', '-Dset_thread_context=ntdll_set_thread_context']
        r = subprocess.run(['xcrun', '-sdk', 'iphoneos', 'clang', *flags, str(build / 'signal_arm64_ios.c')], capture_output=True, text=True)
        check('signal_arm64_ios.c compiles for iOS (syntax only, pinned wine headers)', r.returncode == 0, r.stderr[-2000:])
        check('...and the new header adds no warning', 'ios_av_args' not in r.stderr, r.stderr[-2000:])
    else:
        print('skip the iOS syntax check: no iphoneos SDK')
else:
    print('skip the iOS syntax check: no wine/build-macos/include/config.h (set WINE_SRC to a configured wine tree)')

sys.exit(0 if ok else 1)

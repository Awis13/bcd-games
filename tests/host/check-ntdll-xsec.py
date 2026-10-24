#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright 2026 mckenna654 (the cases and fixtures: thebcd69/bcd-games#4 and thebcd69/bcd-wine#1, submitted with a Signed-off-by under CONTRIBUTING.md)
# Copyright 2026 thebcd69 (BCD) (the changes below)
# Modified by BCD (thebcd69), 2026-10-24: ported into the tree behind the public repository (the patch script, executable-only notification, the shipped FEX module).
# Not BCD Material: LICENSE-BCD.md section 1 keeps the other author's header and licence on a file that mixes in their text.
"""ntdll-xsec.dll: Wine's PE ntdll with executable section views and two code-probe recoveries (opt-in, Diablo II: Resurrected).

Based on thebcd69/bcd-games#4 and thebcd69/bcd-wine#1 by mckenna654. tools/patch-wine-ntdll-xsec.py writes six anchored edits into
wine/dlls/ntdll/signal_arm64ec.c; tools/build-wine-extra-dlls.sh builds the result as ntdll-xsec.dll NEXT TO the untouched ntdll.dll, after the
same byte-for-byte proof as ntdll-sync.dll; build/ntdll-unix/loader_ios.c maps it only with env.MADEIRA_NTDLL_XSEC = 1.

Checks:
  1. the patch: six edits, every anchor once in the pristine file, a second run changes nothing, undoing the edits gives the original back, a moved
     anchor fails loudly, and the patched file compiles for ARM64EC (when the llvm-mingw clang is there; it needs only Wine's source headers) with the
     probe labels on `ldr x16,[x11]` and on the `.Lexit` triple;
  2. behaviour, compiled on this host (AddressSanitizer + UBSan) from the patched text with fakes for the Win32 and emulator calls:
       - notify_map_view_of_section: a non-image section's EXECUTABLE committed regions are told to the emulator (before and after, protections as they
         are, reserved holes and data regions left out, clipped to the view), with no image callback and no loader name; an image section takes the
         old path (loader-name gate, image callback, failure unmaps) exactly as before; a failing query keeps the old path;
       - recover_arm64x_code_probe: a read access violation at any aligned pc of the labelled probe, outside the emulator, finishes .Lexit's job
         (x9 = x11, x11 = x10, pc = lr) and nothing else; every other pc, code, access type, link register or state is declined;
       - recover_emulator_code_probe: the same for the emulator's three target loads, only with MADEIRA_D2R_PROBE_RECOVERY = 1 exactly, only when
         the first twelve instructions at the ExitToX64 export are the ones it was written for; the words are compared with the shipped
         xtajit64.dll;
       - the exception entry: recovery calls the native NtContinue with the recovered context, a failed continuation leaves the original context
         intact, other exceptions take neither path;
  3. --verify on a real PE: the shipped ntdll.dll plus the marker and log strings passes (ARM64EC image, probe sequences once and 0xa0 bytes apart,
     RtlPcToFileHeader's trampoline room, the stock export set); the shipped ntdll.dll alone, a damaged probe sequence and a missing marker fail;
  4. the wiring: loader precedence, farm links, build step, CI, policy, catalog, list, auto-tune rule, session-end unset.
Needs the wine submodule checked out (WINE_SRC overrides its place) and `cc` on PATH.
"""
from pathlib import Path
import importlib.util
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[2]
wine = Path(os.environ.get('WINE_SRC') or (root / 'wine'))
sig = wine / 'dlls/ntdll/signal_arm64ec.c'
if not sig.is_file():
    print('FAIL: %s missing (check out the wine submodule)' % sig)
    sys.exit(1)
ok = True


def check(what, cond, detail=''):
    global ok
    print(('ok   ' if cond else 'FAIL ') + what + (('\n     ' + detail) if (detail and not cond) else ''))
    ok = ok and bool(cond)


script = root / 'tools/patch-wine-ntdll-xsec.py'
spec = importlib.util.spec_from_file_location('ntdllxsec', script)
xsec = importlib.util.module_from_spec(spec)
spec.loader.exec_module(xsec)
sync_spec = importlib.util.spec_from_file_location('ntdllsync', root / 'tools/patch-wine-ntdll-sync.py')
sync = importlib.util.module_from_spec(sync_spec)
sync_spec.loader.exec_module(sync)

# ---- 1. the patch ------------------------------------------------------------------
orig = sig.read_text()
check('the pristine file does not carry the change', xsec.MARK not in orig and 'recover_arm64x_code_probe' not in orig)
check('six edits, each anchor exactly once in the pristine file', len(xsec.EDITS) == 6 and all(orig.count(old) == 1 for _, old, _ in xsec.EDITS))
patched = xsec.apply_text(orig)
check('applying them changes the file and carries the mark', patched != orig and xsec.MARK in patched)
check('a second run changes nothing', xsec.apply_text(patched) == patched)
undone = patched
for _, old, new in reversed(xsec.EDITS):
    undone = undone.replace(new, old)
check('undoing the edits gives the original back', undone == orig)
fails = 0
for name, old, _ in xsec.EDITS:
    try:
        xsec.apply_text(orig.replace(old, old + '/* moved */', 1) if old.endswith('\n') is False else orig.replace(old, old.replace('\n', ' \n', 1), 1))
    except SystemExit as e:
        fails += 1 if name in str(e) else 0
check('a moved anchor stops the build, naming it', fails == len(xsec.EDITS))
with tempfile.TemporaryDirectory() as tmp:
    d = Path(tmp) / 'wine/dlls/ntdll'
    d.mkdir(parents=True)
    (d / 'signal_arm64ec.c').write_text(orig)
    r = subprocess.run([sys.executable, '-I', str(script), str(Path(tmp) / 'wine')], capture_output=True, text=True)
    check('the script patches a tree on disk', r.returncode == 0 and (d / 'signal_arm64ec.c').read_text() == patched, r.stdout + r.stderr)
    r = subprocess.run([sys.executable, '-I', str(script), str(Path(tmp) / 'wine')], capture_output=True, text=True)
    check('...and says so when it is patched already', r.returncode == 0 and 'already carries' in r.stdout, r.stdout + r.stderr)

# optional: the real compiler
mingw = os.environ.get('MINGW')
cands = [Path(mingw)] if mingw else []
cands += sorted((root / 'toolchains').glob('llvm-mingw-*/bin')) if (root / 'toolchains').is_dir() else []
cands += sorted(Path.home().glob('madeira-bcd/toolchains/llvm-mingw-*/bin'))
clang = next((c / 'arm64ec-w64-mingw32-clang' for c in cands if (c / 'arm64ec-w64-mingw32-clang').is_file()), None)
if clang:
    with tempfile.TemporaryDirectory() as tmp:
        c = Path(tmp) / 'signal_arm64ec.c'
        c.write_text(patched)
        o = Path(tmp) / 'signal_arm64ec.o'
        cmd = [str(clang), '-D__STDC__', '-c', '-o', str(o), str(c), '-I' + str(wine / 'dlls/ntdll'),
               '-I' + str(wine / 'include'), '-I' + str(wine / 'include/msvcrt'),
               '-I' + str(wine / 'libs/tomcrypt/src/headers'), '-D_MSVCR_VER=0', '-D__WINESRC__', '-DLTC_NO_PROTOTYPES', '-DLTC_SOURCE',
               '-D_NTSYSTEM_', '-D_ACRTIMP=', '-DWINBASEAPI=', '-D__WINE_PE_BUILD', '-Wall', '-target', 'arm64ec-windows', '-fuse-ld=lld',
               '--no-default-config', '-fno-strict-aliasing', '-Wno-microsoft-enum-forward-reference', '-ffunction-sections',
               '-fasync-exceptions', '-ffp-exception-behavior=maytrap', '-O2']
        env = dict(os.environ, PATH=str(clang.parent) + os.pathsep + os.environ['PATH'])
        r = subprocess.run(cmd, capture_output=True, text=True, env=env)
        check('the patched file compiles for ARM64EC with the flags of the Wine build', r.returncode == 0, r.stderr[-2500:])
        if r.returncode == 0:
            nm = subprocess.run([str(clang.parent / 'llvm-nm'), str(o)], capture_output=True, text=True).stdout
            dis = subprocess.run([str(clang.parent / 'llvm-objdump'), '-d', '--triple=aarch64', str(o)], capture_output=True, text=True).stdout
            m_begin = re.search(r'<arm64x_check_call_probe_begin>:\n\s*[0-9a-f]+:\s+([0-9a-f]{8})', dis)
            m_end = re.search(r'<arm64x_check_call_probe_end>:\n\s*[0-9a-f]+:\s+([0-9a-f]{8})\s.*\n\s*[0-9a-f]+:\s+([0-9a-f]{8})\s.*\n\s*[0-9a-f]+:\s+([0-9a-f]{8})', dis)
            check('compiler-derived probe labels: begin is `ldr x16,[x11]`, end is mov x9,x11 / mov x11,x10 / ret',
                  'arm64x_check_call_probe_begin' in nm and 'arm64x_check_call_probe_end' in nm and m_begin and m_end
                  and m_begin.group(1) == 'f9400170' and m_end.groups() == ('aa0b03e9', 'aa0a03eb', 'd65f03c0'), nm + dis[:600])
else:
    print('skip the ARM64EC compile: no arm64ec-w64-mingw32-clang (CI builds the DLL; --verify checks it)')

# ---- 2. behaviour ------------------------------------------------------------------
def function(text, signature):
    start = text.index(signature)
    return text[start:text.index('\n}', start) + 2] + '\n'


notify_fn = function(patched, 'static void notify_mapped_section_protection(')
map_fn = function(patched, 'static void notify_map_view_of_section(')
probe_fn = function(patched, 'static BOOL recover_arm64x_code_probe(')
gate_fn = function(patched, 'static BOOL emulator_probe_recovery_enabled(')
emu_fn = function(patched, 'static BOOL recover_emulator_code_probe(')
entry_start = patched.index('static void * __attribute__((used)) prepare_exception_arm64ec(')
entry_head_end = patched.index('{\n', entry_start) + 2
entry_end = patched.index('    if (rec->ExceptionCode == STATUS_EMULATION_SYSCALL) dispatch_syscall( arm_ctx );', entry_head_end)
av_prelude = patched[entry_head_end:entry_end]
check('the exception entry tries the two recoveries first, each on a copy of the native context, only for access violations',
      av_prelude.count('syscall_NtContinue( &probe_ctx, FALSE )') == 2 and av_prelude.startswith('    if (rec->ExceptionCode == STATUS_ACCESS_VIOLATION)')
      and av_prelude.count('ARM64_NT_CONTEXT probe_ctx = *arm_ctx;') == 1 and av_prelude.count('probe_ctx = *arm_ctx;') == 2)
prefix_words = [int(x, 16) for x in re.findall(r'0x[0-9a-f]{8}', emu_fn[emu_fn.index('static const ULONG prefix[]'):emu_fn.index('};')])]
check('the emulator prefix table has twelve instructions', len(prefix_words) == 12, str(prefix_words))

# the shipped emulator module: its ExitToX64 starts with exactly that prefix
xt = root / 'app/Madeira/arm64ec-windows/xtajit64.dll'
shipped_words = None
if xt.is_file():
    img = sync.Pe(str(xt))
    exps = img.exports()
    check('xtajit64.dll exports ExitToX64, DispatchJump and RetToEntryThunk', all(n in exps for n in ('ExitToX64', 'DispatchJump', 'RetToEntryThunk')))
    shipped_words = list(struct.unpack_from('<12I', img.d, img.off(exps['ExitToX64'])))
    masks = [0xff00001f if i in (6, 10) else 0xffffffff for i in range(12)]
    check('the first twelve instructions of the shipped ExitToX64 are the prefix recover_emulator_code_probe was written for (b.ne masked)',
          all((w & m) == p for w, m, p in zip(shipped_words, masks, prefix_words)), ' '.join('%08x' % w for w in shipped_words))
    disp6, disp10 = 6 + ((shipped_words[6] >> 5) & 0x7ffff), 10 + ((shipped_words[10] >> 5) & 0x7ffff)
    check('both mismatch branches go forward to the same ordinary entry, past the prefix', disp6 == disp10 and disp6 >= 12 and not (shipped_words[6] & 0x800000)
          and not (shipped_words[10] & 0x800000), 'targets %d %d' % (disp6, disp10))
    # DispatchJump pushes the link register and checks the EC bitmap; the ordinary entry pushes it and enters the emulator
    dj = struct.unpack_from('<2I', img.d, img.off(exps['DispatchJump']))
    ent = struct.unpack_from('<I', img.d, img.off(exps['ExitToX64']) + 4 * disp6 + 8 * 4)[0]
    check('DispatchJump starts with `str x30,[sp,#-8]!` and the ordinary entry of ExitToX64 ends in the same push before the emulator entry',
          dj[0] == 0xf81f8ffe and ent == 0xf81f8ffe, '%08x %08x' % (dj[0], ent))
else:
    print('skip the shipped-module comparison: app/Madeira/arm64ec-windows/xtajit64.dll is not in this tree')

harness_common = r'''
#define _GNU_SOURCE
#include <assert.h>
#include <setjmp.h>
#include <stdint.h>
#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <wchar.h>
typedef int BOOL;
typedef long LONG;
typedef int NTSTATUS;
typedef uint32_t ULONG;
typedef uintptr_t ULONG_PTR;
typedef size_t SIZE_T;
typedef uint16_t WCHAR;
typedef int HANDLE;
#define TRUE 1
#define FALSE 0
#define STATUS_SUCCESS 0
#define STATUS_ACCESS_VIOLATION 0xc0000005u
#define NT_SUCCESS(s) ((s) >= 0)
#define ARRAY_SIZE(a) (sizeof(a)/sizeof((a)[0]))
#define min(a,b) ((a)<(b)?(a):(b))
static int err_lines;
#define ERR(...) (err_lines++)
#define TRACE(...) ((void)0)
static LONG InterlockedIncrement(LONG *p) { return ++*p; }
typedef struct { WCHAR *Buffer; unsigned short Length, MaximumLength; } UNICODE_STRING;
typedef struct { uint32_t ExceptionCode, NumberParameters; uintptr_t ExceptionInformation[2]; } EXCEPTION_RECORD;
typedef struct { uint64_t Pc, Lr, X9, X10, X11, preserved[48]; } ARM64_NT_CONTEXT;
'''

notify_harness = harness_common + r'''
#define PAGE_EXECUTE 0x10
#define PAGE_EXECUTE_READ 0x20
#define PAGE_EXECUTE_READWRITE 0x40
#define PAGE_EXECUTE_WRITECOPY 0x80
#define PAGE_READWRITE 0x04
#define PAGE_READONLY 0x02
#define PAGE_NOACCESS 0x01
#define MEM_COMMIT 0x1000
#define MEM_RESERVE 0x2000
#define SEC_IMAGE 0x1000000
#define MemoryBasicInformation 0
#define SectionBasicInformation 0
#define SectionImageInformation 1
typedef struct { char *BaseAddress; SIZE_T RegionSize; ULONG State, Protect; } MEMORY_BASIC_INFORMATION;
typedef struct { ULONG Attributes; } SECTION_BASIC_INFORMATION;
typedef struct { int unused; } SECTION_IMAGE_INFORMATION;
static struct { struct { void *ArbitraryUserPointer; } Tib; } teb;
#define NtCurrentTeb() (&teb)
#define GetCurrentProcess() 1
static char memory[0x10000];
static unsigned queries, calls, image_calls, unmapped, big_images;
static int query_fail, basic_fail, image_fail, image_result;
static ULONG attributes;
static MEMORY_BASIC_INFORMATION regions[8];
static struct { void *addr; SIZE_T size; ULONG prot; int after; NTSTATUS status; } events[16];
static int NtQueryVirtualMemory(int process, void *addr, int kind, MEMORY_BASIC_INFORMATION *out, SIZE_T size, void *ret)
{
    (void)process; (void)addr; (void)kind; (void)size; (void)ret;
    if (query_fail || queries >= 8) return -1;
    *out = regions[queries++]; return 0;
}
static int NtQuerySection(HANDLE h, int kind, void *out, SIZE_T size, void *ret)
{
    (void)h; (void)size; (void)ret;
    if (kind == SectionBasicInformation) { ((SECTION_BASIC_INFORMATION *)out)->Attributes = attributes; return basic_fail; }
    return image_fail;
}
static void record(void *addr, SIZE_T size, ULONG prot, int after, NTSTATUS status)
{
    assert(calls < 16);
    events[calls].addr = addr; events[calls].size = size; events[calls].prot = prot; events[calls].after = after; events[calls++].status = status;
}
static void (*pNotifyMemoryProtect)(void *,SIZE_T,ULONG,int,NTSTATUS) = record;
static NTSTATUS image(void *a,void *b,void *c,SIZE_T s,ULONG al,ULONG p)
{ (void)a; (void)b; (void)c; (void)s; (void)al; (void)p; image_calls++; return image_result; }
static NTSTATUS (*pNotifyMapViewOfSection)(void *,void *,void *,SIZE_T,ULONG,ULONG) = image;
static void ios_note_big_image(void *p, SIZE_T s) { (void)p; (void)s; big_images++; }
static int NtUnmapViewOfSection(int p, void *a) { (void)p; (void)a; unmapped++; return 0; }
@NOTIFY@
@MAP@
static void reset(void) { calls = queries = image_calls = unmapped = big_images = 0; query_fail = basic_fail = image_fail = image_result = 0; err_lines = 0; }
int main(void)
{
    NTSTATUS status = 0;
    /* a non-image section: executable regions only */
    regions[0] = (MEMORY_BASIC_INFORMATION){memory, 0x1000, MEM_COMMIT, PAGE_EXECUTE_READ};
    regions[1] = (MEMORY_BASIC_INFORMATION){memory+0x1000, 0x1000, MEM_RESERVE, 0};                  /* a reserved hole */
    regions[2] = (MEMORY_BASIC_INFORMATION){memory+0x2000, 0x2000, MEM_COMMIT, PAGE_READWRITE};      /* data */
    regions[3] = (MEMORY_BASIC_INFORMATION){memory+0x4000, 0x4000, MEM_COMMIT, PAGE_EXECUTE_READWRITE};
    regions[4] = (MEMORY_BASIC_INFORMATION){memory+0x8000, 0x10000, MEM_COMMIT, PAGE_EXECUTE_WRITECOPY};  /* longer than the view: clipped */
    pNotifyMapViewOfSection = NULL;                                                                  /* no image callback at all */
    teb.Tib.ArbitraryUserPointer = NULL;                                                             /* and no loader name */
    notify_map_view_of_section(1, memory, 0xc000, 0, 0x20, &status);
    assert(status == 0 && image_calls == 0 && queries == 5);
    assert(calls == 6);                                                                              /* three executable regions, before and after */
    assert(events[0].addr == memory && events[0].size == 0x1000 && events[0].prot == PAGE_EXECUTE_READ && !events[0].after && events[0].status == 0);
    assert(events[1].addr == memory && events[1].size == 0x1000 && events[1].prot == PAGE_EXECUTE_READ && events[1].after && events[1].status == STATUS_SUCCESS);
    assert(events[2].addr == memory+0x4000 && events[2].size == 0x4000 && events[2].prot == PAGE_EXECUTE_READWRITE && !events[2].after);
    assert(events[3].after && events[3].addr == memory+0x4000);
    assert(events[4].addr == memory+0x8000 && events[4].size == 0x4000 && events[4].prot == PAGE_EXECUTE_WRITECOPY);   /* clipped to the view */
    assert(events[5].after && err_lines == 3);
    puts("PASS: executable regions of a non-image section are told to the emulator before and after, clipped; holes and data are not");
    reset(); regions[0] = (MEMORY_BASIC_INFORMATION){memory, 0x1000, MEM_COMMIT, PAGE_READWRITE};
    notify_map_view_of_section(1, memory, 0x1000, 0, 4, &status);
    assert(!calls && queries == 1 && !image_calls);
    reset(); query_fail = 1; notify_mapped_section_protection(memory, 0x4000); assert(!calls);
    reset(); regions[0] = (MEMORY_BASIC_INFORMATION){memory, 0, MEM_COMMIT, PAGE_EXECUTE_READ}; notify_mapped_section_protection(memory, 0x4000); assert(!calls);
    reset(); regions[0] = (MEMORY_BASIC_INFORMATION){memory+0x10, 0x1000, MEM_COMMIT, PAGE_EXECUTE_READ};      /* starts after the cursor: stop */
    regions[0].BaseAddress = memory + 0x20; notify_mapped_section_protection(memory, 0x4000); assert(!calls);
    void (*saved)(void *,SIZE_T,ULONG,int,NTSTATUS) = pNotifyMemoryProtect; pNotifyMemoryProtect = NULL;
    reset(); notify_mapped_section_protection(memory, 0x4000); assert(!queries && !calls); pNotifyMemoryProtect = saved;
    puts("PASS: a data-only view, query failures, an empty or misplaced region and a missing emulator callback tell the emulator nothing");
    /* an image section: the old path, unchanged */
    reset(); pNotifyMapViewOfSection = image; attributes = SEC_IMAGE; teb.Tib.ArbitraryUserPointer = NULL;
    notify_map_view_of_section(1, memory, 0x1000, 0, 0x20, &status);
    assert(!image_calls && !calls);                                                                  /* the loader-name gate is retained */
    teb.Tib.ArbitraryUserPointer = memory;
    notify_map_view_of_section(1, memory, 0x1000, 0, 0x20, &status);
    assert(image_calls == 1 && !calls && big_images == 1 && status == 0);
    image_result = -1;
    notify_map_view_of_section(1, memory, 0x1000, 0, 0x20, &status);
    assert(status == -1 && unmapped == 1);                                                           /* a failing image callback unmaps and reports */
    puts("PASS: an image section takes the old path: loader-name gate, image callback, unmap on failure");
    /* the section cannot be queried: the old path runs, nothing is told as a section */
    reset(); status = 0; basic_fail = 1; teb.Tib.ArbitraryUserPointer = memory; attributes = 0;
    notify_map_view_of_section(1, memory, 0x1000, 0, 0x20, &status);
    assert(!calls && image_calls == 1);
    reset(); basic_fail = 1; teb.Tib.ArbitraryUserPointer = NULL; notify_map_view_of_section(1, memory, 0x1000, 0, 0x20, &status);
    assert(!calls && !image_calls);
    puts("PASS: a section that cannot be queried keeps the old path");
    return 0;
}
'''

recover_harness = harness_common + r'''
static void *p_ios_jit_reverse_translate_addr = (void *)1, *pBeginSimulation = (void *)1;
static struct { int InSimulation; } area;
static int have_area = 1, is_ec;
static void *get_arm64ec_cpu_area_raw(void) { return have_area ? &area : NULL; }
#define get_arm64ec_cpu_area() ((typeof(&area))get_arm64ec_cpu_area_raw())
static int RtlIsEcCode(ULONG_PTR p) { (void)p; return is_ec; }
#define ALIAS (UINT64_C(1) << 63)
static void *xlate_ios_jit_rev(void *p)
{
    uintptr_t a = (uintptr_t)p & ~ALIAS;                       /* the top-bit alias of the emulator cases, the pool alias +0x10000000 of the probe cases */
    if (a >= 0x10000000 && a < 0x10060000) a -= 0x10000000;
    return (void *)a;
}
static void *xlate_ios_jit(void *p) { return (void *)((uintptr_t)p | ALIAS); }
static const char *setting = "1";
static void RtlInitUnicodeString(UNICODE_STRING *s, const wchar_t *name) { (void)s; assert(!wcscmp(name, L"MADEIRA_D2R_PROBE_RECOVERY")); }
static int RtlQueryEnvironmentVariable_U(void *env, UNICODE_STRING *name, UNICODE_STRING *value)
{
    (void)env; (void)name;
    if (!setting || strlen(setting) * sizeof(WCHAR) > value->MaximumLength) return -1;
    value->Length = strlen(setting) * sizeof(WCHAR);
    for (unsigned i = 0; i < strlen(setting); i++) value->Buffer[i] = setting[i];
    return 0;
}
/* the probe labels: addresses inside a pretend ntdll (PE VA 0x5635c..0x563fc) with a pool alias at +0x10000000 */
#define arm64x_check_call_probe_begin ((char *)0x5635c)
#define arm64x_check_call_probe_end ((char *)0x563fc)
static ULONG dispatcher[] = { @WORDS@ };
static void *ios_exit_to_x64 = dispatcher, *ios_dispatch_jump = (void *)0x8000;
static ULONG_PTR native_continue_calls, last_continue_pc;
static ARM64_NT_CONTEXT last_continue;
static int continue_fails;
static jmp_buf out;
static NTSTATUS syscall_NtContinue(ARM64_NT_CONTEXT *ctx, int alertable)
{
    (void)alertable;
    native_continue_calls++; last_continue = *ctx; last_continue_pc = ctx->Pc;
    if (continue_fails) return -1;
    longjmp(out, 1);                       /* a successful NtContinue does not return */
}
@PROBE@
@GATE@
@EMU@
static int entry(EXCEPTION_RECORD *rec, ARM64_NT_CONTEXT *arm_ctx)
{
@AVPRELUDE@
    return 0;
}
static EXCEPTION_RECORD av = { STATUS_ACCESS_VIOLATION, 2, {0, 0x12688f3d0} };
static ARM64_NT_CONTEXT initial_probe(void)
{
    ARM64_NT_CONTEXT c;
    memset(&c, 0xa5, sizeof(c));
    c.Pc = 0x1005635c; c.Lr = 0x15483231c; c.X10 = 0x154833000; c.X11 = av.ExceptionInformation[1]; c.X9 = 0x77;
    return c;
}
static ARM64_NT_CONTEXT initial_emu(unsigned index)
{
    ARM64_NT_CONTEXT c;
    memset(&c, 0xa5, sizeof(c));
    c.Pc = (uintptr_t)&dispatcher[index]; c.Lr = 0x4000; c.X9 = 0x10000;
    return c;
}
static void rejected_probe(EXCEPTION_RECORD rec, ARM64_NT_CONTEXT c)
{ ARM64_NT_CONTEXT before = c; assert(!recover_arm64x_code_probe(&rec, &c)); assert(!memcmp(&before, &c, sizeof(c))); }
static void rejected_emu(EXCEPTION_RECORD rec, ARM64_NT_CONTEXT c)
{ ARM64_NT_CONTEXT before = c; assert(!recover_emulator_code_probe(&rec, &c)); assert(!memcmp(&before, &c, sizeof(c))); }
int main(void)
{
    ARM64_NT_CONTEXT c = initial_probe(), expected = c;
    expected.Pc = c.Lr; expected.X9 = c.X11; expected.X11 = c.X10;
    assert(recover_arm64x_code_probe(&av, &c) && !memcmp(&c, &expected, sizeof(c)));
    for (uintptr_t pc = 0x5635c; pc < 0x563fc; pc += 4)          /* every aligned pc of the labelled probe, by its PE or its pool address */
    {
        c = initial_probe(); c.Pc = pc | (pc & 8 ? 0x10000000 : 0);
        assert(recover_arm64x_code_probe(&av, &c) && c.Pc == expected.Pc && c.X9 == expected.X9 && c.X11 == expected.X11);
    }
    puts("PASS: a read fault in the labelled probe finishes .Lexit's job and changes nothing else");
    uintptr_t unrelated[] = {0, 0x56358, 0x563fc, 0x56400, 0x71f3780000, 0x5635e};
    for (unsigned i = 0; i < sizeof(unrelated)/sizeof(*unrelated); i++) { c = initial_probe(); c.Pc = unrelated[i]; rejected_probe(av, c); }
    EXCEPTION_RECORD rec = av;
    rec.ExceptionCode = 0x80000001; rejected_probe(rec, initial_probe());
    rec = av; rec.ExceptionCode = 0xc000001d; rejected_probe(rec, initial_probe());
    rec = av; rec.NumberParameters = 1; rejected_probe(rec, initial_probe());
    rec = av; rec.ExceptionInformation[0] = 1; rejected_probe(rec, initial_probe());
    rec.ExceptionInformation[0] = 8; rejected_probe(rec, initial_probe());
    c = initial_probe(); c.Lr = 0; rejected_probe(av, c); c.Lr = 2; rejected_probe(av, c);
    c = initial_probe(); c.X10 = 0; rejected_probe(av, c); c.X10 = 2; rejected_probe(av, c);
    area.InSimulation = 1; rejected_probe(av, initial_probe()); area.InSimulation = 0;
    have_area = 0; rejected_probe(av, initial_probe()); have_area = 1;
    pBeginSimulation = NULL; rejected_probe(av, initial_probe()); pBeginSimulation = (void *)1;
    p_ios_jit_reverse_translate_addr = NULL; rejected_probe(av, initial_probe()); p_ios_jit_reverse_translate_addr = (void *)1;
    puts("PASS: other pcs, other codes, writes, fetches, bad links, emulated execution and pre-initialisation paths stay exceptions");

    const unsigned indexes[] = {0,7,11}, offsets[] = {0,8,10}, lengths[] = {8,2,4};
    ARM64_NT_CONTEXT e;
    EXCEPTION_RECORD r2;
    for (unsigned n = 0; n < 3; n++)
    {
        for (unsigned alias = 0; alias < 2; alias++)
        for (unsigned byte = 0; byte < lengths[n]; byte++)
        {
            e = initial_emu(indexes[n]); if (alias) e.Pc |= ALIAS;
            expected = e; expected.Pc = (uintptr_t)ios_dispatch_jump | ALIAS;
            r2 = av; r2.ExceptionInformation[1] = e.X9 + offsets[n] + byte;
            assert(recover_emulator_code_probe(&r2, &e) && !memcmp(&e, &expected, sizeof(e)));
        }
        e = initial_emu(indexes[n]); r2 = av;
        r2.ExceptionInformation[1] = e.X9 + offsets[n] - 1; rejected_emu(r2, e);
        r2.ExceptionInformation[1] = e.X9 + offsets[n] + lengths[n]; rejected_emu(r2, e);
    }
    puts("PASS: all three target loads of ExitToX64, every fault byte, PE and pool aliases: pc moves to DispatchJump, every other register is kept");
    for (unsigned n = 0; n < ARRAY_SIZE(dispatcher); n++) if (n != 0 && n != 7 && n != 11) rejected_emu(av, initial_emu(n));
    e = initial_emu(0); e.Pc += 2; rejected_emu(av, e);
    e = initial_emu(0); e.Pc = 0x9000; rejected_emu(av, e);
    for (unsigned n = 0; n < 12; n++) { ULONG saved = dispatcher[n]; dispatcher[n] ^= 1; rejected_emu(av, initial_emu(0)); dispatcher[n] = saved; }
    ULONG saved = dispatcher[6]; dispatcher[6] ^= 32; rejected_emu(av, initial_emu(0)); dispatcher[6] = saved;
    dispatcher[6] |= 0x800000; rejected_emu(av, initial_emu(0)); dispatcher[6] = saved;
    puts("PASS: other instructions, a changed prefix, a backward or diverging branch are rejected");
    const char *off[] = {NULL, "", "0", "00", "01", "10", "true", " 1", "2"};
    for (unsigned n = 0; n < ARRAY_SIZE(off); n++) { setting = off[n]; rejected_emu(av, initial_emu(0)); }
    setting = "1";
    puts("PASS: off unless the variable is exactly 1");
    rec = av; rec.ExceptionCode = 0x80000001; rejected_emu(rec, initial_emu(0));
    rec = av; rec.NumberParameters = 1; rejected_emu(rec, initial_emu(0));
    rec = av; rec.ExceptionInformation[0] = 1; rejected_emu(rec, initial_emu(0));
    rec.ExceptionInformation[0] = 8; rejected_emu(rec, initial_emu(0));
    e = initial_emu(0); e.Lr = 0; rejected_emu(av, e); e.Lr = 2; rejected_emu(av, e);
    e = initial_emu(0); e.X9 = UINT64_C(1) << 39; rejected_emu(av, e);
    area.InSimulation = 1; rejected_emu(av, initial_emu(0)); area.InSimulation = 0;
    have_area = 0; rejected_emu(av, initial_emu(0)); have_area = 1;
    is_ec = 1; rejected_emu(av, initial_emu(0)); is_ec = 0;
    pBeginSimulation = NULL; rejected_emu(av, initial_emu(0)); pBeginSimulation = (void *)1;
    p_ios_jit_reverse_translate_addr = NULL; rejected_emu(av, initial_emu(0)); p_ios_jit_reverse_translate_addr = (void *)1;
    ios_exit_to_x64 = NULL; rejected_emu(av, initial_emu(0)); ios_exit_to_x64 = dispatcher;
    ios_dispatch_jump = NULL; rejected_emu(av, initial_emu(0)); ios_dispatch_jump = (void *)0x8000;
    puts("PASS: guard, write and fetch faults, native or EC targets, emulated execution and an unregistered emulator stay exceptions");

    /* the exception entry */
    ARM64_NT_CONTEXT ctx = initial_probe(), keep = ctx;
    continue_fails = 0; native_continue_calls = 0;
    if (!setjmp(out)) { entry(&av, &ctx); assert(!"NtContinue returned"); }
    assert(native_continue_calls == 1 && last_continue.Pc == keep.Lr && last_continue.X9 == keep.X11 && last_continue.X11 == keep.X10 && !memcmp(&ctx, &keep, sizeof(ctx)));
    assert(err_lines == 1);
    continue_fails = 1; native_continue_calls = 0;
    ctx = initial_probe(); assert(entry(&av, &ctx) == 0 && native_continue_calls == 1 && !memcmp(&ctx, &keep, sizeof(ctx)));   /* a failed continuation leaves the context intact */
    EXCEPTION_RECORD emu_av = av;
    emu_av.ExceptionInformation[1] = 0x10000;                  /* the unreadable target is X9 */
    ctx = initial_emu(0); continue_fails = 0; native_continue_calls = 0; keep = ctx;
    if (!setjmp(out)) { entry(&emu_av, &ctx); assert(!"NtContinue returned"); }
    assert(native_continue_calls == 1 && last_continue.Pc == ((uintptr_t)ios_dispatch_jump | ALIAS) && !memcmp(&ctx, &keep, sizeof(ctx)));
    continue_fails = 1; native_continue_calls = 0; ctx = initial_emu(0); assert(entry(&emu_av, &ctx) == 0 && native_continue_calls == 1);
    rec = av; rec.ExceptionCode = 0xc0000094; native_continue_calls = 0; ctx = initial_probe(); assert(entry(&rec, &ctx) == 0 && !native_continue_calls);
    ctx = initial_emu(0); ctx.Pc += 4; native_continue_calls = 0; assert(entry(&emu_av, &ctx) == 0 && !native_continue_calls);   /* an unrelated native pc */
    setting = "0"; ctx = initial_emu(0); native_continue_calls = 0; assert(entry(&emu_av, &ctx) == 0 && !native_continue_calls); setting = "1";   /* the emulator probe is off without its variable */
    puts("PASS: the entry continues natively with the recovered context, leaves the original intact when that fails, and ignores other faults");
    return 0;
}
'''

cc = os.environ.get('CC', 'cc')
flags = ['-std=gnu11', '-O1', '-Wall', '-Wextra', '-Werror', '-Wno-unused-parameter', '-Wno-unused-variable', '-Wno-unused-function',
         '-Wno-unused-but-set-variable', '-Wno-implicit-function-declaration', '-fsanitize=address,undefined', '-fno-sanitize-recover=all', '-g']
env = dict(os.environ, ASAN_OPTIONS='detect_leaks=0')
words = ','.join('0x%08x' % w for w in (shipped_words or prefix_words))
with tempfile.TemporaryDirectory() as tmp:
    for name, text in (('notify', notify_harness.replace('@NOTIFY@', notify_fn).replace('@MAP@', map_fn)),
                       ('recover', recover_harness.replace('@WORDS@', words).replace('@PROBE@', probe_fn).replace('@GATE@', gate_fn)
                        .replace('@EMU@', emu_fn).replace('@AVPRELUDE@', av_prelude))):
        c, exe = Path(tmp) / (name + '.c'), Path(tmp) / name
        c.write_text(text)
        r = subprocess.run([cc] + flags + [str(c), '-o', str(exe)], capture_output=True, text=True)
        check('the patched %s text compiles on the host with AddressSanitizer and UBSan' % name, r.returncode == 0, r.stderr[-3500:])
        if r.returncode == 0:
            r = subprocess.run([str(exe)], capture_output=True, text=True, env=env)
            for line in r.stdout.strip().split('\n'):
                print('     ' + line)
            check('the %s cases pass' % name, r.returncode == 0 and r.stdout.count('PASS:') >= 4, (r.stdout + r.stderr)[-1800:])

# ---- 3. --verify on a real PE -----------------------------------------------------
stock = root / 'app/Madeira/arm64ec-windows/ntdll.dll'
if stock.is_file():
    raw = bytearray(stock.read_bytes())
    pad = len(raw) - 0x400
    assert not any(raw[pad:]), 'the shipped ntdll.dll no longer ends in zero padding'
    good = bytearray(raw)
    blob = b'madeira-ntdll-xsec v1\0[section-notify]\0[ec-probe-fallback]\0[ec-emulator-probe-fallback]\0'
    good[pad:pad + len(blob)] = blob
    with tempfile.TemporaryDirectory() as tmp:
        def verify(data, *extra):
            p = Path(tmp) / 'x.dll'
            p.write_bytes(bytes(data))
            return subprocess.run([sys.executable, '-I', str(script), '--verify', str(p)] + list(extra), capture_output=True, text=True)
        r = verify(good, str(stock))
        check('--verify passes the shipped ntdll.dll with the marker and log strings added (ARM64EC, probe sequences, trampoline room, stock exports)',
              r.returncode == 0 and 'ARM64EC image' in r.stdout, r.stdout + r.stderr)
        r = subprocess.run([sys.executable, '-I', str(script), '--verify', str(stock), str(stock)], capture_output=True, text=True)
        check('--verify refuses the shipped ntdll.dll itself (no marker, no log strings)', r.returncode != 0 and 'marker' in r.stderr + r.stdout)
        no_marker = bytearray(good)
        no_marker[pad:pad + 21] = b'\0' * 21
        r = verify(no_marker, str(stock))
        check('--verify refuses a build without the marker', r.returncode != 0 and 'marker' in r.stderr + r.stdout)
        triple = struct.pack('<III', 0xaa0b03e9, 0xaa0a03eb, 0xd65f03c0)
        at = bytes(good).index(triple)
        damaged = bytearray(good)
        damaged[at:at + 4] = struct.pack('<I', 0xaa0b03ea)
        r = verify(damaged, str(stock))
        check('--verify refuses a damaged .Lexit sequence', r.returncode != 0 and 'probe sequences' in r.stderr + r.stdout, r.stdout + r.stderr)
        moved = bytearray(good)
        moved[at - 4:at] = struct.pack('<I', 0xd503201f)           # a nop where the restarting branch is
        r = verify(moved, str(stock))
        check('--verify refuses a .Lexit that no branch precedes', r.returncode != 0, r.stdout + r.stderr)
        twice = bytearray(good)
        twice[pad + 200:pad + 212] = triple
        r = verify(twice, str(stock))
        check('--verify refuses a second .Lexit sequence', r.returncode != 0 and 'probe sequences' in r.stderr + r.stdout)
        not_ec = bytearray(good)
        pe_off = struct.unpack_from('<I', not_ec, 0x3c)[0]
        struct.pack_into('<H', not_ec, pe_off + 4, 0xaa64)         # an ARM64 header, not ARM64EC's AMD64
        r = verify(not_ec, str(stock))
        check('--verify refuses an image that is not ARM64EC', r.returncode != 0 and 'not an ARM64EC' in r.stderr + r.stdout)
else:
    print('skip --verify on a real PE: app/Madeira/arm64ec-windows/ntdll.dll is not in this tree')

# ---- 4. the wiring -----------------------------------------------------------------
loader = (root / 'build/ntdll-unix/loader_ios.c').read_text()
bridge = (root / 'app/Madeira/WineProcessBridge.m').read_text()
build = (root / 'tools/build-wine-extra-dlls.sh').read_text()
ci = (root / '.github/workflows/build-ipa.yml').read_text()
lst = (root / 'recommended/recommendations.json').read_text()
policy = (root / 'app/Madeira/GameRecommendationsList.swift').read_text()
catalog = (root / 'app/Madeira/ConfigCatalog.generated.swift').read_text()
gen = (root / 'build/tools/gen-config-catalog.py').read_text()
rules = (root / 'build/tools/autotune_rules.py').read_text()
name_fn = loader[loader.index('static char *ios_ec_ntdll_name('):]
name_fn = name_fn[:name_fn.index('\n}\n') + 3]
check('the loader maps ntdll-xsec.dll only for exactly env.MADEIRA_NTDLL_XSEC = 1, only in the ARM64EC directory, before ntdll-sync.dll, and falls back to what it did',
      "e && e[0] == '1' && !e[1]" in loader[loader.index('static int ios_ntdll_xsec_on'):] and name_fn.index('ios_ntdll_xsec_on()') < name_fn.index('if (mode &&')
      and '/ntdll-xsec.dll' in name_fn and name_fn.count('"/arm64ec-windows"') == 3 and 'free( name );\n        name = NULL;' in name_fn)
check('the farm links system32 and sysx64 ntdll.dll to the same file, after the ntdll-sync links (so it wins as in the loader), only for the value 1',
      'ntdll-xsec.dll' in bridge and bridge.index('ntdll-sync.dll"];') < bridge.index('ntdll-xsec.dll"];') and 'xsec[0] == \'1\' && !xsec[1]' in bridge
      and 'unsetenv("MADEIRA_NTDLL_XSEC")' in bridge and 'unsetenv("MADEIRA_D2R_PROBE_RECOVERY")' in bridge)
check('the build step makes the variant only after the stock rebuild matched, verifies it, restores the tree and ships nothing on a failure',
      'if [ -n "$XSE" ]; then' in build and 'patch-wine-ntdll-sync.py" --same-as "$B/ntdll-xsec-stock.dll" "$SHIP/ntdll.dll"' in build
      and 'patch-wine-ntdll-xsec.py" --verify "$SHIP/ntdll-xsec.dll.tmp" "$SHIP/ntdll.dll"' in build
      and 'git -C "$R/wine" checkout -- dlls/ntdll/signal_arm64ec.c' in build and 'rm -f "$SHIP/ntdll-xsec.dll.tmp"' in build
      and '$NTS$XSE$FBT' in build and 'MADEIRA_NTDLL_XSEC_BUILD' in build)
check('build-ipa.yml verifies a shipped ntdll-xsec.dll and runs this check',
      'patch-wine-ntdll-xsec.py --verify app/Madeira/arm64ec-windows/ntdll-xsec.dll' in ci and 'python3 tests/host/check-ntdll-xsec.py' in ci)
check('only the D2R list carries the two lines, from build 598',
      lst.count('"env.MADEIRA_NTDLL_XSEC = 1"') == 1 and lst.count('"env.MADEIRA_D2R_PROBE_RECOVERY = 1"') == 1
      and '{"line": "env.MADEIRA_NTDLL_XSEC = 1", "minBuild": 598}' in lst and '{"line": "env.MADEIRA_D2R_PROBE_RECOVERY = 1", "minBuild": 598}' in lst)
check('the list policy allows both names; the settings catalog has both (generator and generated file)',
      '"MADEIRA_NTDLL_XSEC"' in policy and '"MADEIRA_D2R_PROBE_RECOVERY"' in policy
      and all(('env.' + n) in catalog and ('env.' + n) in gen for n in ('MADEIRA_NTDLL_XSEC', 'MADEIRA_D2R_PROBE_RECOVERY')))
check('the auto-tune rule for D2R\'s empty code view is a fix rule with the five lines (no longer "known")',
      '"id": "d2r-loader-empty-code-view",\n        "lines": ["env.MADEIRA_NTDLL_XSEC = 1", "env.MADEIRA_SHARED_SECTION_EXEC = 1"' in rules
      and 'd2r-loader-empty-code-view' in rules[rules.index('d2r-loader-empty-code-view'):][:200]
      and '"noFix": True' not in rules[rules.index('"id": "d2r-loader-empty-code-view"'):rules.index('"id": "metal-texture-double-release"')])

print('PASS' if ok else 'FAIL')
raise SystemExit(0 if ok else 1)

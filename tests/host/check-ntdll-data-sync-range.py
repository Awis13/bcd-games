#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright 2026 mckenna654 (the sanitizer fixture and its cases: thebcd69/bcd-games#4, submitted with a Signed-off-by under CONTRIBUTING.md)
# Copyright 2026 thebcd69 (BCD) (the changes below)
# Modified by BCD (thebcd69), 2026-10-24: ported into the tree behind the public repository (opt-in switch, call-site pins, separate-process switch runs).
# Not BCD Material: LICENSE-BCD.md section 1 keeps the other author's header and licence on a file that mixes in their text.
"""env.MADEIRA_NTDLL_DATA_SYNC_RANGE = 1: a sub-page protect of ntdll's .data copies only the bytes asked for.

Based on thebcd69/bcd-games#4 by mckenna654. Diablo II: Resurrected protects the 8-byte pWow64PrepareForException slot of ntdll. ARM64EC
ntdll writes its globals in the pool copy, a guest patches the PE copy; NtProtectVirtualMemory's IAT sync copies the whole rounded page from
the PE copy over the pool copy, which also zeroes pResetToConsistentState and the notification callbacks stored on the same page (they are
written in the pool copy only). With the switch, a request that lies inside the writable, non-executable `.data` of the CURRENT process's
registered ntdll gets only its own bytes (a request under 4 KB; the loader's bulk protects keep the full sync): complete aligned 8-byte slots whose value changed are rebound to the owner's pool copy as the
full sync does, the rest is copied as bytes, the neighbours keep their live values. Everything else (other images, a request outside `.data`,
anything that cannot be placed) returns 0 and the full sync runs as before.

This compiles the production ios_sync_ntdll_data_patch and the switch reader out of build/ntdll-unix/virtual_ios.c with AddressSanitizer and
UBSan and a model of the two copies (it does not run Wine or iOS), shows the old whole-page copy erasing the callback, and pins the call site:
the helper is reached only behind the switch, after the pool destination is known and before the full copy.
"""
from pathlib import Path
import os
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


helper = function(src, 'static int ios_sync_ntdll_data_patch(')
switch_fn = function(src, 'static int ios_ntdll_data_sync_range_enabled( void )')
check('the switch is read once, by name, and is on only for the value 1',
      src.count('getenv( "MADEIRA_NTDLL_DATA_SYNC_RANGE" )') == 1 and "cached = (s && s[0] == '1' && !s[1]) ? 1 : 0;" in switch_fn
      and 'static int cached = -1;' in switch_fn)

# ---- the call site
site_start = src.index('char *jit_rw_dest = (char *)ios_jit_rw_base_global + pool_offset + off;', src.index('iOS NtProtect-sync: triggered'))
site = src[site_start:site_start + 24000]
guard = ('if (ios_ntdll_data_sync_range_enabled())\n'
         '                    {\n'
         '                        const struct ios_ntdll_funcs *funcs = ios_cur_ntdll_funcs();\n'
         '                        const void *entry = funcs ? funcs->KiUserExceptionDispatcher : pKiUserExceptionDispatcher;\n'
         '                        if (ios_sync_ntdll_data_patch( (const void *)pe_start, pe_end - pe_start,\n'
         '                                ios_jit_reverse_translate_addr( ios_patch_requested ), ios_patch_length,\n'
         '                                base, size, jit_rw_dest, sync_peb, ios_jit_reverse_translate_addr( entry ) )) break;\n'
         '                    }')
check('the helper is called only behind the switch, with the process\'s registered KiUserExceptionDispatcher, the requested range and the pool destination, and a handled patch leaves the loop',
      guard in site and src.count('ios_sync_ntdll_data_patch(') == 2)
check('the call comes after the pool destination is known and before the full copy and the relocation pass',
      site.index('ios_sync_ntdll_data_patch(') < site.index('memcpy(jit_rw_dest, base, size);') and site.index('ios_sync_ntdll_data_patch(') < site.index('Re-apply DIR64 relocations'))
check('the sync that follows is untouched (the old full-page copy, "full copy" and "partial copy" logs, are still there)',
      'iOS JIT IAT sync: full copy' in site and 'iOS JIT IAT sync: partial copy' in site)
bridge = (root / 'app/Madeira/WineProcessBridge.m').read_text()
check('a later game of the same app run does not inherit the switch', 'unsetenv("MADEIRA_NTDLL_DATA_SYNC_RANGE")' in bridge)

code = r'''
#include <assert.h>
#include <stdint.h>
#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
#define min(a,b) ((a)<(b)?(a):(b))
#define FIELD_OFFSET(t,f) offsetof(t,f)
#define IMAGE_DOS_SIGNATURE 0x5a4d
#define IMAGE_NT_SIGNATURE 0x4550
#define IMAGE_NT_OPTIONAL_HDR64_MAGIC 0x20b
#define IMAGE_SIZEOF_SHORT_NAME 8
#define IMAGE_SCN_MEM_WRITE 0x80000000u
#define IMAGE_SCN_MEM_EXECUTE 0x20000000u
#define dprintf(...) ((void)0)
typedef struct { uint16_t e_magic; int32_t e_lfanew; } IMAGE_DOS_HEADER;
typedef struct { uint32_t Signature;
    struct { uint16_t NumberOfSections, SizeOfOptionalHeader; } FileHeader;
    struct { uint16_t Magic, reserved; } OptionalHeader; } IMAGE_NT_HEADERS64;
typedef struct { unsigned char Name[8]; union { uint32_t VirtualSize; } Misc;
    uint32_t VirtualAddress, Characteristics; } IMAGE_SECTION_HEADER;
static int ios_va_is_x86_code(uint64_t p) { return p == 0x71001000; }
static unsigned translations;
static void *ios_jit_translate_addr_for_owner(void *p, void *owner)
{
    translations++;
    return (uintptr_t)p == 0x71002000 ? (void *)(0x140002000ull + (uintptr_t)owner) : p;
}
@SWITCH@
@HELPER@
static union { uint64_t align; unsigned char bytes[0x5000]; } pe_store, pool_store;
#define pe pe_store.bytes
#define pool pool_store.bytes
static IMAGE_NT_HEADERS64 *nt;
static IMAGE_SECTION_HEADER *sec;
static const void *ntdll_entry;
static void put(unsigned char *p, uint64_t v) { memcpy(p, &v, 8); }
static uint64_t get(const unsigned char *p) { uint64_t v; memcpy(&v,p,8); return v; }
static int run(size_t off, size_t n, void *owner)
{ return ios_sync_ntdll_data_patch(pe, sizeof(pe), pe+off, n, pe+0x2000, 0x1000, pool+0x2000, owner, ntdll_entry); }
int main(int argc, char **argv)
{
    const char *want = argc > 1 ? argv[1] : "1";
    if (!strcmp(want, "unset")) unsetenv("MADEIRA_NTDLL_DATA_SYNC_RANGE"); else setenv("MADEIRA_NTDLL_DATA_SYNC_RANGE", want, 1);
    int on = !strcmp(want, "1");
    assert(ios_ntdll_data_sync_range_enabled() == on);
    setenv("MADEIRA_NTDLL_DATA_SYNC_RANGE", on ? "0" : "1", 1);
    assert(ios_ntdll_data_sync_range_enabled() == on);        /* read once */
    if (!on) { printf("switch %s: off\n", want); return 0; }

    IMAGE_DOS_HEADER *dos = (void *)pe;
    dos->e_magic = IMAGE_DOS_SIGNATURE; dos->e_lfanew = 0x100;
    nt = (void *)(pe+0x100); nt->Signature = IMAGE_NT_SIGNATURE;
    nt->FileHeader.SizeOfOptionalHeader = sizeof(nt->OptionalHeader);
    nt->FileHeader.NumberOfSections = 2;
    nt->OptionalHeader.Magic = IMAGE_NT_OPTIONAL_HDR64_MAGIC;
    sec = (void *)((char *)&nt->OptionalHeader + sizeof(nt->OptionalHeader));
    memcpy(sec[0].Name,".text",6); sec[0].VirtualAddress=0x1000;
    sec[0].Misc.VirtualSize=0x1000; sec[0].Characteristics=IMAGE_SCN_MEM_EXECUTE;
    memcpy(sec[1].Name,".data",6); sec[1].VirtualAddress=0x2000;
    sec[1].Misc.VirtualSize=0x2000; sec[1].Characteristics=IMAGE_SCN_MEM_WRITE;
    ntdll_entry=pe+0x1000;
    /* Exact device pattern (the contributor's ntdll build): hook slot +0x4f0, callbacks +0xe78/+0xec8 on the same page.
     * Before the change the page memcpy erased BOTH sentinel callbacks. */
    put(pool+0x2e78,0x154111111); put(pool+0x2ec8,0x154222222);
    put(pe+0x24f0,0x71001000);
    unsigned char legacy[0x1000];
    memcpy(legacy,pe+0x2000,sizeof(legacy));
    assert(get(legacy+0xec8)==0 && get(pool+0x2ec8)!=0);
    puts("control: the old whole-page copy erases the live recovery callback");
    assert(run(0x24f0,8,(void *)1));
    assert(get(pool+0x24f0)==0x71001000 && translations==0);
    assert(get(pool+0x2e78)==0x154111111 && get(pool+0x2ec8)==0x154222222);
    assert(run(0x24f0,8,(void *)1)); /* restore protection must preserve too */
    put(pe+0x24f0,0x71002000);
    assert(run(0x24f0,8,(void *)1) && get(pool+0x24f0)==0x140002001ull);
    assert(run(0x24f0,8,(void *)2) && get(pool+0x24f0)==0x140002002ull);
    /* Unchanged constants are not rebound just because they resemble VAs. */
    put(pe+0x2500,0x71002000); put(pool+0x2500,0x71002000);
    unsigned before=translations;
    assert(run(0x2500,8,NULL) && translations==before);
    assert(get(pool+0x2500)==0x71002000);
    memset(pool+0x2510,0xa5,16); memset(pe+0x2510,0x5a,16);
    assert(run(0x2513,3,NULL));
    for(unsigned i=0;i<16;i++) assert(pool[0x2510+i]==(i>=3 && i<6?0x5a:0xa5));
    /* Partial words on both sides of a complete pointer. */
    memset(pool+0x2520,0xa5,24); memset(pe+0x2520,0x5a,24);
    put(pe+0x2528,0x71002000);
    assert(run(0x2523,16,(void *)1));
    assert(get(pool+0x2528)==0x140002001ull);
    assert(pool[0x2522]==0xa5 && pool[0x2533]==0xa5);
    /* a value the loader wrote that is x86 code of a registered image stays a PE address */
    put(pe+0x2540,0x71001000); put(pool+0x2540,0);
    assert(run(0x2540,8,(void *)1) && get(pool+0x2540)==0x71001000);
    /* a zero value is written as zero (the callback is cleared by the guest on purpose) */
    put(pool+0x2548,0x154333333); put(pe+0x2548,0);
    assert(run(0x2548,8,(void *)1) && get(pool+0x2548)==0);
    puts("PASS: ntdll sub-page data patches preserve neighbouring live callbacks");
    /* everything the helper must decline, so that the full sync runs as before */
    assert(!run(0x24f0,0,NULL));
    assert(run(0x2001,0xfff,(void *)1));         /* the largest sub-page request is taken ... */
    assert(!run(0x2000,0x1000,NULL));            /* ... a whole page and bulk protects (the loader's own) keep the full sync */
    assert(!run(0x2000,0x2000,NULL));
    assert(!run(0x2ff8,16,NULL)); /* not contained in the protected page */
    assert(!run(0x4ff8,16,NULL)); /* not contained in the image */
    assert(!ios_sync_ntdll_data_patch(pe,sizeof(pe),(void *)(UINTPTR_MAX-3),8,pe+0x2000,0x1000,pool+0x2000,NULL,ntdll_entry));
    ntdll_entry=NULL; assert(!run(0x24f0,8,NULL));
    ntdll_entry=pool; assert(!run(0x24f0,8,NULL)); ntdll_entry=pe+0x1000;       /* another module than the registered ntdll */
    assert(!run(0x1100,8,NULL));                                                 /* .text */
    sec[1].Characteristics|=IMAGE_SCN_MEM_EXECUTE; assert(!run(0x24f0,8,NULL));
    sec[1].Characteristics=0; assert(!run(0x24f0,8,NULL)); sec[1].Characteristics=IMAGE_SCN_MEM_WRITE;
    sec[1].Name[0]='?'; assert(!run(0x24f0,8,NULL)); sec[1].Name[0]='.';
    nt->FileHeader.NumberOfSections=0xffff; assert(!run(0x24f0,8,NULL));
    nt->FileHeader.NumberOfSections=2; dos->e_lfanew=0x4fff; assert(!run(0x24f0,8,NULL));
    dos->e_lfanew=-1; assert(!run(0x24f0,8,NULL));
    dos->e_lfanew=0x100; nt->Signature=0; assert(!run(0x24f0,8,NULL));
    nt->Signature=IMAGE_NT_SIGNATURE; nt->OptionalHeader.Magic=0x10b; assert(!run(0x24f0,8,NULL));
    nt->OptionalHeader.Magic=IMAGE_NT_OPTIONAL_HDR64_MAGIC;
    assert(run(0x24f0,8,(void *)1));                                             /* and it works again once restored */
    puts("PASS: other images, other sections, malformed headers and unplaceable ranges are declined (the full sync runs)");
    printf("switch %s: on\n", want);
    return 0;
}
'''

cc = os.environ.get('CC', 'cc')
flags = ['-std=gnu11', '-Wall', '-Wextra', '-Werror', '-Wno-unused-parameter', '-Wno-unused-function',
         '-fsanitize=address,undefined', '-fno-sanitize-recover=all', '-g']
env = dict(os.environ, ASAN_OPTIONS='detect_leaks=0')
with tempfile.TemporaryDirectory() as tmp:
    source, binary = Path(tmp) / 'check.c', Path(tmp) / 'check'
    source.write_text(code.replace('@SWITCH@', switch_fn).replace('@HELPER@', helper))
    r = subprocess.run([cc] + flags + [str(source), '-o', str(binary)], capture_output=True, text=True)
    check('the production helper and switch compile on the host with AddressSanitizer and UBSan', r.returncode == 0, r.stderr[-3000:])
    if r.returncode == 0:
        for value in ('unset', '', '0', '2', '01', 'true', ' 1', '1 '):
            r = subprocess.run([str(binary), value], capture_output=True, text=True, env=env)
            check('switch %r: off, read once' % value, r.returncode == 0 and 'off' in r.stdout, (r.stdout + r.stderr)[-600:])
        r = subprocess.run([str(binary), '1'], capture_output=True, text=True, env=env)
        for line in r.stdout.strip().split('\n'):
            print('     ' + line)
        check('switch 1: the neighbouring callbacks survive, pointers are rebound, the declined cases are declined',
              r.returncode == 0 and r.stdout.count('PASS:') == 2, (r.stdout + r.stderr)[-1500:])

lst = (root / 'recommended/recommendations.json').read_text()
policy = (root / 'app/Madeira/GameRecommendationsList.swift').read_text()
catalog = (root / 'app/Madeira/ConfigCatalog.generated.swift').read_text()
ci = (root / '.github/workflows/build-ipa.yml').read_text()
check('only the D2R list carries the line, from build 598', lst.count('"env.MADEIRA_NTDLL_DATA_SYNC_RANGE = 1"') == 1
      and '{"line": "env.MADEIRA_NTDLL_DATA_SYNC_RANGE = 1", "minBuild": 598}' in lst)
check('the list policy allows the name; the settings catalog has it', '"MADEIRA_NTDLL_DATA_SYNC_RANGE"' in policy and 'env.MADEIRA_NTDLL_DATA_SYNC_RANGE' in catalog)
check('build-ipa.yml runs this check', 'python3 tests/host/check-ntdll-data-sync-range.py' in ci)

print('PASS' if ok else 'FAIL')
raise SystemExit(0 if ok else 1)

#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""[nt-fail] (build/ntdll-unix/virtual_ios.c): every failed NtAllocateVirtualMemory[Ex] / NtMapViewOfSection[Ex] names itself.

Rockstar Launcher's service (RockstarService.exe) never reached its 2 MB shared-memory allocation in the failing runs of
builds 574/576 and the log could not say whether one of its calls failed: [alloc-fail] ml814 prints 64 failures of the
whole session and then every 64th (the Launcher's own 0x1000-byte probe loop spends it), covers neither the Ex variants'
address requirements nor a failed map, and never names the process.

1. The four exported functions are wrappers around the old bodies, which are forced inline and keep their code: each
   wrapper takes the hint and the size before the call, calls the body with every argument, logs only an NT error status and
   returns the body's status; the [alloc-fail] ml814 line is still there.
2. The helper region (between the ntfail-test markers) is compiled on the host and driven: a flood of one kind of failure
   (a thousand probes at a thousand hints) prints its first three and then the 4th, 8th, 16th ... and counts all; another
   status, size, protection, process or extended parameters is another kind; a hint and no hint are two kinds but two hints
   are one; the line names the api, the status, the process image, the thread, size, type, protection, hint, zero bits,
   the section handle and offset of a map and the extended parameters; the lines (240) and kinds (160) are capped and the
   kinds past the cap still count together; an image path is cut to its last component, lower case, '?' for non-ASCII.
3. With a wine checkout (WINE_SRC, default ./wine) and the iOS SDK, virtual_ios.c still compiles with build.sh's flags.
"""
from pathlib import Path
import os, re, shutil, subprocess, sys, tempfile

root = Path(__file__).resolve().parents[2]
src = (root / "build/ntdll-unix/virtual_ios.c").read_text()
ok = True


def check(what, cond, detail=""):
    global ok
    print(("ok   " if cond else "FAIL ") + what + ((" -- " + str(detail)) if (detail and not cond) else ""))
    ok = ok and bool(cond)


# ---- 1. the wrappers --------------------------------------------------------------------------------------------
APIS = {
    "NtAllocateVirtualMemory": ("HANDLE process, PVOID *ret, ULONG_PTR zero_bits,", "ios_ntfail_alloc"),
    "NtAllocateVirtualMemoryEx": ("HANDLE process, PVOID *ret, SIZE_T *size_ptr, ULONG type,", "ios_ntfail_alloc"),
    "NtMapViewOfSection": ("HANDLE handle, HANDLE process, PVOID *addr_ptr, ULONG_PTR zero_bits,", "ios_ntfail_map"),
    "NtMapViewOfSectionEx": ("HANDLE handle, HANDLE process, PVOID *addr_ptr,", "ios_ntfail_map"),
}
for api, (params, reporter) in APIS.items():
    impl = "static inline __attribute__((always_inline)) NTSTATUS ios_nt_impl_%s( %s" % (api, params)
    check(api + ": the old body is the forced-inline ios_nt_impl_" + api, src.count(impl) == 1)
    check(api + ": no other definition of the exported name but the wrapper",
          len(re.findall(r"^NTSTATUS WINAPI %s\(" % api, src, re.M)) == 1)
    m = re.search(r"^NTSTATUS WINAPI %s\(.*?^}\n" % api, src, re.M | re.S)
    wrapper = m.group(0) if m else ""
    check(api + ": the wrapper takes the hint and the size first, calls the body, logs only an NT error, returns its status",
          "const void *hint = " in wrapper and "const ULONGLONG want = " in wrapper
          and wrapper.index("const ULONGLONG want") < wrapper.index("ios_nt_impl_" + api + "(")
          and "if (NT_ERROR( status ))" in wrapper and reporter + "( \"%s\"" % api in wrapper
          and wrapper.rstrip().endswith("return status;\n}") and wrapper.count("return") == 1, wrapper)
    # every parameter of the wrapper goes to the body
    sig = re.search(r"NTSTATUS WINAPI %s\((.*?)\)\n\{" % api, wrapper, re.S).group(1)
    names = [re.split(r"[ *]", p.strip())[-1] for p in sig.split(",")]
    call = re.search(r"ios_nt_impl_%s\((.*?)\);" % api, wrapper, re.S).group(1)
    check(api + ": the body gets every argument, in order", [a.strip() for a in call.split(",")] == names, (names, call))
check("[alloc-fail] ml814 is untouched", 'dprintf( 2, "[alloc-fail] ml814 #%lu status=%08x hint=%p size=0x%llx type=0x%x "' in src)

# ---- 2. the helper region ---------------------------------------------------------------------------------------
begin = src.index("/* ntfail-test:begin")
end = src.index("/* ntfail-test:end */")
region = src[begin:end]
harness = r"""
#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
""" + region + r"""
static unsigned short w[260];
static void wide( const char *s ) { size_t i; for (i = 0; s[i]; i++) w[i] = (unsigned char)s[i]; w[i] = 0; }
int main( void )
{
    char name[64];
    int i, pass;

    /* image names */
    wide( "C:\\Program Files\\Rockstar Games\\Launcher\\RockstarService.exe" );
    ios_ntfail_basename( w, wcslen_u( w ), name, sizeof(name) );
    printf( "NAME1 %s\n", name );
    wide( "/unix/Path/Launcher.EXE" );
    ios_ntfail_basename( w, wcslen_u( w ), name, sizeof(name) );
    printf( "NAME2 %s\n", name );
    w[0] = 'a'; w[1] = 0x3b1; w[2] = 'B'; w[3] = 0;
    ios_ntfail_basename( w, 3, name, sizeof(name) );
    printf( "NAME3 %s\n", name );
    wide( "C:\\x\\averyveryverylongname.exe" );
    ios_ntfail_basename( w, wcslen_u( w ), name, 8 );
    printf( "NAME4 %s\n", name );

    /* a flood of one kind: the Launcher's probe loop, a thousand hints */
    for (i = 0; i < 1000; i++)
        ios_ntfail_note( "NtAllocateVirtualMemory", 0xc0000018, "launcher.exe", 0, 0xcc, 0x1000, 0x3000, 0x40,
                         0x73f4562000ULL + 0x1000ULL * i, 0, 0, 0, "" );
    printf( "FLOOD-END\n" );
    /* the same call without a hint is another kind; so is another status, size, protection, process, ext, remote */
    ios_ntfail_note( "NtAllocateVirtualMemory", 0xc0000018, "launcher.exe", 0, 0xcc, 0x1000, 0x3000, 0x40, 0, 0, 0, 0, "" );
    ios_ntfail_note( "NtAllocateVirtualMemory", 0xc0000017, "launcher.exe", 0, 0xcc, 0x1000, 0x3000, 0x40, 0, 0, 0, 0, "" );
    ios_ntfail_note( "NtAllocateVirtualMemory", 0xc0000018, "launcher.exe", 0, 0xcc, 0x2000, 0x3000, 0x40, 0, 0, 0, 0, "" );
    ios_ntfail_note( "NtAllocateVirtualMemory", 0xc0000018, "launcher.exe", 0, 0xcc, 0x1000, 0x3000, 0x04, 0, 0, 0, 0, "" );
    ios_ntfail_note( "NtAllocateVirtualMemory", 0xc0000018, "rockstarservice.exe", 0, 0xf8, 0x1000, 0x3000, 0x40, 0, 0, 0, 0, "" );
    ios_ntfail_note( "NtAllocateVirtualMemoryEx", 0xc000000d, "rockstarservice.exe", 0, 0xf8, 0x200000, 0x3000, 0x04, 0, 0, 0, 0,
                     "addr[low=0x100000000 high=0x7fffffff0000 align=0x10000]" );
    ios_ntfail_note( "NtAllocateVirtualMemoryEx", 0xc000000d, "rockstarservice.exe", 1, 0xf8, 0x200000, 0x3000, 0x04, 0, 0, 0, 0,
                     "addr[low=0x100000000 high=0x7fffffff0000 align=0x10000]" );
    ios_ntfail_note( "NtMapViewOfSection", 0xc0000018, "rockstarservice.exe", 0, 0xf8, 0, 0x2000, 0x04, 0x7140990000ULL, 0x10,
                     0x70, 0x1000, "" );
    printf( "KINDS-END\n" );
    /* two hints, one kind: a second pair of calls with other addresses adds hits, not lines */
    ios_ntfail_note( "NtMapViewOfSection", 0xc0000018, "rockstarservice.exe", 0, 0xf8, 0, 0x2000, 0x04, 0x7140a00000ULL, 0x10,
                     0x71, 0x2000, "" );
    printf( "SAMEKIND-END\n" );
    /* the kind table: 160 kinds fit, the next ones count together (lines at 1, 2, 4, 8...), the lines stop at 240 */
    for (pass = 0; pass < 3; pass++)
        for (i = 0; i < 400; i++)
            ios_ntfail_note( "NtMapViewOfSection", 0xc0000001 + (unsigned)i, "other.exe", 0, 1, 0x10, 0, 0, 0, 0, 0, 0, "" );
    printf( "TABLE-END lines=%u other=%u\n", ios_ntfail_lines, ios_ntfail_other );
    return 0;
}
"""
harness = harness.replace("static unsigned short w[260];",
                          "static unsigned short w[260];\nstatic size_t wcslen_u( const unsigned short *s ) { size_t n = 0; while (s[n]) n++; return n; }")
with tempfile.TemporaryDirectory() as t:
    t = Path(t)
    (t / "nf.c").write_text(harness)
    cc = shutil.which("cc") or "cc"
    b = subprocess.run([cc, "-std=gnu11", "-Wall", "-Wextra", "-Wno-unused-function", "-fsanitize=address,undefined",
                        "-fno-sanitize-recover=undefined", "-o", str(t / "nf"), str(t / "nf.c")], capture_output=True, text=True)
    check("the helper region compiles on the host without a warning", b.returncode == 0 and not b.stderr.strip(), b.stderr[-1500:])
    if b.returncode == 0:
        env = dict(os.environ, ASAN_OPTIONS="symbolize=0:detect_leaks=0")   # the symbolizer opens a password dialog on this Mac
        r = subprocess.run([str(t / "nf")], capture_output=True, text=True, env=env)
        check("the harness runs clean under ASAN/UBSAN", r.returncode == 0, r.stderr[-1500:])
        out, err = r.stdout, r.stderr
        check("an image path is cut to its last component, lower case",
              "NAME1 rockstarservice.exe" in out and "NAME2 launcher.exe" in out)
        check("non-ASCII becomes '?', and the name is cut to the buffer", "NAME3 a?b" in out and "NAME4 averyve" in out, out)
        lines = [l for l in err.split("\n") if l.startswith("[nt-fail]")]
        # section of lines before each marker: the harness writes markers to stdout, lines to stderr; count by kind
        flood = [l for l in lines if "status=c0000018" in l and "proc=launcher.exe" in l and "size=0x1000" in l
                 and "prot=0x40" in l and "hint=0x7" in l]
        hits = [int(re.search(r"hits=(\d+)", l).group(1)) for l in flood]
        check("a thousand probes of one kind print their 1st, 2nd, 3rd, then the 4th, 8th ... 512th",
              hits == [1, 2, 3, 4, 8, 16, 32, 64, 128, 256, 512], hits)
        check("every line says the api, the thread, the type and a rev tag",
              all(re.match(r"\[nt-fail\] #\d+ Nt\w+ status=[0-9a-f]{8} proc=\S+( \(remote target\))? tid=[0-9a-f]{4} size=0x[0-9a-f]+ type=0x[0-9a-f]+ "
                           r"prot=0x[0-9a-f]+ hint=0x[0-9a-f]+ zb=0x[0-9a-f]+", l) and "rev=ajan5-rockstar" in l for l in lines))
        kinds = [l for l in lines if l not in flood]
        first = [l for l in kinds if "hits=1 " in l][:10]
        check("the same call without a hint, another status, size, protection, process, extended parameters, remote target and a map are "
              "kinds of their own", len([l for l in first if "proc=launcher.exe" in l]) == 4
              and len([l for l in first if "proc=rockstarservice.exe" in l]) == 4, first)
        ext = [l for l in lines if "NtAllocateVirtualMemoryEx" in l]
        check("extended parameters and a remote target are told", len(ext) == 2 and all("ext={addr[low=0x100000000 high=0x7fffffff0000 align=0x10000]}" in l for l in ext)
              and sum("(remote target)" in l for l in ext) == 1, ext)
        mp = [l for l in lines if "NtMapViewOfSection " in l and "proc=rockstarservice.exe" in l]
        check("a map names its section handle and offset, and a second address is the same kind (hits 2, not a new kind)",
              len(mp) == 2 and "h=0x70 off=0x1000" in mp[0] and "hint=0x7140990000 zb=0x10" in mp[0] and "hits=2" in mp[1], mp)
        total = re.search(r"TABLE-END lines=(\d+) other=(\d+)", out)
        nl = len(lines)
        check("the lines are capped at 240 in all and the kinds past 160 count together",
              total and int(total.group(1)) > 240 and nl == 240 and int(total.group(2)) > 0, (total.group(0) if total else out, nl))
        other = [l for l in lines if "proc=other.exe" in l]
        late = [l for l in lines if "proc=other.exe" in l and int(re.search(r"status=([0-9a-f]+)", l).group(1), 16) > 0xc00000a1]
        check("the kinds past the table print only on a power of two of their own count",
              late and all(bin(int(re.search(r"hits=(\d+)", l).group(1))).count("1") == 1 for l in late), late[:3])

# ---- 3. the real file, when a wine checkout is there ----------------------------------------------------------------
wine = Path(os.environ.get("WINE_SRC", root / "wine"))
xcrun = shutil.which("xcrun")
bld = next((p for p in (wine / "build-macos", wine / "build-arm64ec") if (p / "include/config.h").exists()), None)
if bld and xcrun and (wine / "dlls/ntdll/unix").is_dir():
    sdk = subprocess.run([xcrun, "--sdk", "iphoneos", "--show-sdk-path"], capture_output=True, text=True).stdout.strip()
    shims = root / "build/ntdll-unix/shims"
    cmd = [xcrun, "-sdk", "iphoneos", "clang", "-arch", "arm64", "-isysroot", sdk, "-miphoneos-version-min=17.0", "-O2",
           "-fsyntax-only", "-fno-strict-aliasing", "-Wno-implicit-function-declaration", "-Wno-int-conversion",
           "-include", str(bld / "include/config.h"), "-include", str(shims / "wine_ios_exit.h"), "-I" + str(shims),
           "-I" + str(bld / "dlls/ntdll"), "-I" + str(wine / "dlls/ntdll"), "-I" + str(wine / "dlls/ntdll/unix"),
           "-I" + str(bld / "include"), "-I" + str(wine / "include"), "-D__WINESRC__", "-DLTC_NO_PROTOTYPES", "-DLTC_SOURCE",
           "-D_NTSYSTEM_", "-D_ACRTIMP=", "-DWINBASEAPI=", "-DWINE_UNIX_LIB", "-DWINE_IOS=1",
           "-Dget_thread_context=ntdll_get_thread_context", "-Dset_thread_context=ntdll_set_thread_context",
           str(root / "build/ntdll-unix/virtual_ios.c")]
    r = subprocess.run(cmd, capture_output=True, text=True)
    check("virtual_ios.c compiles with build.sh's flags (the always_inline bodies included)",
          r.returncode == 0 and "error:" not in r.stderr, r.stderr[-1500:])
else:
    print("note: no wine checkout or iOS SDK here; virtual_ios.c is compiled by the build only")

print("PASS: [nt-fail]" if ok else "FAILED: [nt-fail]")
sys.exit(0 if ok else 1)

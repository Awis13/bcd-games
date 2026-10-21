#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""[map-name] (build/wineserver/mapping_ios.c): every named section a process creates or opens, with the status.

The Rockstar Launcher shares a 2 MB block with RockstarService.exe; in the failing runs of builds 574/576 the service never
mapped it ("Failed to allocate shared memory") and the log held no name, status or process for any section: [srv-map] had
no name, an open that failed was not logged and [sc-shm] knows only Social Club's "rgsc_" names.

1. create_mapping and open_mapping each call ws_map_name_log once, after their own work and before they return (the reply
   is already made); [srv-map] and [sc-shm] are still there.
2. The helper region (mapname-test markers) is compiled on the host and driven: a UTF-16 name becomes printable ASCII with
   '?' for the rest and keeps its last 103 characters; the same (operation, name, status) prints its 1st, 2nd, 4th, 8th and
   16th time and no more; a name with another status or operation is another entry; 160 lines in all; 160 entries.
3. With a wine checkout (WINE_SRC) and the iOS SDK mapping_ios.c compiles with build.sh's flags.
"""
from pathlib import Path
import os, re, shutil, subprocess, sys, tempfile

root = Path(__file__).resolve().parents[2]
src = (root / "build/wineserver/mapping_ios.c").read_text()
ok = True


def check(what, cond, detail=""):
    global ok
    print(("ok   " if cond else "FAIL ") + what + ((" -- " + str(detail)) if (detail and not cond) else ""))
    ok = ok and bool(cond)


# ---- 1. the two handlers ----------------------------------------------------------------------------------------
create = re.search(r"^DECL_HANDLER\(create_mapping\)\n\{.*?^}\n", src, re.M | re.S).group(0)
opn = re.search(r"^DECL_HANDLER\(open_mapping\)\n\{.*?^}\n", src, re.M | re.S).group(0)
check("create_mapping logs its name once, after the mapping is made and its handle allocated",
      create.count("ws_map_name_log(") == 1 and create.index("alloc_handle") < create.index("ws_map_name_log(")
      and create.index("ws_map_name_log(") < create.index("release_object( root )"), create)
check("open_mapping logs its name once, after open_object",
      opn.count("ws_map_name_log(") == 1 and opn.index("open_object") < opn.index("ws_map_name_log("), opn)
check("neither handler's reply is touched by the log",
      "reply->" not in re.search(r"ws_map_name_log\(.*?\);", create, re.S).group(0).replace("reply->handle", "")
      and "reply->" not in re.search(r"ws_map_name_log\(.*?\);", opn, re.S).group(0).replace("reply->handle", ""))
check("[srv-map] create_mapping FAILED and the [sc-shm] lines are still there",
      '"[srv-map] create_mapping FAILED err=%08x flags=%08x size=%llu pid=%04x\\n"' in create
      and create.count('"[sc-shm] create_mapping') == 1 and opn.count('"[sc-shm] open_mapping') == 1)

# ---- 2. the helper region ---------------------------------------------------------------------------------------
region = src[src.index("/* mapname-test:begin"):src.index("/* mapname-test:end */")]
harness = r"""
#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
""" + region + r"""
static unsigned short w[400];
static size_t wide( const char *s ) { size_t i; for (i = 0; s[i]; i++) w[i] = (unsigned char)s[i]; return i * 2; }
int main( void )
{
    char out[104];
    unsigned int hits, line, i, j;
    size_t len;

    len = wide( "\\Sessions\\1\\BaseNamedObjects\\Global\\RockstarShared" );
    ws_mapname_ascii( w, len, out, sizeof(out) );
    printf( "A %s\n", out );
    w[3] = 0x3b1; w[4] = 0x7;
    ws_mapname_ascii( w, len, out, sizeof(out) );
    printf( "B %s\n", out );
    for (i = 0; i < 300; i++) w[i] = 'a' + i % 26;
    ws_mapname_ascii( w, 300 * 2, out, sizeof(out) );
    printf( "C %zu %c%c\n", strlen( out ), out[0], out[102] );
    ws_mapname_ascii( w, 0, out, sizeof(out) );
    printf( "D [%s]\n", out );

    /* one entry: 40 repeats print at 1, 2, 4, 8, 16 */
    for (i = 0; i < 40; i++)
        if (ws_mapname_take( "open", "\\x\\name", 0xc0000034, &hits, &line )) { printf( "E hits=%u line=%u\n", hits, line ); }
    /* another status or operation is another entry */
    printf( "F %d %d %d\n", ws_mapname_take( "open", "\\x\\name", 0, &hits, &line ), ws_mapname_take( "create", "\\x\\name", 0xc0000034, &hits, &line ),
            ws_mapname_take( "open", "\\x\\other", 0xc0000034, &hits, &line ) );
    /* the table (160) and the lines (160) */
    for (j = 0; j < 3; j++)
        for (i = 0; i < 300; i++)
        {
            char nm[32]; snprintf( nm, sizeof(nm), "\\n%u", i );
            ws_mapname_take( "create", nm, 0, &hits, &line );
        }
    printf( "G lines=%u\n", ws_mapname_lines );
    return 0;
}
"""
with tempfile.TemporaryDirectory() as t:
    t = Path(t)
    (t / "mn.c").write_text(harness)
    cc = shutil.which("cc") or "cc"
    b = subprocess.run([cc, "-std=gnu11", "-Wall", "-Wextra", "-Wno-unused-function", "-fsanitize=address,undefined",
                        "-fno-sanitize-recover=undefined", "-o", str(t / "mn"), str(t / "mn.c")], capture_output=True, text=True)
    check("the helper region compiles on the host without a warning", b.returncode == 0 and not b.stderr.strip(), b.stderr[-1500:])
    if b.returncode == 0:
        env = dict(os.environ, ASAN_OPTIONS="symbolize=0:detect_leaks=0")   # the symbolizer opens a password dialog on this Mac
        r = subprocess.run([str(t / "mn")], capture_output=True, text=True, env=env)
        check("the harness runs clean under ASAN/UBSAN", r.returncode == 0, r.stderr[-1500:])
        out = r.stdout
        check("a name becomes printable ASCII", "A \\Sessions\\1\\BaseNamedObjects\\Global\\RockstarShared" in out)
        check("non-ASCII and control characters become '?'", "B \\Se?\\" not in out and "B \\Se" in out and "?" in out.split("B ")[1].split("\n")[0], out)
        check("a long name keeps its last 103 characters", "C 103 " in out, out)
        check("an empty name is empty", "D []" in out)
        e = re.findall(r"E hits=(\d+) line=(\d+)", out)
        check("the same operation, name and status prints at its 1st, 2nd, 4th, 8th and 16th time only",
              [int(h) for h, _ in e] == [1, 2, 4, 8, 16], e)
        check("another status, another operation and another name are entries of their own", "F 1 1 1" in out, out)
        check("the lines are capped at 160", re.search(r"G lines=(\d+)", out) and int(re.search(r"G lines=(\d+)", out).group(1)) >= 160, out)

# ---- 3. the real file, when a wine checkout is there --------------------------------------------------------------
wine = Path(os.environ.get("WINE_SRC", root / "wine"))
xcrun = shutil.which("xcrun")
bld = next((p for p in (wine / "build-macos", wine / "build-arm64ec") if (p / "include").is_dir()), None)
if bld and xcrun and (wine / "server").is_dir():
    sdk = subprocess.run([xcrun, "--sdk", "iphoneos", "--show-sdk-path"], capture_output=True, text=True).stdout.strip()
    ws = root / "build/wineserver"
    cmd = [xcrun, "-sdk", "iphoneos", "clang", "-arch", "arm64", "-isysroot", sdk, "-miphoneos-version-min=17.0", "-O2", "-fsyntax-only",
           "-I" + str(wine / "include"), "-I" + str(wine / "include/wine"), "-I" + str(bld / "include"), "-I" + str(ws),
           "-I" + str(wine / "server"), "-I" + str(root / "build/ntdll-unix/shims"), "-I" + str(root / "build/madsync"),
           "-DHAVE_LINUX_NTSYNC_H=1", "-include", str(ws / "config_ios.h"), "-include", "stdarg.h",
           "-include", str(ws / "unicode_fix.h"), "-include", str(ws / "wineserver_ios_kill.h"),
           '-DBINDIR="/usr/local/bin"', '-DDATADIR="/usr/local/share"', "-D__WINESRC__", "-DWINE_IOS=1", "-Dmain=wineserver_main",
           "-Wno-implicit-function-declaration", str(ws / "mapping_ios.c")]
    r = subprocess.run(cmd, capture_output=True, text=True)
    check("mapping_ios.c compiles with build.sh's flags", r.returncode == 0 and "error:" not in r.stderr, r.stderr[-1500:])
else:
    print("note: no wine checkout or iOS SDK here; mapping_ios.c is compiled by the build only")

print("PASS: [map-name]" if ok else "FAILED: [map-name]")
sys.exit(0 if ok else 1)

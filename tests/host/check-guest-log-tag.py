#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""tools/patch-wine-guest-log-tag.py: the log-level tag "[DISPLAY]" keeps no line of a game's own log; no Wine runs.

Rockstar Games Launcher's launcher.log tags every non-error line "[DISPLAY]"; as the word "display" it used the whole
64-line [guest-log] budget before the launcher's error text (19 of 20 GTA V / RDR2 logs of 2026-10-20).

1. Applies the patch to the mirror as tools/patch-wine-guest-log.py writes it (a skeleton file.c) and, when the wine
   checkout is present, to the pinned dlls/ntdll/unix/file.c: once, then "already patched"; a file without the
   mirror fails by name and is not written.
2. Compiles the patched helper region and writes real launcher.log lines: default mode keeps "Display adapter" and
   the [ ERROR ] lines but not "[DISPLAY] ... Opening log file."; MADEIRA_GUEST_LOG=all still mirrors every line;
   a game log that says "display" in its own words (Ghost of Tsushima style) still matches; the budget holds.
"""
from pathlib import Path
import os, re, subprocess, sys, tempfile

root = Path(__file__).resolve().parents[2]
mirror = root / "tools/patch-wine-guest-log.py"
tag = root / "tools/patch-wine-guest-log-tag.py"
wine = Path(os.environ.get("WINE_SRC", root / "wine"))
real = wine / "dlls/ntdll/unix/file.c"

skeleton = """#include <errno.h>
#include <fcntl.h>
#include <limits.h>

/******************************************************************************
 *              NtWriteFile   (NTDLL.@)
 */
NTSTATUS WINAPI NtWriteFile( void )
{
done:
    send_completion = cvalue != 0;

err:
    if (needs_close) close( unix_handle );

    if (type == FD_TYPE_SERIAL && (status == STATUS_SUCCESS || status == STATUS_PENDING))
        set_pending_write( handle );
}
"""


def run(script, path):
    return subprocess.run([sys.executable, str(script), str(path)], capture_output=True, text=True)


def keep_function(text):
    return text[text.index("static int ios_guest_log_keep"):text.index("/* A BOM can be its own NtWriteFile call.")]


with tempfile.TemporaryDirectory() as t:
    t = Path(t)
    sources = [("skeleton", skeleton)]
    if real.exists():
        sources.append(("wine file.c", real.read_text()))
    else:
        print("note: no wine checkout (%s); only the skeleton is patched" % real)
    patched = None
    for name, text in sources:
        p = t / "file.c"
        p.write_text(text)
        if "static void ios_guest_log_mirror" not in text:
            r = run(tag, p)
            assert r.returncode != 0 and "anchor" in r.stderr and p.read_text() == text, (name, r)   # no mirror: fails by name
            r = run(mirror, p)
            assert r.returncode == 0 and "mirror added" in r.stdout, (name, r)
        before = p.read_text()
        r1, r2 = run(tag, p), run(tag, p)
        assert r1.returncode == 0 and "applied" in r1.stdout, (name, r1)
        assert r2.returncode == 0 and "already patched" in r2.stdout, (name, r2)
        after = p.read_text()
        assert after.count("madeira-bcd: [guest-log] tag") == 1, name
        # Only the keyword function changed.
        assert after.replace(keep_function(after), "") == before.replace(keep_function(before), ""), name
        patched = after
        print("ok   %s: applies once, only ios_guest_log_keep changes" % name)

    region = patched[patched.index("/* guest-log-test:begin"):patched.index("/* guest-log-test:end */")]
    harness = r"""
#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
#include <unistd.h>
static unsigned int GetCurrentThreadId(void) { return 0x24; }
""" + region + r"""
static int open_file( const char *dir, const char *name )
{
    char p[512]; snprintf( p, sizeof(p), "%s/%s", dir, name );
    return open( p, O_CREAT | O_TRUNC | O_WRONLY, 0644 );
}
static void put( int fd, const char *s ) { ios_guest_log_mirror( fd, s, (unsigned)strlen( s ) ); }
int main( int argc, char **argv )
{
    const char *dir = argv[1];
    int rl = open_file( dir, "launcher.log" ), gl = open_file( dir, "GhostOfTsushima.log" );
    int i;
    put( rl, "[2026-10-20 13:41:47.261] [DISPLAY] [Main ] [launcher] Opening log file.\n" );
    put( rl, "[2026-10-20 13:41:47.395] [DISPLAY] [Main ] [external] Steam not detected as parent process\n" );
    put( rl, "[2026-10-20 13:41:47.420] [display] [Main ] [render] Display adapter: Apple A18 Pro GPU\n" );
    put( rl, "[2026-10-20 13:41:47.500] [DISPLAY] [Main ] [packfile] Initialize 'anim_0.rpf' - failed\n" );
    put( rl, "[2026-10-20 13:41:48.121] [ ERROR ] [Main ] [launcher] Steam is not initialized\n" );
    put( rl, "[2026-10-20 13:41:48.132] [ ERROR ] [Main ] [error] * Code:      113 (LAUNCHER_ERR_STEAM_FAILED)\n" );
    put( gl, "12:00:01 > [NxApp] Failed to get GPU Driver Info\n" );
    for (i = 0; i < 70; i++) { char l[96]; snprintf( l, sizeof(l), "[x] [DISPLAY] [Main ] [loop] line %d\n", i ); put( rl, l ); }
    put( rl, "[2026-10-20 13:41:49.000] [ ERROR ] [Main ] [late] after 70 chatter lines\n" );
    return 0;
}
"""
    c = t / "gl.c"; c.write_text(harness)
    exe = t / "gl"
    subprocess.run(["cc", "-std=gnu11", "-Wall", "-Wno-unused-function", "-fsanitize=address,undefined",
                    str(c), "-o", str(exe)], check=True)

    def mirrored(env_extra):
        d = tempfile.mkdtemp(dir=t)
        env = {k: v for k, v in os.environ.items() if not k.startswith("MADEIRA_GUEST_LOG")}
        env.update(env_extra)
        r = subprocess.run([str(exe), d], capture_output=True, text=True, env=env)
        assert r.returncode == 0, (env_extra, r.returncode, r.stderr)
        return [re.sub(r"^\[guest-log\] #\d+ tid=0024 ", "", l) for l in r.stderr.splitlines()
                if re.match(r"\[guest-log\] #\d+", l)]

    body = mirrored({})
    assert body == ["[2026-10-20 13:41:47.420] [display] [Main ] [render] Display adapter: Apple A18 Pro GPU",
                    "[2026-10-20 13:41:47.500] [DISPLAY] [Main ] [packfile] Initialize 'anim_0.rpf' - failed",
                    "[2026-10-20 13:41:48.121] [ ERROR ] [Main ] [launcher] Steam is not initialized",
                    "[2026-10-20 13:41:48.132] [ ERROR ] [Main ] [error] * Code:      113 (LAUNCHER_ERR_STEAM_FAILED)",
                    "12:00:01 > [NxApp] Failed to get GPU Driver Info",
                    "[2026-10-20 13:41:49.000] [ ERROR ] [Main ] [late] after 70 chatter lines"], body
    print("ok   default mode: the tag alone keeps nothing; display words, failures and [ ERROR ] lines do, past 70 chatter lines")
    body = mirrored({"MADEIRA_GUEST_LOG": "all"})
    assert len(body) == 6 + 1 + 70 + 1 and body[0].endswith("Opening log file."), len(body)
    print("ok   MADEIRA_GUEST_LOG=all still mirrors every line")
    assert len(mirrored({"MADEIRA_GUEST_LOG_LIMIT": "2"})) == 2
    assert mirrored({"MADEIRA_GUEST_LOG": "0"}) == []
    print("ok   the line budget and the off switch are untouched")

print("PASS: guest-log tag")

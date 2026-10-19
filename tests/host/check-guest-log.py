#!/usr/bin/env python3
"""[guest-log] mirror (tools/patch-wine-guest-log.py); no Wine runs.

1. Applies the patch to a copy of wine's dlls/ntdll/unix/file.c (the wine
   submodule, or $WINE_SRC) twice: the second run must be a no-op, the hook
   must sit after `err:` and before the fd is closed. Skipped with a note when
   no wine checkout is present.
2. Compiles the patched helper region on its own (Linux: the fd's path comes
   from /proc/self/fd instead of Darwin's F_GETPATH) and writes real files:
   default mode mirrors error/GPU/display lines of *.log files only, names the
   file once, skips binary writes and other text files, dedups a repeated
   line and honours the line budget; MADEIRA_GUEST_LOG=all mirrors every line;
   MADEIRA_GUEST_LOG=0 and the old MADEIRA_GUEST_LOG_ERRORS=0 turn it off;
   errno is preserved. UTF-16 LE/BE (including a separately written BOM) is
   decoded; malformed UTF-16 and control-containing writes are refused.
"""
from pathlib import Path
import os, re, subprocess, sys, tempfile

root = Path(__file__).resolve().parents[2]
patch = root / "tools/patch-wine-guest-log.py"
wine = Path(os.environ.get("WINE_SRC", root / "wine"))
real = wine / "dlls/ntdll/unix/file.c"

# A file.c skeleton with the two anchors the patch needs (and its includes).
skeleton = """#include <errno.h>
#include <fcntl.h>
#include <limits.h>

static void other(void) {}

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

def apply(path):
    out = subprocess.run([sys.executable, str(patch), str(path)], capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    return out.stdout

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
        if name != "skeleton" and "static void ios_guest_log_mirror" in text:
            # The wine pin carries the mirror itself (willfaust/wine#31, pinned since
            # the 2026-10-15 upstream merge): the patch leaves it as it is.
            assert "already patched" in apply(p), name
            assert p.read_text() == text, name + ": the patch changed a file that has the mirror"
        else:
            assert "mirror added" in apply(p), name
        once = p.read_text()
        assert "already patched" in apply(p), name
        assert p.read_text() == once, name + ": second run changed the file"
        tail = once[once.index("err:\n#ifdef WINE_IOS"):]
        assert tail.index("ios_guest_log_mirror( unix_handle, buffer, total )") < tail.index("close( unix_handle )"), name
        assert once.index("static void ios_guest_log_mirror") < once.index(" *              NtWriteFile   (NTDLL.@)"), name
        patched = once

    region = patched[patched.index("/* guest-log-test:begin"):patched.index("/* guest-log-test:end */")]
    wide_writes = {
        "le_bom": b"\xff\xfe",
        "le_info": "SDK initialized: café Türkçe 😀\r\n".encode("utf-16-le"),
        "le_error": b"\xff\xfe" + "Fatal Error: code 1024\r\n".encode("utf-16-le"),
        "be_error": b"\xfe\xff" + "Error: browser timeout\n".encode("utf-16-be"),
        "be_info": "Waiting for Browser\n".encode("utf-16-be"),
        "bad_high": "error ".encode("utf-16-le") + b"\x00\xd8A\x00",
        "bad_low": "error ".encode("utf-16-le") + b"\x00\xdc",
        "bad_odd": "error odd".encode("utf-16-le") + b"x",
        "bad_null": "error \0 hidden\n".encode("utf-16-le"),
        "bad_control": "error \x01 hidden\n".encode("utf-16-le"),
        "bad_after_full": b"\xff\xfe" + ("error " + "界" * 1700 + "\x01").encode("utf-16-le"),
        "unicode_long": ("error " + "a" * 293 + "😀" + "b" * 100 + "\n").encode("utf-16-le"),
        "clipped_pair": ("error " + "a" * 2041 + "😀").encode("utf-16-le"),
    }
    arrays = "\n".join("static const unsigned char %s[] = {%s};" %
                       (name, ",".join(str(v) for v in data)) for name, data in wide_writes.items())
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
""" + region + arrays + r"""
static int open_file( const char *dir, const char *name )
{
    char p[512]; snprintf( p, sizeof(p), "%s/%s", dir, name );
    return open( p, O_CREAT | O_TRUNC | O_WRONLY, 0644 );
}
static void put( int fd, const char *s ) { ios_guest_log_mirror( fd, s, (unsigned)strlen( s ) ); }
int main( int argc, char **argv )
{
    const char *dir = argv[1];
    int log = open_file( dir, "GhostOfTsushima.log" ), txt = open_file( dir, "settings.txt" );
    int out = open_file( dir, "output_log.txt" );
    static const char bin[] = "abc\0def error\n";
    if (argc > 2 && !strcmp( argv[2], "wide" ))
    {
        unsigned char disk[sizeof(le_info)];
        int wide = open_file( dir, "socialclub.log" );
        char small[8];
        static const unsigned char cjk[] = {255,254,0x4c,0x75,0x4c,0x75,0x4c,0x75};
        static const unsigned char invalid_tail[] = {255,254,0x4c,0x75,0x4c,0x75,1,0};
        if (ios_guest_log_utf16( cjk, sizeof(cjk), 0, small, sizeof(small) ) != 6) return 11;
        if (ios_guest_log_utf16( invalid_tail, sizeof(invalid_tail), 0, small, 3 ) != -1) return 12;
        if (write( wide, le_bom, sizeof(le_bom) ) != sizeof(le_bom)) return 13;
        ios_guest_log_mirror( wide, le_bom, sizeof(le_bom) ); /* separate BOM */
        if (write( wide, le_info, sizeof(le_info) ) != sizeof(le_info)) return 14;
        errno = 1234;
#define MIRROR(fd, name) do { ios_guest_log_mirror( fd, name, sizeof(name) ); \
                             if (errno != 1234) return 15; } while (0)
        MIRROR( wide, le_info );
        MIRROR( wide, le_error );
        MIRROR( wide, be_error );
        MIRROR( wide, be_info );
        MIRROR( txt, le_error );       /* a UTF-16 non-log file is still ignored */
        MIRROR( wide, bad_high );
        MIRROR( wide, bad_low );
        MIRROR( wide, bad_odd );
        MIRROR( wide, bad_null );
        MIRROR( wide, bad_control );
        MIRROR( wide, bad_after_full );
        MIRROR( wide, unicode_long );
        MIRROR( wide, clipped_pair );
        close( wide );
        wide = open_file( dir, "unused.log" ); /* fd reuse cannot inherit an encoding */
        put( wide, "error UTF-8 after reuse\n" );
        close( wide );
        {
            char path[512];
            snprintf( path, sizeof(path), "%s/socialclub.log", dir );
            wide = open( path, O_RDONLY );
            if (lseek( wide, sizeof(le_bom), SEEK_SET ) != sizeof(le_bom)) return 16;
            if (read( wide, disk, sizeof(disk) ) != sizeof(disk) || memcmp( disk, le_info, sizeof(disk))) return 17;
            close( wide );
        }
        return 0;
    }
    errno = 1234;
    put( log, "12:00:00 > [NxApp] Initializing\n12:00:01 > [NxApp] Failed to get GPU Driver Info\r\n" );
    if (errno != 1234) return 9;
    put( log, "12:00:01 > [NxApp] Failed to get GPU Driver Info\n" );           /* repeated: dedup */
    put( txt, "error in a text file that is not a log\n" );                    /* not *.log */
    ios_guest_log_mirror( log, bin, sizeof(bin) - 1 );                          /* binary */
    put( log, "x\n" );
    put( out, "[Render] Display adapter: NVIDIA GeForce RTX 3060\n" );
    put( log, "line one error\nline two error\nline three error\nline four error\n" );
    return 0;
}
"""
    c = t / "gl.c"; c.write_text(harness)
    exe = t / "gl"
    subprocess.run(["cc", "-std=gnu11", "-Wall", "-Wno-unused-function", "-fsanitize=address,undefined",
                    str(c), "-o", str(exe)], check=True)

    def run(env_extra, scenario=None):
        d = tempfile.mkdtemp(dir=t)
        env = {k: v for k, v in os.environ.items() if not k.startswith("MADEIRA_GUEST_LOG")}
        env.update(env_extra)
        r = subprocess.run([str(exe), d] + ([scenario] if scenario else []), capture_output=True, text=True, env=env)
        assert r.returncode == 0, (env_extra, r.returncode, r.stderr)
        return [l for l in r.stderr.splitlines() if l.startswith("[guest-log]")]

    lines = run({})
    body = [re.sub(r"^\[guest-log\] #\d+ tid=0024 ", "", l) for l in lines if re.match(r"\[guest-log\] #\d+", l)]
    files = [l for l in lines if l.startswith("[guest-log] file ")]
    assert "error/GPU/display lines, 64 lines" in lines[0], lines
    assert body == ["12:00:01 > [NxApp] Failed to get GPU Driver Info",
                    "[Render] Display adapter: NVIDIA GeForce RTX 3060",
                    "line one error", "line two error", "line three error", "line four error"], body
    assert len(files) == 2 and files[0].endswith("/GhostOfTsushima.log") and files[1].endswith("/output_log.txt"), files

    lines = run({"MADEIRA_GUEST_LOG_LIMIT": "3"})
    assert len([l for l in lines if re.match(r"\[guest-log\] #\d+", l)]) == 3, lines

    lines = run({"MADEIRA_GUEST_LOG": "all"})
    body = [re.sub(r"^\[guest-log\] #\d+ tid=0024 ", "", l) for l in lines if re.match(r"\[guest-log\] #\d+", l)]
    assert body[:2] == ["12:00:00 > [NxApp] Initializing", "12:00:01 > [NxApp] Failed to get GPU Driver Info"], body
    assert "every line, 400 lines" in lines[0] and len(body) == 7, (lines[0], body)

    for off in ({"MADEIRA_GUEST_LOG": "0"}, {"MADEIRA_GUEST_LOG_ERRORS": "0"}):
        lines = run(off)
        assert len(lines) == 1 and "mirror: off" in lines[0], (off, lines)

    lines = run({"MADEIRA_GUEST_LOG": "all"}, "wide")
    body = [re.sub(r"^\[guest-log\] #\d+ tid=0024 ", "", l) for l in lines if re.match(r"\[guest-log\] #\d+", l)]
    assert body[:4] == ["SDK initialized: café Türkçe 😀", "Fatal Error: code 1024",
                        "Error: browser timeout", "Waiting for Browser"], body
    assert len(body) == 7 and body[-1] == "error UTF-8 after reuse", body
    assert body[4] == "error " + "a" * 293 + " [truncated]", body[4]
    assert body[5] == "error " + "a" * 294 + " [truncated]", body[5]
    assert len([l for l in lines if l.startswith("[guest-log] file ")]) == 2, lines
    lines = run({}, "wide")
    body = [l for l in lines if re.match(r"\[guest-log\] #\d+", l)]
    assert len(body) == 5 and all("SDK initialized" not in l and "Waiting for Browser" not in l for l in body), body
    assert len([l for l in run({"MADEIRA_GUEST_LOG_LIMIT": "2"}, "wide") if re.match(r"\[guest-log\] #\d+", l)]) == 2
    assert len(run({"MADEIRA_GUEST_LOG": "0"}, "wide")) == 1

print("PASS: patch idempotent and hooked before close; UTF-8 and UTF-16 LE/BE logs including separate BOM, Unicode "
      "and bounded prefixes; malformed/binary/non-log writes refused; budget, dedup, modes, bytes and errno preserved")

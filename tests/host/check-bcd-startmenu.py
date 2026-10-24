#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""BCD's entries in the Start menu of Wine's desktop (BCDStartMenu.c, WineProcessBridge.m).

Wine's explorer builds the Start menu from the .lnk files under C:\\ProgramData\\Microsoft\\Windows\\Start Menu;
BCD writes an Explorer entry and one test program per Direct3D 9, 10, 11 and 12 there, as shortcut files
in the binary Shell Link format ([MS-SHLLINK]). Checks:
  1. The bytes. BCDStartMenu.c is built on the host into a driver, and every shortcut it writes is
       - parsed by a port of Wine's loader (dlls/shell32/shelllink.c: IPersistStream::Load with
         Stream_ReadChunk, Stream_LoadLocation, Stream_LoadVolume, Stream_LoadPath, Stream_LoadString, the
         trailing zero word), which has to give the target path, the description, the working folder and
         the arguments back, and to reach the end of the file exactly;
       - parsed by the independent reader below, which walks the [MS-SHLLINK] structures by their
         offsets and sizes and checks them against the file's length (the header, the LinkInfo block, the
         VolumeID, the string data, the terminal block);
       - dumped by Wine's own winedump (`winedump dump x.lnk`, built in ~/madeira-bcd/wine/build-native) when
         the build tree has it: its local path and description must be the ones BCD wrote.
  2. What the install does in a prefix: it needs the template's public Start Menu folder, writes only the
     entries whose program the app carries, is idempotent (a second run writes nothing), brings an
     outdated BCD file up to date, and never touches a file without BCD's marker, a symlink or a folder of
     the same name; the files are the same bytes every time.
  3. The session: only a desktop session (MADEIRA_DESKTOP=1, not a BCD Dock session) gets the files, env.MADEIRA_START_MENU = 0
     leaves them out, the call sits with the session's other prefix work, and a game that is not started
     in a Wine desktop sees no change.
  4. The Xcode project builds the new C file; the licence headers; the programs the entries name are
     the ones tools/build-dx-tests.sh and the D3D12 build ship; the workflow runs this check.
"""
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

R = Path(__file__).resolve().parents[2]
APP = R / "app/Madeira"
ok = True


def check(what, cond, detail=""):
    global ok
    print(("ok   " if cond else "FAIL ") + what + (("\n     " + str(detail)[-3000:]) if detail and not cond else ""))
    ok = ok and bool(cond)


def note(text):
    print("note " + text)


# ---------------------------------------------------------------- 1. the bytes
DRIVER_C = r'''
#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>
#include "BCDStartMenu.h"

/* ---- a port of Wine's shell link loader (dlls/shell32/shelllink.c), reading from memory ---- */
#pragma pack(push,1)
typedef struct { uint32_t dwSize; uint8_t MagicGuid[16]; uint32_t dwFlags; uint32_t dwFileAttr;
                 uint64_t CreationTime, AccessTime, WriteTime; uint32_t dwFileSize; uint32_t nIcon; uint32_t fStartup;
                 uint16_t wHotKey; uint16_t Reserved1; uint32_t Reserved2; uint32_t Reserved3; } LINK_HEADER;
typedef struct { uint32_t dwTotalSize, dwHeaderSize, dwFlags, dwVolTableOfs, dwLocalPathOfs, dwNetworkVolTableOfs, dwFinalPathOfs; } LOCATION_INFO;
typedef struct { uint32_t dwSize, dwType, dwVolSerial, dwVolLabelOfs; } LOCAL_VOLUME_INFO;
#pragma pack(pop)
#define SLDF_HAS_ID_LIST 1u
#define SLDF_HAS_LINK_INFO 2u
#define SLDF_HAS_NAME 4u
#define SLDF_HAS_RELPATH 8u
#define SLDF_HAS_WORKINGDIR 0x10u
#define SLDF_HAS_ARGS 0x20u
#define SLDF_HAS_ICONLOCATION 0x40u
#define SLDF_UNICODE 0x80u
#define SLDF_HAS_LOGO3ID 0x100u
#define SLDF_HAS_DARWINID 0x1000u

typedef struct { const uint8_t *d; size_t n, pos; } stream;
/* IStream_Read: returns how many bytes it gave, a short read at the end of the data */
static size_t srd(stream *s, void *buf, size_t len)
{
    size_t avail = s->n - s->pos, c = len < avail ? len : avail;
    memcpy(buf, s->d + s->pos, c);
    s->pos += c;
    return c;
}

/* Stream_ReadChunk: a 32-bit size that counts itself, then size-4 bytes; the chunk keeps the size field */
static uint8_t *read_chunk(stream *s)
{
    uint32_t size;
    uint8_t *chunk;
    if (srd(s, &size, 4) != 4) return NULL;
    if (size < 4) return NULL;                         /* Wine would read (size - 4) wrapped; BCD never writes it */
    chunk = malloc(size);
    memcpy(chunk, &size, 4);
    if (srd(s, chunk + 4, size - 4) != size - 4) { free(chunk); return NULL; }
    return chunk;
}

/* Stream_LoadPath: an ANSI string of at most maxlen bytes, ASCII widened here */
static char *load_path(const char *p, uint32_t maxlen)
{
    uint32_t len = 0;
    char *out;
    while (len < maxlen && p[len]) len++;
    out = malloc(len + 1);
    memcpy(out, p, len);
    out[len] = 0;
    return out;
}

/* Stream_LoadLocation + Stream_LoadVolume */
static int load_location(stream *s, char **path, uint32_t *vol_type, char *label, size_t labelcap)
{
    uint8_t *p = read_chunk(s);
    LOCATION_INFO *loc;
    uint32_t n;
    if (!p) return 0;
    loc = (LOCATION_INFO *)p;
    if (loc->dwTotalSize < sizeof(LOCATION_INFO)) { free(p); return 0; }
    if (loc->dwVolTableOfs && loc->dwVolTableOfs + sizeof(LOCAL_VOLUME_INFO) <= loc->dwTotalSize) {
        LOCAL_VOLUME_INFO *vol = (LOCAL_VOLUME_INFO *)&p[loc->dwVolTableOfs];
        *vol_type = vol->dwType;
        if (vol->dwVolLabelOfs && vol->dwSize > vol->dwVolLabelOfs) {
            uint32_t len = vol->dwSize - vol->dwVolLabelOfs;
            const char *l = (const char *)vol + vol->dwVolLabelOfs;
            size_t i = 0;
            while (i < len && l[i] && i + 1 < labelcap) { label[i] = l[i]; i++; }
            label[i] = 0;
        }
    }
    n = loc->dwLocalPathOfs;
    if (n && n < loc->dwTotalSize) *path = load_path((char *)&p[n], loc->dwTotalSize - n);
    free(p);
    return 1;
}

/* Stream_LoadString: a 16-bit count (characters when unicode), the text, no terminator in the file */
static int load_string(stream *s, int unicode, char **out)
{
    uint16_t len;
    uint32_t bytes;
    uint8_t *temp;
    size_t i, count;
    if (srd(s, &len, 2) != 2) return 0;
    bytes = unicode ? (uint32_t)len * 2 : len;
    temp = malloc(bytes + 2);
    if (srd(s, temp, bytes) != bytes) { free(temp); return 0; }
    count = unicode ? bytes / 2 : bytes;
    *out = malloc(count + 1);
    for (i = 0; i < count; i++) {
        if (unicode) {
            uint16_t c = (uint16_t)(temp[2 * i] | (temp[2 * i + 1] << 8));
            (*out)[i] = c < 0x80 ? (char)c : '?';
        } else (*out)[i] = (char)temp[i];
    }
    (*out)[count] = 0;
    free(temp);
    return 1;
}

typedef struct { char *path, *desc, *rel, *workdir, *args, *icon; uint32_t vol_type; char label[16]; int zero_word, at_end; uint32_t flags; } loaded;

/* IPersistStream::Load, in Wine's order and with its checks; returns 1 for S_OK */
static int wine_load(const uint8_t *data, size_t len, loaded *L)
{
    stream s = { data, len, 0 };
    LINK_HEADER hdr;
    uint32_t zero = 1;
    int unicode;
    memset(L, 0, sizeof *L);
    if (sizeof(hdr) != 0x4C) return 0;
    if (srd(&s, &hdr, sizeof hdr) != sizeof hdr) return 0;
    if (hdr.dwSize != sizeof hdr) return 0;
    {
        static const uint8_t clsid[16] = { 0x01, 0x14, 0x02, 0, 0, 0, 0, 0, 0xC0, 0, 0, 0, 0, 0, 0, 0x46 };
        if (memcmp(hdr.MagicGuid, clsid, 16) != 0) return 0;
    }
    L->flags = hdr.dwFlags;
    if (hdr.dwFlags & SLDF_HAS_ID_LIST) return 0;          /* BCD writes none; the port does not read one */
    if ((hdr.dwFlags & SLDF_HAS_LINK_INFO) && !load_location(&s, &L->path, &L->vol_type, L->label, sizeof L->label)) return 0;
    unicode = (hdr.dwFlags & SLDF_UNICODE) != 0;
    if ((hdr.dwFlags & SLDF_HAS_NAME) && !load_string(&s, unicode, &L->desc)) return 0;
    if ((hdr.dwFlags & SLDF_HAS_RELPATH) && !load_string(&s, unicode, &L->rel)) return 0;
    if ((hdr.dwFlags & SLDF_HAS_WORKINGDIR) && !load_string(&s, unicode, &L->workdir)) return 0;
    if ((hdr.dwFlags & SLDF_HAS_ARGS) && !load_string(&s, unicode, &L->args)) return 0;
    if ((hdr.dwFlags & SLDF_HAS_ICONLOCATION) && !load_string(&s, unicode, &L->icon)) return 0;
    if (hdr.dwFlags & (SLDF_HAS_LOGO3ID | SLDF_HAS_DARWINID)) return 0;
    L->zero_word = srd(&s, &zero, 4) == 4 && zero == 0;     /* Wine only warns when this is not so */
    L->at_end = s.pos == s.n;
    return 1;
}

static uint8_t *slurp(const char *path, size_t *len)
{
    FILE *f = fopen(path, "rb");
    uint8_t *b;
    long n;
    if (!f) return NULL;
    fseek(f, 0, SEEK_END); n = ftell(f); fseek(f, 0, SEEK_SET);
    b = malloc((size_t)n + 1);
    if (fread(b, 1, (size_t)n, f) != (size_t)n) { fclose(f); return NULL; }
    fclose(f);
    *len = (size_t)n;
    return b;
}

static void put(const char *p, const char *what) { (void)p; (void)what; }

int main(int argc, char **argv)
{
    const char *cmd = argc > 1 ? argv[1] : "";
    (void)put;
    if (!strcmp(cmd, "install")) {                /* install <prefix> <bundle> */
        bcd_start_result r = bcd_startmenu_install(argv[2], argv[3]);
        printf("written=%d current=%d foreign=%d missing=%d failed=%d no_menu=%d\n",
               r.written, r.current, r.foreign, r.missing, r.failed, r.no_menu);
        return 0;
    }
    if (!strcmp(cmd, "entries")) {
        size_t i;
        for (i = 0; i < bcd_start_entry_count; i++)
            printf("%s|%s|%s|%s\n", bcd_start_entries[i].folder, bcd_start_entries[i].name, bcd_start_entries[i].program,
                   bcd_start_entries[i].description);
        return 0;
    }
    if (!strcmp(cmd, "load")) {                   /* load <file> */
        size_t len;
        uint8_t *b = slurp(argv[2], &len);
        loaded L;
        if (!b) { puts("unreadable"); return 1; }
        if (!wine_load(b, len, &L)) { puts("REFUSED"); return 1; }
        printf("flags=%#x\npath=%s\ndesc=%s\nworkdir=%s\nargs=%s\nvoltype=%u\nlabel=%s\nzero=%d\nend=%d\nowner=%d\n", L.flags,
               L.path ? L.path : "(none)", L.desc ? L.desc : "(none)", L.workdir ? L.workdir : "(none)",
               L.args ? L.args : "(none)", L.vol_type, L.label, L.zero_word, L.at_end, bcd_lnk_owner(b, len));
        return 0;
    }
    if (!strcmp(cmd, "build")) {                  /* build <out> <target> <desc> [args [workdir]] */
        uint8_t buf[8192];
        size_t n = bcd_lnk_build(argv[3], argc > 5 && *argv[5] ? argv[5] : NULL, argc > 6 && *argv[6] ? argv[6] : NULL,
                                 argv[4], buf, sizeof buf);
        FILE *f;
        if (!n) { puts("refused"); return 1; }
        f = fopen(argv[2], "wb"); fwrite(buf, 1, n, f); fclose(f);
        printf("%zu\n", n);
        return 0;
    }
    if (!strcmp(cmd, "owner")) {                  /* owner <file> */
        size_t len;
        uint8_t *b = slurp(argv[2], &len);
        printf("%d\n", b ? bcd_lnk_owner(b, len) : -9);
        return 0;
    }
    if (!strcmp(cmd, "small")) {                  /* a buffer that is too small is refused, never overrun */
        uint8_t buf[512], big[512];
        size_t n, need = bcd_lnk_build("C:\\windows\\system32\\explorer.exe", NULL, NULL, "x", big, sizeof big);
        int bad = need == 0;
        for (n = 0; n < 300; n++) {
            size_t got;
            memset(buf, 0xAA, sizeof buf);
            got = bcd_lnk_build("C:\\windows\\system32\\explorer.exe", NULL, NULL, "x", buf, n);
            if (n < need && got != 0) bad++;
            if (n >= need && (got != need || memcmp(buf, big, need) != 0)) bad++;
            if (n < 500 && memcmp(buf + n, "\xAA\xAA\xAA\xAA", 4) != 0) bad++;   /* nothing past `cap` */
        }
        printf("bad=%d need=%zu\n", bad, need);
        return 0;
    }
    return 2;
}
'''


def sh(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


tmp = Path(tempfile.mkdtemp(prefix="bcd-startmenu-"))
try:
    drv_c = tmp / "driver.c"
    drv_c.write_text(DRIVER_C)
    drv = tmp / "driver"
    flags = ["-std=gnu11", "-Wall", "-Wextra", "-Werror", "-Wno-unused-function", "-I", str(APP)]
    san = []
    probe = sh(["clang", "-fsanitize=address,undefined", "-x", "c", "-", "-o", str(tmp / "probe")], input="int main(void){return 0;}")
    if probe.returncode == 0 and sh([str(tmp / "probe")]).returncode == 0:
        san = ["-fsanitize=address,undefined", "-fno-sanitize-recover=undefined"]
    else:
        note("clang has no working sanitizers here; the driver runs without them")
    build = sh(["clang", *flags, *san, "-O1", "-g", str(drv_c), str(APP / "BCDStartMenu.c"), "-o", str(drv)])
    check("BCDStartMenu.c and the driver build with -Wall -Wextra -Werror" + (" and ASan/UBSan" if san else ""),
          build.returncode == 0, build.stderr)

    def run(*a):
        return sh([str(drv), *map(str, a)])

    def fields(out):
        d = {}
        for line in out.splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                d[k] = v
        return d

    # An independent reader: [MS-SHLLINK] by offsets and sizes, strict about every length.
    def spec_parse(b):
        problems = []
        if len(b) < 0x4C + 4:
            return {"problems": ["shorter than a header and a terminal block"]}
        if struct.unpack_from("<I", b, 0)[0] != 0x4C:
            problems.append("HeaderSize is not 0x4C")
        if b[4:20] != bytes.fromhex("0114020000000000c000000000000046"):
            problems.append("LinkCLSID is not 00021401-0000-0000-C000-000000000046")
        flags_ = struct.unpack_from("<I", b, 0x14)[0]
        info = {"flags": flags_}
        if struct.unpack_from("<I", b, 0x3C)[0] not in (1, 3, 7):
            problems.append("ShowCommand is not SW_SHOWNORMAL, SHOWMAXIMIZED or SHOWMINNOACTIVE")
        if struct.unpack_from("<H", b, 0x42)[0] != 0 or struct.unpack_from("<I", b, 0x44)[0] != 0 or struct.unpack_from("<I", b, 0x48)[0] != 0:
            problems.append("a reserved field is not zero")
        pos = 0x4C
        if flags_ & 1:
            pos += 2 + struct.unpack_from("<H", b, pos)[0]
        if flags_ & 2:
            size, hsize, lflags, vofs, lofs, nofs, sofs = struct.unpack_from("<IIIIIII", b, pos)
            if pos + size > len(b):
                problems.append("LinkInfoSize runs past the file")
            if hsize != 0x1C:
                problems.append("LinkInfoHeaderSize is not 0x1C (no optional Unicode offsets)")
            if lflags != 1:
                problems.append("LinkInfoFlags is not VolumeIDAndLocalBasePath")
            if vofs != 0x1C:
                problems.append("VolumeIDOffset is not right behind the header")
            vsize, dtype, serial, loff = struct.unpack_from("<IIII", b, pos + vofs)
            if vsize <= 0x10 or loff != 0x10 or vofs + vsize != lofs:
                problems.append("VolumeID size or label offset is wrong")
            if dtype not in (0, 1, 2, 3, 4, 5, 6):
                problems.append("DriveType is not a drive type")
            if nofs != 0:
                problems.append("CommonNetworkRelativeLinkOffset set without the flag")
            end = b.index(b"\0", pos + lofs)
            info["base"] = b[pos + lofs:end].decode("ascii")
            if sofs != lofs + len(info["base"]) + 1:
                problems.append("CommonPathSuffixOffset is not right behind the base path")
            if b[pos + sofs] != 0 or sofs + 1 != size:
                problems.append("the common path suffix is not one empty string ending the block")
            pos += size
        for bit, key in ((4, "name"), (8, "rel"), (0x10, "dir"), (0x20, "args"), (0x40, "icon")):
            if flags_ & bit:
                n = struct.unpack_from("<H", b, pos)[0]
                pos += 2
                if flags_ & 0x80:
                    info[key] = b[pos:pos + 2 * n].decode("utf-16-le")
                    pos += 2 * n
                else:
                    info[key] = b[pos:pos + n].decode("ascii")
                    pos += n
        if len(b) != pos + 4 or struct.unpack_from("<I", b, pos)[0] >= 4:
            problems.append("the file does not end with a terminal block of four bytes")
        info["problems"] = problems
        return info

    if build.returncode == 0:
        work = tmp / "bundle-test"
        prefix = work / "prefix"
        bundle = work / "app"
        menu = prefix / "drive_c/ProgramData/Microsoft/Windows/Start Menu"
        (menu / "Programs").mkdir(parents=True)
        (bundle / "arm64ec-windows").mkdir(parents=True)
        (bundle / "aarch64-windows").mkdir(parents=True)

        ents = [l.split("|") for l in run("entries").stdout.splitlines()]
        check("the Start menu has the Explorer entry and the four DirectX tests",
              [(e[1]) for e in ents] == ["Explorer", "DirectX 9 test", "DirectX 10 test", "DirectX 11 test", "DirectX 12 test"], ents)
        check("Explorer is at the top of the menu, the tests in Programs/BCD",
              ents[0][0] == "" and all(e[0] == "Programs/BCD" for e in ents[1:]), ents)
        programs = {e[1]: e[2] for e in ents}
        check("the entries start explorer.exe and the four x64 test programs",
              programs == {"Explorer": "explorer.exe", "DirectX 9 test": "d3d9-cube-x64.exe", "DirectX 10 test": "d3d10-cube-x64.exe",
                           "DirectX 11 test": "d3d11-cube-x64.exe", "DirectX 12 test": "d3d12-cube-x64.exe"}, programs)

        # the app carries explorer.exe (aarch64) and the four tests (arm64ec)
        (bundle / "aarch64-windows/explorer.exe").write_bytes(b"MZ")
        for p in programs.values():
            if p != "explorer.exe":
                (bundle / "arm64ec-windows" / p).write_bytes(b"MZ")

        r = fields(run("install", prefix, bundle).stdout.replace(" ", "\n"))
        check("the first install writes all five entries", r == {"written": "5", "current": "0", "foreign": "0", "missing": "0", "failed": "0", "no_menu": "0"}, r)
        files = {e[1]: menu / e[0] / (e[1] + ".lnk") for e in ents}
        check("the files are where Wine's menu looks", all(f.is_file() for f in files.values()), [str(f) for f in files.values()])
        check("nothing but the five links and the BCD folder was added",
              sorted(p.name for p in menu.rglob("*") if p.is_file()) == sorted(e[1] + ".lnk" for e in ents)
              and sorted(p.name for p in menu.iterdir()) == ["Explorer.lnk", "Programs"]
              and sorted(p.name for p in (menu / "Programs").iterdir()) == ["BCD"],
              sorted(str(p.relative_to(menu)) for p in menu.rglob("*")))

        for name, f in files.items():
            wine = fields(run("load", f).stdout)
            spec = spec_parse(f.read_bytes())
            want_target = "C:\\windows\\system32\\" + programs[name]
            check(name + ": Wine's loader gives the target, the description, no folder or arguments, and the whole file",
                  wine.get("path") == want_target and wine.get("desc", "").startswith("[BCD] ") and wine.get("workdir") == "(none)"
                  and wine.get("args") == "(none)" and wine.get("zero") == "1" and wine.get("end") == "1" and wine.get("owner") == "1"
                  and wine.get("voltype") == "3", wine)
            check(name + ": [MS-SHLLINK] by its offsets and sizes", not spec["problems"] and spec.get("base") == want_target
                  and spec.get("name", "").startswith("[BCD] "), spec)
            check(name + ": a name string Wine's menu can show (the file name; the description is only a tooltip)",
                  f.stem == name and "/" not in name and "\\" not in name)

        # the same bytes every time, and with arguments and a working folder
        a, b = tmp / "a.lnk", tmp / "b.lnk"
        run("build", a, "C:\\windows\\system32\\x.exe", "With arguments", "-one \"two\"", "C:\\windows")
        run("build", b, "C:\\windows\\system32\\x.exe", "With arguments", "-one \"two\"", "C:\\windows")
        check("a link is the same bytes every time", a.read_bytes() == b.read_bytes())
        wine = fields(run("load", a).stdout)
        spec = spec_parse(a.read_bytes())
        check("arguments and a working folder come back through Wine's loader",
              wine.get("args") == '-one "two"' and wine.get("workdir") == "C:\\windows" and wine.get("zero") == "1" and wine.get("end") == "1", wine)
        check("... and through the independent reader", not spec["problems"] and spec.get("args") == '-one "two"' and spec.get("dir") == "C:\\windows", spec)

        # winedump, Wine's own dumper, when this checkout is next to a build tree
        for cand in (os.environ.get("WINEDUMP"), str(R / "wine/build-native/tools/winedump/winedump"),
                     str(Path.home() / "madeira-bcd/wine/build-native/tools/winedump/winedump")):
            if cand and Path(cand).exists():
                dumped = sh([cand, "dump", str(files["Explorer"])])
                text = dumped.stdout
                check("winedump parses the Explorer link: HAS_LINK_INFO HAS_NAME UNICODE, local path, description",
                      dumped.returncode == 0 and "HAS_LINK_INFO HAS_NAME UNICODE" in text
                      and 'LocalPath ofs = 0000002d ("C:\\windows\\system32\\explorer.exe")' in text
                      and "Description : [BCD] Opens Wine's file manager" in text and "Done dumping" in text, text)
                dumped = sh([cand, "dump", str(a)])
                check("winedump parses a link with arguments and a working folder",
                      dumped.returncode == 0 and "HAS_WORKINGDIR" in dumped.stdout and "HAS_ARGS" in dumped.stdout
                      and "-one" in dumped.stdout and "C:\\windows" in dumped.stdout, dumped.stdout)
                break
        else:
            note("winedump is not built here (wine/build-native/tools/winedump): Wine's dumper was not run")

        # idempotent: a second run writes nothing
        before = {n: f.read_bytes() for n, f in files.items()}
        stamp = {n: f.stat().st_mtime_ns for n, f in files.items()}
        r = fields(run("install", prefix, bundle).stdout.replace(" ", "\n"))
        check("a second install writes nothing", r.get("written") == "0" and r.get("current") == "5" and r.get("failed") == "0", r)
        check("... and the files are the same bytes and were not rewritten",
              all(f.read_bytes() == before[n] and f.stat().st_mtime_ns == stamp[n] for n, f in files.items()))
        check("no temporary file is left", not list(menu.rglob("*.bcdtmp")))

        # an outdated BCD file is brought up to date
        old = files["DirectX 9 test"]
        run("build", old, "C:\\windows\\system32\\d3d9-old.exe", "Older text")
        r = fields(run("install", prefix, bundle).stdout.replace(" ", "\n"))
        check("a BCD file that is outdated is written again, the rest stay", r.get("written") == "1" and r.get("current") == "4", r)
        check("... with the bytes of the current entry", old.read_bytes() == before["DirectX 9 test"])

        # never a file that is not BCD's
        mine = files["DirectX 10 test"]
        own = b"MZ-not-a-link, written by a person"
        mine.write_bytes(own)
        other = files["DirectX 11 test"]
        # a real-looking link of the person's own: no marker
        run("build", other, "C:\\Games\\mine.exe", "x")
        data = bytearray(other.read_bytes())
        data = bytes(data).replace("[BCD] ".encode("utf-16-le"), "mine: ".encode("utf-16-le"))
        other.write_bytes(data)
        tests_dir = menu / "Programs/BCD"
        link_target = tmp / "elsewhere.lnk"
        link_target.write_bytes(b"kept")
        sym = files["DirectX 12 test"]
        sym.unlink()
        sym.symlink_to(link_target)
        r = fields(run("install", prefix, bundle).stdout.replace(" ", "\n"))
        check("files that are not BCD's (not a link, a person's own link, a symbolic link) are counted and left alone",
              r.get("foreign") == "3" and r.get("written") == "0" and r.get("failed") == "0", r)
        check("... byte for byte", mine.read_bytes() == own and other.read_bytes() == data and sym.is_symlink() and link_target.read_bytes() == b"kept")
        for f in (mine, other):
            f.unlink()
        sym.unlink()
        (tests_dir / "DirectX 12 test.lnk").mkdir()        # a folder where the file should be
        r = fields(run("install", prefix, bundle).stdout.replace(" ", "\n"))
        check("a folder of the entry's name is left alone and not counted as written", r.get("foreign") == "1" and r.get("written") == "2", r)
        (tests_dir / "DirectX 12 test.lnk").rmdir()

        # an entry whose program the app does not carry gets none
        shutil.rmtree(menu)
        (menu / "Programs").mkdir(parents=True)
        (bundle / "arm64ec-windows/d3d10-cube-x64.exe").unlink()
        r = fields(run("install", prefix, bundle).stdout.replace(" ", "\n"))
        check("an entry whose program is not in the app is skipped", r.get("written") == "4" and r.get("missing") == "1", r)
        check("... and its file is not made", not (menu / "Programs/BCD/DirectX 10 test.lnk").exists())

        # a prefix without the public Start Menu folder gets nothing
        bare = work / "bare"
        (bare / "drive_c").mkdir(parents=True)
        r = fields(run("install", bare, bundle).stdout.replace(" ", "\n"))
        check("a prefix without the template's Start Menu folder gets nothing, and none is made",
              r.get("no_menu") == "1" and r.get("written") == "0" and not (bare / "drive_c/ProgramData").exists(), r)

        # refused input is refused, a buffer that is too small is never overrun
        check("a target with a non-ASCII byte is refused", run("build", tmp / "x.lnk", "C:\\caf\u00e9.exe", "d").returncode == 1)
        check("an empty target is refused", run("build", tmp / "x.lnk", "", "d").returncode == 1)
        small = run("small")
        check("every buffer smaller than the link is refused, every large enough one gets the same bytes, nothing is written past it",
              small.stdout.startswith("bad=0 "), small.stdout + small.stderr)
        garbage = tmp / "garbage.lnk"
        garbage.write_bytes(files["Explorer"].read_bytes()[:90])
        check("a truncated link is read as not a link", run("owner", garbage).stdout.strip() == "-1")
        garbage.write_bytes(b"\0" * 200)
        check("zero bytes are read as not a link", run("owner", garbage).stdout.strip() == "-1")
finally:
    shutil.rmtree(tmp, ignore_errors=True)

# ---------------------------------------------------------------- 3. the session
bridge = (APP / "WineProcessBridge.m").read_text()
check("WineProcessBridge.m includes the module", '#include "BCDStartMenu.h"' in bridge or '#import "BCDStartMenu.h"' in bridge)
call = bridge.find("bcd_startmenu_install(")
check("the session calls bcd_startmenu_install once", bridge.count("bcd_startmenu_install(") == 1 and call > 0)
block = bridge[max(0, call - 1800):call + 900]
check("only a desktop session (MADEIRA_DESKTOP=1) gets the files", 'getenv("MADEIRA_DESKTOP")' in block)
check("a BCD Dock session (MADEIRA_DOCK_SESSION=1) does not: Valve's client and its games run there", 'getenv("MADEIRA_DOCK_SESSION")' in block
      and "!(dockSession && dockSession[0] == '1')" in block)
check("env.MADEIRA_START_MENU = 0 leaves them out", 'getenv("MADEIRA_START_MENU")' in block)
check("the call sits after the shell-folder repairs, before the VC++ overlay",
      bridge.index("[shell-dir] user folders for") < call < bridge.index("Layer Microsoft's real VC++ Runtime DLLs"))
check("the result is logged", "[start-menu]" in block)

# The user's own Start Menu folder (build 598). Battle.net on build 597: pressing the Start button read address 0 in Wine's
# explorer (0x1400192cc, fill_menu) and the session ended, because the user's half of the menu had no folder while the public
# half had entries. The template's registry puts the user's Start Menu under %USERPROFILE% and ships the folder for its own
# user only, so the session makes it for the running user like the other profile folders.
import tarfile
with tarfile.open(APP / "prefix-template.tar.gz") as tf:
    names_t = tf.getnames()
    user_reg = tf.extractfile("prefix/user.reg").read().decode("utf-8", "replace")
usf = user_reg[user_reg.index("[Software\\\\Microsoft\\\\Windows\\\\CurrentVersion\\\\Explorer\\\\User Shell Folders]"):]
usf = usf[:usf.index("\n\n")]
check("the template's User Shell Folders put the user's Start Menu under %USERPROFILE%",
      '"Start Menu"=str(2):"%USERPROFILE%\\\\AppData\\\\Roaming\\\\Microsoft\\\\Windows\\\\Start Menu"' in usf, usf[:600])
tpl_users = sorted({n.split("/")[3] for n in names_t if n.startswith("prefix/drive_c/users/") and n.count("/") >= 4})
with_menu = sorted({n.split("/")[3] for n in names_t
                    if n.startswith("prefix/drive_c/users/") and "/AppData/Roaming/Microsoft/Windows/Start Menu" in n})
check("the template has a user Start Menu only for its own user, not for the device's user (mobile)",
      "mobile" not in with_menu and with_menu, f"users {tpl_users}, with a Start Menu {with_menu}")
uf = bridge.index("[shell-dir] user folders for")
ublock = bridge[bridge.rindex("NSArray *rels = @[", 0, uf):uf]
check("the session makes the user's Start Menu\\Programs with the other profile folders",
      '@"AppData/Roaming/Microsoft/Windows/Start Menu/Programs"' in ublock)
check("an existing folder (or a link that resolves) is left alone",
      "if (!S_ISLNK(lst.st_mode) || stat(cp, &tgt) == 0) continue;" in ublock)
check("the log line says the Start Menu is part of it",
      '"[shell-dir] user folders for %s: %d created (Start Menu included)\\n"' in bridge)

# ---------------------------------------------------------------- 4. the project
pbx = (R / "app/Madeira.xcodeproj/project.pbxproj").read_text()
check("the Xcode project compiles BCDStartMenu.c and lists the header",
      "BCDStartMenu.c in Sources" in pbx and "BCDStartMenu.h" in pbx and pbx.count("BCDStartMenu.c") >= 4)
for f in ("BCDStartMenu.c", "BCDStartMenu.h"):
    head = (APP / f).read_text()[:400]
    check(f + " has the BCD licence header", "SPDX-License-Identifier: GPL-3.0-only" in head and "LICENSE-BCD.md" in head)
build_script = R / "tools/build-dx-tests.sh"
d3d12_script = (R / "tools/build-madeira-d3d12-dll.sh").read_text()
bs = build_script.read_text() if build_script.exists() else ""
check("tools/build-dx-tests.sh builds d3d9, d3d10 and d3d11 as <api>-cube-x64.exe", "for t in d3d9 d3d10 d3d11; do" in bs and '"$t-cube-x64.exe"' in bs)
for prog in ("d3d9-cube-x64.exe", "d3d10-cube-x64.exe", "d3d11-cube-x64.exe"):
    check(prog + " is tracked in arm64ec-windows, with its source in tests/dxtests",
          (APP / "arm64ec-windows" / prog).is_file() and (R / "tests/dxtests" / (prog.split("-")[0] + "_cube.c")).is_file())
check("d3d12-cube-x64.exe is built by tools/build-madeira-d3d12-dll.sh and tracked", "d3d12-cube-x64.exe" in d3d12_script
      and (APP / "arm64ec-windows/d3d12-cube-x64.exe").is_file())
check("explorer.exe is in the app's aarch64-windows folder", (APP / "aarch64-windows/explorer.exe").is_file())
wf = (R / ".github/workflows/build-ipa.yml").read_text()
check("the workflow runs this check", "tests/host/check-bcd-startmenu.py" in wf)

sys.exit(0 if ok else 1)

// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
//
//  BCDStartMenu.c
//
//  BCD's Start menu shortcuts (BCDStartMenu.h). The file layout, all integers
//  little-endian ([MS-SHLLINK]):
//
//    ShellLinkHeader  76 bytes: size 0x4C, the CLSID 00021401-0000-0000-C000-000000000046,
//                     LinkFlags, file attributes, three FILETIMEs, the target's size, the icon
//                     index, the show command, a hot key and three reserved fields.
//    LinkInfo         (HasLinkInfo) size, header size 0x1C, flags (1: VolumeIDAndLocalBasePath),
//                     the offsets of the VolumeID, the local base path, the network block (none)
//                     and the common path suffix; the VolumeID (size, drive type, serial, label
//                     offset, label); the base path and the suffix, null-ended ANSI strings.
//    StringData       the name, relative path, working folder, arguments and icon location that
//                     the flags announce, each a 16-bit character count and that many UTF-16
//                     characters (IsUnicode).
//    ExtraData        a terminal block: four zero bytes.
//
//  Wine's loader (shell32 shelllink.c) reads the header, an item ID list when the flags have one,
//  the LinkInfo, the strings, then one more 32-bit word that should be zero; it takes the target
//  from the LinkInfo's local path. BCD's links carry no item ID list and no relative path.

#include "BCDStartMenu.h"

#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <stdio.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

/* ---------------------------------------------------------------- the entries */

#define BCD_TESTS "Programs/BCD"

const bcd_start_entry bcd_start_entries[] = {
    { "",         "Explorer",        "explorer.exe",        "Opens Wine's file manager" },
    { BCD_TESTS,  "DirectX 9 test",  "d3d9-cube-x64.exe",   "A spinning cube drawn with Direct3D 9 (64-bit)" },
    { BCD_TESTS,  "DirectX 10 test", "d3d10-cube-x64.exe",  "A spinning cube drawn with Direct3D 10 (64-bit)" },
    { BCD_TESTS,  "DirectX 11 test", "d3d11-cube-x64.exe",  "A spinning cube drawn with Direct3D 11 (64-bit)" },
    { BCD_TESTS,  "DirectX 12 test", "d3d12-cube-x64.exe",  "A spinning cube drawn with Direct3D 12 (64-bit)" },
};
const size_t bcd_start_entry_count = sizeof(bcd_start_entries) / sizeof(bcd_start_entries[0]);

/* ---------------------------------------------------------------- bytes */

#define LNK_HEADER_SIZE 0x4C
#define LNK_MAX_TEXT    1000
#define SLDF_LINK_INFO  0x00000002u   /* HasLinkInfo */
#define SLDF_NAME       0x00000004u   /* HasName */
#define SLDF_WORKINGDIR 0x00000010u   /* HasWorkingDir */
#define SLDF_ARGS       0x00000020u   /* HasArguments */
#define SLDF_UNICODE    0x00000080u   /* IsUnicode */
#define SLDF_ID_LIST    0x00000001u   /* HasLinkTargetIDList */

/* CLSID_ShellLink {00021401-0000-0000-C000-000000000046}, as GUID bytes. */
static const uint8_t lnk_clsid[16] = { 0x01, 0x14, 0x02, 0x00, 0x00, 0x00, 0x00, 0x00,
                                       0xC0, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x46 };

static void put16(uint8_t *p, uint32_t v) { p[0] = (uint8_t)v; p[1] = (uint8_t)(v >> 8); }
static void put32(uint8_t *p, uint32_t v) { put16(p, v); put16(p + 2, v >> 16); }
static uint32_t get16(const uint8_t *p) { return (uint32_t)p[0] | ((uint32_t)p[1] << 8); }
static uint32_t get32(const uint8_t *p) { return get16(p) | (get16(p + 2) << 16); }

/* ASCII text of an acceptable length: 1 when it can go into a link. */
static int lnk_text_ok(const char *s)
{
    size_t n = 0;
    for (; s[n]; n++)
        if ((unsigned char)s[n] >= 0x80 || n >= LNK_MAX_TEXT) return 0;
    return 1;
}

/* A counted UTF-16 string at out[*pos]; 0 when it does not fit. */
static int lnk_put_string(uint8_t *out, size_t cap, size_t *pos, const char *prefix, const char *s)
{
    size_t n = strlen(prefix) + strlen(s), i, k = 0;
    if (*pos + 2 + 2 * n > cap) return 0;
    put16(out + *pos, (uint32_t)n);
    *pos += 2;
    for (i = 0; prefix[i]; i++, k++) put16(out + *pos + 2 * k, (uint8_t)prefix[i]);
    for (i = 0; s[i]; i++, k++) put16(out + *pos + 2 * k, (uint8_t)s[i]);
    *pos += 2 * n;
    return 1;
}

size_t bcd_lnk_build(const char *target, const char *arguments, const char *working_dir,
                     const char *description, uint8_t *out, size_t cap)
{
    size_t pos = 0, target_len, info_size, vol_size;
    uint32_t flags = SLDF_LINK_INFO | SLDF_NAME | SLDF_UNICODE;

    if (!target || !*target || !description || !out) return 0;
    if (!lnk_text_ok(target) || !lnk_text_ok(description)) return 0;
    if (arguments && (!lnk_text_ok(arguments))) return 0;
    if (working_dir && (!lnk_text_ok(working_dir))) return 0;
    if (arguments && *arguments) flags |= SLDF_ARGS;
    if (working_dir && *working_dir) flags |= SLDF_WORKINGDIR;

    target_len = strlen(target);
    vol_size = 0x10 + 1;                                  /* VolumeID: four fields and the empty label's null */
    info_size = 0x1C + vol_size + target_len + 1 + 1;     /* header, VolumeID, base path, empty common suffix */
    if (cap < LNK_HEADER_SIZE + info_size) return 0;
    memset(out, 0, LNK_HEADER_SIZE + info_size);          /* every gap, label and null below stays zero */

    /* ShellLinkHeader */
    put32(out + 0x00, LNK_HEADER_SIZE);
    memcpy(out + 0x04, lnk_clsid, 16);
    put32(out + 0x14, flags);
    put32(out + 0x18, 0x20);                              /* FILE_ATTRIBUTE_ARCHIVE */
    /* the three FILETIMEs (0x1C, 0x24, 0x2C) and the file size (0x34) stay zero: the target is not measured */
    put32(out + 0x38, 0);                                 /* icon index */
    put32(out + 0x3C, 1);                                 /* SW_SHOWNORMAL */
    pos = LNK_HEADER_SIZE;

    /* LinkInfo */
    put32(out + pos + 0x00, (uint32_t)info_size);
    put32(out + pos + 0x04, 0x1C);                        /* LinkInfoHeaderSize */
    put32(out + pos + 0x08, 1);                           /* VolumeIDAndLocalBasePath */
    put32(out + pos + 0x0C, 0x1C);                        /* VolumeIDOffset */
    put32(out + pos + 0x10, (uint32_t)(0x1C + vol_size)); /* LocalBasePathOffset */
    put32(out + pos + 0x14, 0);                           /* CommonNetworkRelativeLinkOffset: none */
    put32(out + pos + 0x18, (uint32_t)(0x1C + vol_size + target_len + 1)); /* CommonPathSuffixOffset */
    put32(out + pos + 0x1C + 0x00, (uint32_t)vol_size);   /* VolumeIDSize */
    put32(out + pos + 0x1C + 0x04, 3);                    /* DRIVE_FIXED */
    put32(out + pos + 0x1C + 0x08, 0);                    /* DriveSerialNumber */
    put32(out + pos + 0x1C + 0x0C, 0x10);                 /* VolumeLabelOffset */
    /* the label is the one zero byte already there */
    memcpy(out + pos + 0x1C + vol_size, target, target_len);   /* LocalBasePath, null-ended by the cleared buffer */
    pos += info_size;

    /* StringData */
    if (!lnk_put_string(out, cap, &pos, BCD_LNK_MARKER, description)) return 0;
    if ((flags & SLDF_WORKINGDIR) && !lnk_put_string(out, cap, &pos, "", working_dir)) return 0;
    if ((flags & SLDF_ARGS) && !lnk_put_string(out, cap, &pos, "", arguments)) return 0;

    /* ExtraData: the terminal block */
    if (pos + 4 > cap) return 0;
    put32(out + pos, 0);
    pos += 4;
    return pos;
}

int bcd_lnk_owner(const uint8_t *data, size_t len)
{
    size_t pos = LNK_HEADER_SIZE, i, n;
    uint32_t flags;
    const size_t mlen = strlen(BCD_LNK_MARKER);

    if (!data || len < LNK_HEADER_SIZE) return -1;
    if (get32(data) != LNK_HEADER_SIZE || memcmp(data + 4, lnk_clsid, 16) != 0) return -1;
    flags = get32(data + 0x14);
    if (flags & SLDF_ID_LIST) {
        if (pos + 2 > len) return -1;
        pos += 2 + get16(data + pos);
    }
    if (flags & SLDF_LINK_INFO) {
        if (pos + 4 > len) return -1;
        n = get32(data + pos);
        if (n < 4 || pos + n > len) return -1;
        pos += n;
    }
    if (!(flags & SLDF_NAME)) return 0;                   /* a link with no description is not BCD's */
    if (pos + 2 > len) return -1;
    n = get16(data + pos);
    pos += 2;
    if (flags & SLDF_UNICODE) {
        if (pos + 2 * n > len) return -1;
        if (n < mlen) return 0;
        for (i = 0; i < mlen; i++)
            if (get16(data + pos + 2 * i) != (uint8_t)BCD_LNK_MARKER[i]) return 0;
    } else {
        if (pos + n > len) return -1;
        if (n < mlen) return 0;
        if (memcmp(data + pos, BCD_LNK_MARKER, mlen) != 0) return 0;
    }
    return 1;
}

/* ---------------------------------------------------------------- the prefix */

#define STARTMENU_REL "drive_c/ProgramData/Microsoft/Windows/Start Menu"
#define LNK_FILE_MAX  4096

static int is_dir(const char *path)
{
    struct stat st;
    return stat(path, &st) == 0 && S_ISDIR(st.st_mode);
}

static int exists(const char *path)
{
    struct stat st;
    return stat(path, &st) == 0;
}

/* Every folder of `folder` below `base`, made when missing; 1 when the last one is there afterwards. */
static int make_folders(const char *base, const char *folder)
{
    char path[PATH_MAX];
    size_t len = strlen(base);
    const char *p = folder;

    if (len + 1 >= sizeof(path)) return 0;
    memcpy(path, base, len + 1);
    while (*p) {
        size_t part = strcspn(p, "/");
        if (part == 0) { p++; continue; }
        if (len + 1 + part >= sizeof(path)) return 0;
        path[len++] = '/';
        memcpy(path + len, p, part);
        len += part;
        path[len] = 0;
        if (!is_dir(path) && mkdir(path, 0755) != 0 && !is_dir(path)) return 0;
        p += part;
    }
    return is_dir(path);
}

/* The file's bytes (at most LNK_FILE_MAX + 1), or -1 when it is not a plain file one can read. */
static long read_plain(const char *path, uint8_t *buf)
{
    struct stat st;
    long total = 0;
    ssize_t got;
    int fd;

    if (lstat(path, &st) != 0 || !S_ISREG(st.st_mode)) return -1;
    fd = open(path, O_RDONLY);
    if (fd < 0) return -1;
    while (total < LNK_FILE_MAX + 1 && (got = read(fd, buf + total, (size_t)(LNK_FILE_MAX + 1 - total))) > 0) total += got;
    close(fd);
    return total;
}

static int write_file(const char *path, const uint8_t *data, size_t len)
{
    char tmp[PATH_MAX];
    size_t done = 0;
    int fd;

    if (snprintf(tmp, sizeof(tmp), "%s.bcdtmp", path) >= (int)sizeof(tmp)) return 0;
    fd = open(tmp, O_WRONLY | O_CREAT | O_TRUNC, 0644);
    if (fd < 0) return 0;
    while (done < len) {
        ssize_t n = write(fd, data + done, len - done);
        if (n <= 0) { close(fd); unlink(tmp); return 0; }
        done += (size_t)n;
    }
    close(fd);
    if (rename(tmp, path) != 0) { unlink(tmp); return 0; }
    return 1;
}

bcd_start_result bcd_startmenu_install(const char *prefix, const char *bundle)
{
    bcd_start_result r = { 0, 0, 0, 0, 0, 0 };
    char menu[PATH_MAX], probe[PATH_MAX], file[PATH_MAX], target[256];
    uint8_t built[LNK_FILE_MAX], current[LNK_FILE_MAX + 1];
    size_t i;

    if (!prefix || !bundle || snprintf(menu, sizeof(menu), "%s/" STARTMENU_REL, prefix) >= (int)sizeof(menu) || !is_dir(menu)) {
        r.no_menu = 1;
        return r;
    }
    for (i = 0; i < bcd_start_entry_count; i++) {
        const bcd_start_entry *e = &bcd_start_entries[i];
        size_t size;
        long have;

        /* a program the app does not carry gets no entry */
        if ((snprintf(probe, sizeof(probe), "%s/arm64ec-windows/%s", bundle, e->program) >= (int)sizeof(probe) || !exists(probe)) &&
            (snprintf(probe, sizeof(probe), "%s/aarch64-windows/%s", bundle, e->program) >= (int)sizeof(probe) || !exists(probe))) {
            r.missing++;
            continue;
        }
        if (snprintf(target, sizeof(target), "C:\\windows\\system32\\%s", e->program) >= (int)sizeof(target)) { r.failed++; continue; }
        size = bcd_lnk_build(target, NULL, NULL, e->description, built, sizeof(built));
        if (size == 0) { r.failed++; continue; }
        if (!make_folders(menu, e->folder)) { r.failed++; continue; }
        if (snprintf(file, sizeof(file), "%s/%s%s%s.lnk", menu, e->folder, *e->folder ? "/" : "", e->name) >= (int)sizeof(file)) {
            r.failed++;
            continue;
        }
        if (lstat(file, &(struct stat){ 0 }) == 0) {
            have = read_plain(file, current);
            if (have < 0 || have > LNK_FILE_MAX || bcd_lnk_owner(current, (size_t)have) != 1) { r.foreign++; continue; }
            if ((size_t)have == size && memcmp(current, built, size) == 0) { r.current++; continue; }
        }
        if (write_file(file, built, size)) r.written++; else r.failed++;
    }
    return r;
}

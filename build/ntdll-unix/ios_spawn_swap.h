/* env.MADEIRA_SPAWN_SWAP: a program that is about to start is replaced by another program of the same folder (OPT-IN, off unless set).
 *
 * SPDX-License-Identifier: GPL-3.0-only
 * Copyright 2026 thebcd69 (BCD)
 * BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
 *
 * WHY THIS EXISTS
 * ---------------
 * Fallout 4 (Steam 377160) through BCD Dock. Steam's launch configuration for the game is ONE entry, key 0, Fallout4Launcher.exe (type "none",
 * osarch 64; the product info says nothing else), and Valve's client starts exactly that. On this port the launcher does not get far: its 4.3 MB
 * 64-bit image has its relocations stripped, so it can only load at 0x140000000; it starts a second instance of itself 0.2 s later, and that one fails
 * with "wine: failed to create main module ... Fallout4Launcher.exe, status c0000018" (C050A4, build 580: "dock-game-end ... after 0 s", the session
 * ended 19 s after Play). Two players got the game running by swapping the file names Fallout4.exe and Fallout4Launcher.exe in the install folder
 * (CW3VZF, build 592: the program Valve's client starts is then the game, it ran 14 minutes). That swap is undone by Steam's file verification and
 * is not something a player should have to do.
 *
 * WHAT IT DOES
 * ------------
 * env.MADEIRA_SPAWN_SWAP = Fallout4Launcher.exe=Fallout4.exe (a list of <from.exe>=<to.exe> separated by ';'): when NtCreateUserProcess is asked to
 * start a program whose file name is <from.exe> (case-insensitive), it starts <to.exe> from the SAME folder instead, if that file exists there.
 * The image name (the NT path and the DOS path) and, when it begins with the old path, the command line's first word are rewritten; nothing on
 * disk changes, and the program that runs has its own name, as on Windows. If <to.exe> is not in the folder, or the names do not agree, the program
 * starts as asked. Without the variable (the default) none of this runs: one getenv per started program.
 *
 * The pure string work is here so tests/host/check-spawn-swap.py can compile it on the host; the one caller is process_ios.c (spawn_swap_apply).
 */
#ifndef IOS_SPAWN_SWAP_H
#define IOS_SPAWN_SWAP_H

#include <stddef.h>
#include <string.h>

#define SPAWN_SWAP_NAME_MAX 255

static WCHAR spawn_swap_lower( WCHAR c )
{
    return (c >= 'A' && c <= 'Z') ? (WCHAR)(c + ('a' - 'A')) : c;
}

/* A program file name of the list: printable ASCII and spaces, no path or list punctuation, no leading or trailing space, ".exe" at the end. */
static int spawn_swap_name_valid( const char *name, size_t len )
{
    size_t i;

    if (len < 5 || len > SPAWN_SWAP_NAME_MAX || name[0] == ' ' || name[len - 1] == ' ') return 0;
    for (i = 0; i < len; i++)
    {
        unsigned char c = (unsigned char)name[i];
        if (c < 32 || c > 126 || strchr( "<>:\"/\\|?*=;", c )) return 0;
    }
    return name[len - 4] == '.' && (name[len - 3] | 0x20) == 'e' && (name[len - 2] | 0x20) == 'x' && (name[len - 1] | 0x20) == 'e';
}

/* The two names are the same, ignoring the case of ASCII letters: `a` is the list's text, `b` a path's file name. */
static int spawn_swap_equal_c_w( const char *a, const WCHAR *b, size_t len )
{
    size_t i;

    for (i = 0; i < len; i++)
        if (spawn_swap_lower( (WCHAR)(unsigned char)a[i] ) != spawn_swap_lower( b[i] )) return 0;
    return 1;
}

static int spawn_swap_equal_w_w( const WCHAR *a, const WCHAR *b, size_t len )
{
    size_t i;

    for (i = 0; i < len; i++)
        if (spawn_swap_lower( a[i] ) != spawn_swap_lower( b[i] )) return 0;
    return 1;
}

/* Where the file name of a path starts: just after its last '\' or '/'. */
static size_t spawn_swap_base( const WCHAR *path, size_t len )
{
    while (len && path[len - 1] != '\\' && path[len - 1] != '/') len--;
    return len;
}

/* `name` (the file name of the program being started, `name_len` characters) looked up in `list`. Returns the replacement's text (not
 * NUL-terminated, `*to_len` characters), or NULL. An entry that is not <from.exe>=<to.exe> with two valid names is skipped, and so is one that
 * swaps a program for itself. */
static const char *spawn_swap_find( const char *list, const WCHAR *name, size_t name_len, size_t *to_len )
{
    const char *entry = list;

    while (entry && *entry)
    {
        const char *end = strchr( entry, ';' ), *equal, *from_end, *to, *to_end, *next;
        size_t from_len, len;

        if (!end) end = entry + strlen( entry );
        next = *end ? end + 1 : end;
        while (entry < end && (*entry == ' ' || *entry == '\t')) entry++;
        while (end > entry && (end[-1] == ' ' || end[-1] == '\t')) end--;
        equal = memchr( entry, '=', end - entry );
        if (equal)
        {
            from_end = equal;
            while (from_end > entry && (from_end[-1] == ' ' || from_end[-1] == '\t')) from_end--;
            to = equal + 1;
            while (to < end && (*to == ' ' || *to == '\t')) to++;
            to_end = end;
            from_len = from_end - entry;
            len = to_end - to;
            if (spawn_swap_name_valid( entry, from_len ) && spawn_swap_name_valid( to, len ) && from_len == name_len &&
                spawn_swap_equal_c_w( entry, name, name_len ) && !(len == from_len && spawn_swap_equal_c_w( to, name, name_len )))
            {
                *to_len = len;
                return to;
            }
        }
        entry = next;
    }
    return NULL;
}

/* `path` with its file name replaced by `to`, written to `out` (NUL-terminated). Returns the length in characters, or 0 when it does not fit. */
static size_t spawn_swap_path( const WCHAR *path, size_t len, const char *to, size_t to_len, WCHAR *out, size_t capacity )
{
    size_t base = spawn_swap_base( path, len ), i;

    if (base + to_len + 1 > capacity) return 0;
    memcpy( out, path, base * sizeof(WCHAR) );
    for (i = 0; i < to_len; i++) out[base + i] = (WCHAR)(unsigned char)to[i];
    out[base + to_len] = 0;
    return base + to_len;
}

/* The command line with the old program path replaced by the new one, when the line begins with it (after an opening quote, if any) and the path
 * is followed by the end, a closing quote or a blank (not by more of a longer name). Returns the new length, or 0 when the line does not begin
 * that way or does not fit; the caller then keeps the line as it is. */
static size_t spawn_swap_cmdline( const WCHAR *line, size_t len, const WCHAR *old_path, size_t old_len,
                                  const WCHAR *new_path, size_t new_len, WCHAR *out, size_t capacity )
{
    size_t start = (len && line[0] == '"') ? 1 : 0, total;

    if (!old_len || len < start + old_len || !spawn_swap_equal_w_w( line + start, old_path, old_len )) return 0;
    if (start + old_len < len)
    {
        WCHAR c = line[start + old_len];
        if (c != '"' && c != ' ' && c != '\t') return 0;
    }
    total = len - old_len + new_len;
    if (total + 1 > capacity) return 0;
    memcpy( out, line, start * sizeof(WCHAR) );
    memcpy( out + start, new_path, new_len * sizeof(WCHAR) );
    memcpy( out + start + new_len, line + start + old_len, (len - start - old_len) * sizeof(WCHAR) );
    out[total] = 0;
    return total;
}

#endif /* IOS_SPAWN_SWAP_H */

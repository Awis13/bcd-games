// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
//
//  BCDStartMenu.h
//
//  BCD's own entries in the Start menu of Wine's desktop (explorer.exe
//  /desktop=shell): an Explorer entry and one test program for each of
//  Direct3D 9, 10, 11 and 12. Wine's explorer builds the menu from the .lnk
//  files under C:\ProgramData\Microsoft\Windows\Start Menu (and the user's),
//  so the entries are shortcut files in the prefix.
//
//  A shortcut is written in the binary Shell Link format of Microsoft's
//  [MS-SHLLINK] specification: a header, a LinkInfo block with the target's
//  path, and the description, working folder and arguments as UTF-16 strings.
//  That is the part of the format Wine's shell32 (dlls/shell32/shelllink.c,
//  IPersistStream::Load) reads to start the program; a link with no item ID
//  list is valid, and Wine takes the target from the LinkInfo path.
//
//  The files are BCD's own and the session start keeps them so: each one's
//  description begins with BCD_LNK_MARKER. A file is written only when it is
//  missing, or when it carries the marker and its bytes are outdated; a file
//  without the marker (a person's own shortcut of the same name) is never
//  touched.

#ifndef BCD_STARTMENU_H
#define BCD_STARTMENU_H

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/* The start of every description BCD writes: how a later start knows a shortcut is BCD's own. */
#define BCD_LNK_MARKER "[BCD] "

/* A shortcut file as bytes. `target` is the program's Windows path ("C:\\windows\\system32\\explorer.exe");
 * `arguments` and `working_dir` may be NULL. The description is BCD_LNK_MARKER followed by `description`.
 * Every string must be plain ASCII and at most 1000 characters. Returns the size, or 0 when the text is
 * not acceptable or `cap` is too small (about 400 bytes plus the strings are needed). The bytes depend
 * only on the arguments, so the same call always gives the same file. */
size_t bcd_lnk_build(const char *target, const char *arguments, const char *working_dir,
                     const char *description, uint8_t *out, size_t cap);

/* Whether a shortcut file is BCD's own: 1 when it is a readable link whose description begins with
 * BCD_LNK_MARKER, 0 when it is a readable link that is not BCD's, -1 when it cannot be read as a link. */
int bcd_lnk_owner(const uint8_t *data, size_t len);

/* One entry of BCD's Start menu. */
typedef struct {
    const char *folder;       /* below the Start Menu folder, "/" between levels; "" is the top of the menu */
    const char *name;         /* what the menu shows; the file is "<name>.lnk" */
    const char *program;      /* a file of the app's arm64ec-windows or aarch64-windows folder (system32 in a session) */
    const char *description;  /* after BCD_LNK_MARKER */
} bcd_start_entry;

extern const bcd_start_entry bcd_start_entries[];
extern const size_t bcd_start_entry_count;

typedef struct {
    int written;   /* files made or brought up to date */
    int current;   /* BCD's files that were already as they should be */
    int foreign;   /* files without BCD's marker, left alone */
    int missing;   /* entries whose program the app does not carry */
    int failed;    /* entries that could not be written */
    int no_menu;   /* 1 when the prefix has no public Start Menu folder, so nothing was written */
} bcd_start_result;

/* Puts BCD's entries into the prefix's public Start Menu folder (<prefix>/drive_c/ProgramData/Microsoft/
 * Windows/Start Menu), which the prefix template provides; a prefix without it gets nothing. `bundle` is
 * the app's folder (its arm64ec-windows and aarch64-windows hold the programs). Only the entries' folders
 * below "Start Menu" are created. */
bcd_start_result bcd_startmenu_install(const char *prefix, const char *bundle);

#ifdef __cplusplus
}
#endif

#endif /* BCD_STARTMENU_H */

#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""[guest-log] keeps no line for the log-level tag "[DISPLAY]" alone (madeira-bcd; logging only).

The mirror (ios_guest_log_mirror in wine/dlls/ntdll/unix/file.c, carried by the Wine pin or added by
tools/patch-wine-guest-log.py) copies at most 64 lines of a game's own *.log into the session log, the ones
that mention error / fail / exception / ... / display. Rockstar Games Launcher's launcher.log writes
"[2026-10-20 13:41:47.261] [DISPLAY] [Main ] [launcher] Opening log file." -- [DISPLAY] is its log level,
on every line that is not an error -- so every line matched "display" and the first 64 lines of start-up
chatter used the whole budget: 19 of the 20 GTA V / RDR2 launcher sessions of 2026-10-20 (56KFE8, WEY0D5,
05RNCK, BS0146, ...) end the mirror at line 64 and WEY0D5, BS0146, D7Q84R and 5C8AFX never show the
launcher's own error text after it ("[ ERROR ] ... Steam is not initialized", LAUNCHER_ERR_STEAM_FAILED, in the
sessions where it came early enough to fit).

With the patch the literal tag "[display]" (any case) is blanked in the scratch copy that is searched, so only
the line's own words count; "Display adapter: ..." still matches. Nothing is written to the game and the
budget, the modes and MADEIRA_GUEST_LOG=all (every line) are untouched.

Usage: patch-wine-guest-log-tag.py [path/to/wine/dlls/ntdll/unix/file.c]. Idempotent; fails by name if the
mirror is not there or its keyword function moved.
"""
import sys

path = sys.argv[1] if len(sys.argv) > 1 else "wine/dlls/ntdll/unix/file.c"
src = open(path).read()
marker = "madeira-bcd: [guest-log] tag"
if marker in src:
    print("patch-wine-guest-log-tag: already patched")
    sys.exit(0)

old = '''    char lower[320];
    size_t i;

    if (len >= sizeof(lower)) len = sizeof(lower) - 1;
    for (i = 0; i < len; i++)
        lower[i] = line[i] >= 'A' && line[i] <= 'Z' ? line[i] + ('a' - 'A') : line[i];
    lower[len] = 0;
    for (i = 0; i < sizeof(words) / sizeof(words[0]); i++)
        if (strstr( lower, words[i] )) return 1;
    return 0;'''
new = '''    char lower[320], *tag;
    size_t i;

    if (len >= sizeof(lower)) len = sizeof(lower) - 1;
    for (i = 0; i < len; i++)
        lower[i] = line[i] >= 'A' && line[i] <= 'Z' ? line[i] + ('a' - 'A') : line[i];
    lower[len] = 0;
    /* ''' + marker + ''' (tools/patch-wine-guest-log-tag.py): Rockstar's launcher.log tags every
     * non-error line "[DISPLAY]", a log level; as the word "display" it used up the whole budget. */
    for (tag = lower; (tag = strstr( tag, "[display]" )); tag += 9) memset( tag, ' ', 9 );
    for (i = 0; i < sizeof(words) / sizeof(words[0]); i++)
        if (strstr( lower, words[i] )) return 1;
    return 0;'''
if src.count(old) != 1:
    sys.exit("patch-wine-guest-log-tag: ios_guest_log_keep anchor not found exactly once in %s "
             "(the mirror is missing or its keyword function moved)" % path)
open(path, "w").write(src.replace(old, new))
print("patch-wine-guest-log-tag: applied to " + path)

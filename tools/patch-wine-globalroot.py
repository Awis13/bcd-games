#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright 2026 le0-VV (the resolver change itself: thebcd69/bcd-games#16, submitted with a Signed-off-by under CONTRIBUTING.md)
# Copyright 2026 thebcd69 (BCD) (the changes below)
# Modified by BCD (thebcd69), 2026-10-24: ported into the tree behind the public repository (host check, build step, opt-in switch).
# Not BCD Material: LICENSE-BCD.md section 1 keeps the other author's header and licence on a file that mixes in their text.
r"""Resolve GLOBALROOT-wrapped DOS file paths in the iOS Wine file layer.

Based on thebcd69/bcd-games#16 by le0-VV (Minecraft Dungeons II).

A program that opens a file as \??\GLOBALROOT\??\C:\...\data.dat (or \DosDevices\GLOBALROOT\??\C:\...) got "path not found":
Wine's client-side resolver takes "GLOBALROOT" for the name of a DOS device, finds no dosdevices/globalroot, answers
STATUS_BAD_DEVICE_TYPE, and the server's object manager cannot translate the DOS drive that follows. Windows resolves such a
name, because GLOBALROOT is a link to the root of the object manager's namespace.

Here every complete \??\GLOBALROOT\ (or \DosDevices\GLOBALROOT\) wrapper in front of another DOS prefix is skipped by
get_dos_prefix_len, which then returns the offset of the inner DOS path in the ORIGINAL name, so the drive, component and
reparse code behind it gets the same input as for a plain \??\C:\... name. Every other name keeps the old answer, value for
value: a name without a wrapper, a lookalike (GLOBALROOTx, GLOBALROOT/), a bare \??\GLOBALROOT, and a wrapper with no DOS prefix
behind it (\??\GLOBALROOT\Device\Null: the old code's 4, which ends in STATUS_BAD_DEVICE_TYPE and the object manager as before).
The change is compiled for iOS only (WINE_IOS); no other build of this file differs.

Applied before ntdll-unix is compiled (the "Check and patch GLOBALROOT DOS paths" step of build-ipa.yml); the submodule pin
stays unchanged. Marked as modified by BCD, 2026-10-24.
Usage: patch-wine-globalroot.py [wine/dlls/ntdll/unix/file.c]
"""
from pathlib import Path
import sys

MARKER = "madeira-bcd: GLOBALROOT DOS paths"
OLD = r'''static inline int get_dos_prefix_len( const UNICODE_STRING *name )
{
    static const WCHAR dosdev_prefixW[] = {'\\','D','o','s','D','e','v','i','c','e','s','\\'};

    if (name->Length >= sizeof(nt_prefixW) &&
        !memcmp( name->Buffer, nt_prefixW, sizeof(nt_prefixW) ))
        return ARRAY_SIZE( nt_prefixW );

    if (name->Length >= sizeof(dosdev_prefixW) &&
        !wcsnicmp( name->Buffer, dosdev_prefixW, ARRAY_SIZE( dosdev_prefixW )))
        return ARRAY_SIZE( dosdev_prefixW );

    return 0;
}'''
NEW = r'''static inline int get_dos_prefix_len( const UNICODE_STRING *name )
{
    static const WCHAR dosdev_prefixW[] = {'\\','D','o','s','D','e','v','i','c','e','s','\\'};
#ifdef WINE_IOS
    /* madeira-bcd: GLOBALROOT DOS paths (tools/patch-wine-globalroot.py), modified 2026-10-24. */
    static const WCHAR globalrootW[] = {'G','L','O','B','A','L','R','O','O','T','\\'};
    unsigned int first = 0;
#endif
    const WCHAR *buffer = name->Buffer;
    unsigned int len = name->Length / sizeof(WCHAR), pos = 0, prefix_len;

    for (;;)
    {
        if (len >= ARRAY_SIZE(nt_prefixW) && !memcmp( buffer, nt_prefixW, sizeof(nt_prefixW) ))
            prefix_len = ARRAY_SIZE(nt_prefixW);
        else if (len >= ARRAY_SIZE(dosdev_prefixW) &&
                 !wcsnicmp( buffer, dosdev_prefixW, ARRAY_SIZE(dosdev_prefixW) ))
            prefix_len = ARRAY_SIZE(dosdev_prefixW);
        else
#ifdef WINE_IOS
            return pos ? first : 0;  /* no DOS path behind the wrapper: the answer the old code gave */
#else
            return 0;
#endif

#ifdef WINE_IOS
        if (len >= prefix_len + ARRAY_SIZE(globalrootW) &&
            !wcsnicmp( buffer + prefix_len, globalrootW, ARRAY_SIZE(globalrootW) ))
        {
            if (!pos) first = prefix_len;  /* the old answer for this name */
            /* Keep the backslash: GLOBALROOT targets the object manager root. */
            prefix_len += ARRAY_SIZE(globalrootW) - 1;
            buffer += prefix_len;
            len -= prefix_len;
            pos += prefix_len;
            continue;
        }
#endif
        return pos + prefix_len;
    }
}'''


def patch(source):
    if MARKER in source:
        return source
    if source.count(OLD) != 1:
        raise ValueError("GLOBALROOT patch anchor missing or ambiguous")
    return source.replace(OLD, NEW)


def main():
    path = Path(sys.argv[1] if len(sys.argv) > 1 else "wine/dlls/ntdll/unix/file.c")
    source = path.read_text()
    try:
        result = patch(source)
    except ValueError as error:
        sys.exit(str(error))
    if result == source:
        print("GLOBALROOT DOS paths already patched")
    else:
        path.write_text(result)
        print("patched GLOBALROOT DOS paths")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""Wine's setupapi: a lock around the device node table (dlls/setupapi/devinst.c).

Dying Light (Steam 239140, build 576, iPhone18,2, log 1c807435) ends with an access violation in
setupapi.dll+0x132bc, inside create_device (get_devinst_for_device_id inlined). The controller
library libpad.dll (thread 00fc) lists HID devices and registers the first one (the [av-args] line's RSI
is "HID\\VID_845E&PID_0001\\0&0000&0&0&0"), while the main thread loads dinput8.dll. Wine's
alloc_devinst_for_device_id has no lock and publishes the table's size before the table:

    devinst_table_size = 256;
    devinst_table = calloc(devinst_table_size, sizeof(*devinst_table));

A second thread that runs get_devinst_for_device_id between the two statements sees 256 entries in a
NULL table and reads devinst_table[0]: the NULL read at the fault. The growth path (realloc, then
size *= 2) is as unguarded, and so are the readers (get_devnode_device, CM_Get_Device_IDA/IDW,
CM_Get_Device_ID_Size, CM_Locate_DevNode_ExW), which index the table without a lock.

The patch (all in devinst.c; the file's other code is untouched):
  - one SRWLOCK, devinst_lock, guards devinst_table and devinst_table_size;
  - alloc_devinst_for_device_id takes it for the whole lookup-grow-store, and allocates the new table
    BEFORE it publishes the new size (an allocation failure leaves both as they were);
  - every reader asks get_devnode_id / locate_devnode, which look up under the lock. The id strings are
    written once and never freed, so a string stays valid after the lock is dropped even if a growth
    moves the table; the reader calls (SetupDiOpenDeviceInfoW, lstrcpynW ...) run outside the lock.

The patched DLL ships as arm64ec-windows/setupapi-optin.dll NEXT TO the untouched setupapi.dll; only
a game whose settings have env.MADEIRA_OPTIN_DLLS = setupapi gets it (app/Madeira/WineProcessBridge.m
links it as setupapi.dll in sysx64, and in system32 for an ARM64EC session, and the next session's
farm relink restores the shipped one). Every other game keeps the shipped file, byte for byte.

Wine's setupapi is LGPL 2.1 or later; the patched file carries a "Modified by BCD" notice, and this
script is the source of the change.

Usage:
  patch-wine-setupapi-devinst-lock.py WINE_SRC               patch the tree (idempotent: a second run
                                                             changes nothing)
  patch-wine-setupapi-devinst-lock.py --verify DLL SHIPPED   check a built DLL: it carries the marker,
      is the same machine type, and exports exactly the shipped setupapi.dll's names
tools/build-wine-extra-dlls.sh applies it around its setupapi build and restores the tree afterwards.
"""
import os
import struct
import sys

MARKER = "setupapi-devinst-lock-v1 madeira-bcd"
FILE = "dlls/setupapi/devinst.c"

NOTICE_ANCHOR = "#include <assert.h>\n#include <stdarg.h>\n"
NOTICE = '''/*
 * Modified by BCD, 2026-10-21 (tools/patch-wine-setupapi-devinst-lock.py): the table of device
 * nodes (see devinst_lock) is guarded by a lock, and a new table is allocated before its size is
 * published. Everything else in this file is as Wine's; this file stays under the license above.
 */

'''

TABLE_OLD = '''static WCHAR **devinst_table;
static unsigned int devinst_table_size;

static DEVINST get_devinst_for_device_id(const WCHAR *id)
{
'''
TABLE_NEW = '''static WCHAR **devinst_table;
static unsigned int devinst_table_size;

/* Modified by BCD: devinst_lock guards devinst_table and devinst_table_size. Wine's code read and wrote
 * them with no lock, and published the size before the table was allocated; two threads that listed
 * devices at the same moment (Dying Light's libpad.dll and the game's main thread) made one of them read
 * a NULL table. Entries are written once and never freed, so an id string stays valid after the lock is
 * dropped even when a growth moves the table. */
static SRWLOCK devinst_lock = SRWLOCK_INIT;
static const char devinst_lock_tag[] __attribute__((used)) = "setupapi-devinst-lock-v1 madeira-bcd";

/* Called with devinst_lock held. */
static DEVINST get_devinst_for_device_id(const WCHAR *id)
{
'''

ALLOC_OLD = '''static DEVINST alloc_devinst_for_device_id(const WCHAR *id)
{
    DEVINST ret;

    ret = get_devinst_for_device_id(id);
    if (ret == devinst_table_size)
    {
        if (devinst_table)
        {
            devinst_table = realloc(devinst_table, devinst_table_size * 2 * sizeof(*devinst_table));
            memset(devinst_table + devinst_table_size, 0, devinst_table_size * sizeof(*devinst_table));
            devinst_table_size *= 2;
        }
        else
        {
            devinst_table_size = 256;
            devinst_table = calloc(devinst_table_size, sizeof(*devinst_table));
        }
    }
    if (!devinst_table[ret])
        devinst_table[ret] = wcsdup(id);
    return ret;
}
'''
ALLOC_NEW = '''static DEVINST alloc_devinst_for_device_id(const WCHAR *id)
{
    DEVINST ret;

    AcquireSRWLockExclusive(&devinst_lock);
    ret = get_devinst_for_device_id(id);
    if (ret == devinst_table_size)
    {
        unsigned int size = devinst_table_size ? devinst_table_size * 2 : 256;
        WCHAR **table;

        if (devinst_table)
        {
            if ((table = realloc(devinst_table, size * sizeof(*table))))
                memset(table + devinst_table_size, 0, (size - devinst_table_size) * sizeof(*table));
        }
        else
            table = calloc(size, sizeof(*table));
        if (!table)
        {
            /* Out of memory: nothing was changed. -1 is no device node (every reader refuses a
             * node at or past the size), where Wine's code dereferenced the NULL result. */
            ReleaseSRWLockExclusive(&devinst_lock);
            return -1;
        }
        TRACE("%s: device node table %p, %u entries\\n", devinst_lock_tag, table, size);
        devinst_table = table;       /* the table first, then the size that says how big it is */
        devinst_table_size = size;
    }
    if (!devinst_table[ret])
        devinst_table[ret] = wcsdup(id);
    ReleaseSRWLockExclusive(&devinst_lock);
    return ret;
}

/* The id string of a device node, or NULL when there is none. The string stays valid after the lock is
 * dropped (see devinst_lock). */
static const WCHAR *get_devnode_id(DEVINST devnode)
{
    const WCHAR *id = NULL;

    AcquireSRWLockExclusive(&devinst_lock);
    if (devnode < devinst_table_size) id = devinst_table[devnode];
    ReleaseSRWLockExclusive(&devinst_lock);
    return id;
}

/* The device node of an id that is in the table. */
static BOOL locate_devnode(const WCHAR *id, DEVINST *devnode)
{
    DEVINST ret;
    BOOL found;

    AcquireSRWLockExclusive(&devinst_lock);
    ret = get_devinst_for_device_id(id);
    found = ret < devinst_table_size && devinst_table[ret];
    ReleaseSRWLockExclusive(&devinst_lock);
    if (found) *devnode = ret;
    return found;
}
'''

# (old, new) pairs, each old text exactly once in devinst.c.
EDITS = (
    (NOTICE_ANCHOR, NOTICE + NOTICE_ANCHOR),
    (TABLE_OLD, TABLE_NEW),
    (ALLOC_OLD, ALLOC_NEW),
    # get_devnode_device
    ('''static struct device *get_devnode_device(DEVINST devnode, HDEVINFO *set, SP_DEVINFO_DATA *data)
{
    data->cbSize = sizeof(*data);
    *set = NULL;
    if (devnode >= devinst_table_size || !devinst_table[devnode])
    {
        WARN("device node %lu not found\\n", devnode);
        return NULL;
    }

    *set = SetupDiCreateDeviceInfoListExW(NULL, NULL, NULL, NULL);
    if (*set == INVALID_HANDLE_VALUE) return NULL;
    if (!SetupDiOpenDeviceInfoW(*set, devinst_table[devnode], NULL, 0, data))
''', '''static struct device *get_devnode_device(DEVINST devnode, HDEVINFO *set, SP_DEVINFO_DATA *data)
{
    const WCHAR *id;

    data->cbSize = sizeof(*data);
    *set = NULL;
    if (!(id = get_devnode_id(devnode)))
    {
        WARN("device node %lu not found\\n", devnode);
        return NULL;
    }

    *set = SetupDiCreateDeviceInfoListExW(NULL, NULL, NULL, NULL);
    if (*set == INVALID_HANDLE_VALUE) return NULL;
    if (!SetupDiOpenDeviceInfoW(*set, id, NULL, 0, data))
'''),
    # CM_Get_Device_IDA
    ('''CONFIGRET WINAPI CM_Get_Device_IDA(DEVINST devnode, char *buffer, ULONG len, ULONG flags)
{
    TRACE("%lu, %p, %lu, %#lx\\n", devnode, buffer, len, flags);

    if (devnode >= devinst_table_size || !devinst_table[devnode])
        return CR_NO_SUCH_DEVINST;

    WideCharToMultiByte(CP_ACP, 0, devinst_table[devnode], -1, buffer, len, 0, 0);
''', '''CONFIGRET WINAPI CM_Get_Device_IDA(DEVINST devnode, char *buffer, ULONG len, ULONG flags)
{
    const WCHAR *id;

    TRACE("%lu, %p, %lu, %#lx\\n", devnode, buffer, len, flags);

    if (!(id = get_devnode_id(devnode)))
        return CR_NO_SUCH_DEVINST;

    WideCharToMultiByte(CP_ACP, 0, id, -1, buffer, len, 0, 0);
'''),
    # CM_Get_Device_IDW
    ('''CONFIGRET WINAPI CM_Get_Device_IDW(DEVINST devnode, WCHAR *buffer, ULONG len, ULONG flags)
{
    TRACE("%lu, %p, %lu, %#lx\\n", devnode, buffer, len, flags);

    if (devnode >= devinst_table_size || !devinst_table[devnode])
        return CR_NO_SUCH_DEVINST;

    lstrcpynW(buffer, devinst_table[devnode], len);
''', '''CONFIGRET WINAPI CM_Get_Device_IDW(DEVINST devnode, WCHAR *buffer, ULONG len, ULONG flags)
{
    const WCHAR *id;

    TRACE("%lu, %p, %lu, %#lx\\n", devnode, buffer, len, flags);

    if (!(id = get_devnode_id(devnode)))
        return CR_NO_SUCH_DEVINST;

    lstrcpynW(buffer, id, len);
'''),
    # CM_Get_Device_ID_Size
    ('''CONFIGRET WINAPI CM_Get_Device_ID_Size(ULONG *len, DEVINST devnode, ULONG flags)
{
    TRACE("%p, %lu, %#lx\\n", len, devnode, flags);

    if (devnode >= devinst_table_size || !devinst_table[devnode])
        return CR_NO_SUCH_DEVINST;

    *len = lstrlenW(devinst_table[devnode]);
''', '''CONFIGRET WINAPI CM_Get_Device_ID_Size(ULONG *len, DEVINST devnode, ULONG flags)
{
    const WCHAR *id;

    TRACE("%p, %lu, %#lx\\n", len, devnode, flags);

    if (!(id = get_devnode_id(devnode)))
        return CR_NO_SUCH_DEVINST;

    *len = lstrlenW(id);
'''),
    # CM_Locate_DevNode_ExW
    ('''    if ((ret = get_devinst_for_device_id(device_id)) < devinst_table_size && devinst_table[ret])
    {
        *devinst = ret;
        return CR_SUCCESS;
    }
''', '''    if (locate_devnode(device_id, &ret))
    {
        *devinst = ret;
        return CR_SUCCESS;
    }
'''),
)

# Nothing may touch the table outside the helpers above: after the edits, the table's names appear only
# between the declaration and the end of locate_devnode, where every access holds devinst_lock.
GUARDED_FROM = "static WCHAR **devinst_table;\n"
GUARDED_TO = "    if (found) *devnode = ret;\n    return found;\n}\n"


def patch(wine):
    path = os.path.join(wine, FILE)
    if not os.path.isfile(path):
        sys.exit("patch-wine-setupapi-devinst-lock: no " + path)
    text = open(path).read()
    if MARKER in text:
        print("already patched")
        return
    if "free(devinst_table" in text:
        sys.exit("patch-wine-setupapi-devinst-lock: Wine now frees devinst_table or its entries; the lock-free "
                 "string use after get_devnode_id is no longer safe. Read " + path)
    for old, new in EDITS:
        if text.count(old) != 1:
            sys.exit("patch-wine-setupapi-devinst-lock: anchor found %d times in %s:\n%s"
                     % (text.count(old), FILE, old[:400]))
        text = text.replace(old, new)
    start = text.index(GUARDED_FROM)
    end = text.index(GUARDED_TO, start) + len(GUARDED_TO)
    if "devinst_table" in text[:start] + text[end:]:
        sys.exit("patch-wine-setupapi-devinst-lock: after the edits the table is still used outside the "
                 "guarded helpers: Wine reads it somewhere the patch does not cover (%s)" % FILE)
    open(path, "w").write(text)
    print("patched %s (%d edits)" % (path, len(EDITS)))


def pe_exports(path):
    d = open(path, "rb").read()
    if d[:2] != b"MZ":
        sys.exit("verify: %s is not a PE image" % path)
    pe = struct.unpack_from("<I", d, 0x3c)[0]
    machine, nsec = struct.unpack_from("<HH", d, pe + 4)
    optsz = struct.unpack_from("<H", d, pe + 20)[0]
    opt = pe + 24
    secs = []
    for i in range(nsec):
        s = opt + optsz + 40 * i
        vsz, va, rsz, raw = struct.unpack_from("<IIII", d, s + 8)
        secs.append((va, max(vsz, rsz), raw))

    def off(rva):
        for va, size, raw in secs:
            if va <= rva < va + size:
                return rva - va + raw
        raise ValueError("rva %#x outside the sections" % rva)

    exp_rva = struct.unpack_from("<I", d, opt + 112)[0]
    e = off(exp_rva)
    nnames, anames = struct.unpack_from("<I", d, e + 24)[0], struct.unpack_from("<I", d, e + 32)[0]
    names = set()
    for i in range(nnames):
        no = off(struct.unpack_from("<I", d, off(anames) + 4 * i)[0])
        names.add(d[no:d.index(b"\0", no)].decode("latin-1"))
    return d, machine, names


def verify(path, shipped):
    d, machine, names = pe_exports(path)
    _, smachine, snames = pe_exports(shipped)
    bad = []
    if MARKER.encode() not in d:
        bad.append("no '%s' string: not built from the patched tree" % MARKER)
    if machine != smachine:
        bad.append("machine %#x, the shipped setupapi.dll's is %#x" % (machine, smachine))
    if names != snames:
        bad.append("exports differ from the shipped setupapi.dll: only here %s, only there %s"
                   % (sorted(names - snames)[:8], sorted(snames - names)[:8]))
    if bad:
        sys.exit("verify %s:\n  %s" % (path, "\n  ".join(bad)))
    print("verify %s: device node lock present, the %d exports of %s" % (path, len(names), shipped))


if __name__ == "__main__":
    if len(sys.argv) == 4 and sys.argv[1] == "--verify":
        verify(sys.argv[2], sys.argv[3])
    elif len(sys.argv) == 2:
        patch(sys.argv[1])
    else:
        sys.exit(__doc__)

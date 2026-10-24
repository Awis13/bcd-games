#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright 2026 le0-VV (the resolver harness: thebcd69/bcd-games#16, submitted with a Signed-off-by under CONTRIBUTING.md)
# Copyright 2026 thebcd69 (BCD) (the changes below)
# Modified by BCD (thebcd69), 2026-10-24: ported into the tree behind the public repository (host check, build step, opt-in switch).
# Not BCD Material: LICENSE-BCD.md section 1 keeps the other author's header and licence on a file that mixes in their text.
r"""Wine's real DOS-prefix and drive resolver on \??\GLOBALROOT\??\C:\... paths (tools/patch-wine-globalroot.py).

Based on thebcd69/bcd-games#16 by le0-VV. The harness compiles the REAL get_dos_prefix_len and nt_to_unix_file_name_no_root
out of the pinned wine/dlls/ntdll/unix/file.c (before and after the patch, with and without WINE_IOS) with AddressSanitizer and
UBSan; only the final component lookup and the DOS device lookup are replaced by stand-ins. No Wine process runs and nothing
tests the object manager or a game.

What it shows, and why the change is safe as a default:
  * a name that does not carry \GLOBALROOT\ right behind a DOS prefix AND another DOS prefix behind that wrapper gets the SAME
    answer from the patched get_dos_prefix_len as from the old one, value for value (an enumeration of every sequence of up to
    4 name pieces, and every truncation of a long wrapped name, each in a buffer of exactly its length);
  * a name that does carry the wrapper and a DOS path after it failed before (STATUS_BAD_DEVICE_TYPE from the client, then the
    server finds no \??\C: in its object manager) and now opens the same inode as the plain \??\C:\... name, with the original
    offsets, options and disposition forwarded;
  * \??\GLOBALROOT\Device\..., GLOBALROOTx, a bare \??\GLOBALROOT and \Device\... keep the old answer (the object manager);
  * without WINE_IOS the patched file answers exactly like the old one;
  * the patch applies after the other file.c patches of build-ipa.yml (and before them), twice in a row, and refuses a file
    whose anchor has moved, so a drifted pin stops the build instead of silently losing the fix.
"""
from pathlib import Path
import importlib.util
import os
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
WINE = Path(os.environ.get('WINE_SRC', ROOT / 'wine'))
CI = (ROOT / '.github/workflows/build-ipa.yml').read_text()
spec = importlib.util.spec_from_file_location('globalroot_patch', ROOT / 'tools/patch-wine-globalroot.py')
patcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(patcher)
source = (WINE / 'dlls/ntdll/unix/file.c').read_text()
assert patcher.MARKER not in source, 'run this check against an unpatched wine tree'


def ok(text):
    print('PASS: ' + text)


# ---------------------------------------------------------------- the patch itself
patched = patcher.patch(source)
assert patched != source and patcher.patch(patched) == patched
ok('the patch changes the pinned file once and is idempotent')
baseline = patched.replace(patcher.NEW, patcher.OLD)
assert baseline == source, 'OLD is not the pinned function'
for drift in (source.replace(patcher.OLD, '', 1), source + '\n' + patcher.OLD):
    try:
        patcher.patch(drift)
    except ValueError:
        pass
    else:
        raise AssertionError('missing or ambiguous anchors must fail before writing')
ok('a missing or doubled anchor is refused (a drifted pin stops the build)')

# The patch changes get_dos_prefix_len only; its single caller is the resolver.
assert source.count('get_dos_prefix_len( attr->ObjectName )') == 1 and source.count('get_dos_prefix_len(') == 2


def function(text, signature):
    start = text.index(signature)
    return text[start:text.index('\n}\n', start) + 2]


prefix_signature = 'static inline int get_dos_prefix_len('
resolver_signature = 'static NTSTATUS nt_to_unix_file_name_no_root('
assert function(patched, resolver_signature) == function(source, resolver_signature)
ok('nt_to_unix_file_name_no_root is untouched; only get_dos_prefix_len differs')

# ---------------------------------------------------------------- the patch in the build's order of patches
step_names = re.findall(r'python3 (tools/patch-wine-[a-z0-9-]+\.py) wine/dlls/ntdll/unix/file\.c', CI)   # in the build's order
chain = [name for name in dict.fromkeys(step_names) if name != 'tools/patch-wine-globalroot.py']
assert chain, 'no other file.c patch found in build-ipa.yml'
with tempfile.TemporaryDirectory(prefix='bcd-globalroot-chain-') as chain_dir:
    for label, order in (('after', chain + ['tools/patch-wine-globalroot.py']), ('before', ['tools/patch-wine-globalroot.py'] + chain)):
        target = Path(chain_dir) / (label + '-file.c')
        target.write_text(source)
        for tool in order:
            run = subprocess.run([sys.executable, str(ROOT / tool), str(target)], capture_output=True, text=True)
            assert run.returncode == 0, label + ': ' + tool + ' failed: ' + run.stdout + run.stderr
        final = target.read_text()
        assert patcher.MARKER in final and function(final, prefix_signature) == function(patched, prefix_signature), label
ok('the patch applies %s the %d other file.c patches of build-ipa.yml' % ('before and after', len(chain)))

ci_check = CI.index('python3 tests/host/check-globalroot.py')
ci_patch = CI.index('python3 tools/patch-wine-globalroot.py wine/dlls/ntdll/unix/file.c')
assert ci_check < ci_patch < CI.index('run: bash build/ntdll-unix/build.sh')
ok('build-ipa.yml checks and patches before ntdll-unix is compiled')

# ---------------------------------------------------------------- the compiled functions
harness = r'''
#include <assert.h>
#include <ctype.h>
#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
#include <sys/stat.h>
#include <unistd.h>
#include "ntstatus.h"
#include "windef.h"
#include "winternl.h"
#define ARRAY_SIZE(a) (sizeof(a) / sizeof((a)[0]))
#define MAX_DIR_ENTRY_LEN 255
#define INVALID_NT_CHARS '*','?','<','>','|','"'
static const char *config_dir = ".";
static const WCHAR nt_prefixW[] = {'\\','?','?','\\'};
static UINT expected_disposition;
static BOOL expected_reparse;
static unsigned int expected_reparse_count;
static const char *relative_file;
static int lookup_calls;

/* These adapters cover ASCII names; Wine's WCHAR stays 16 bits wide. */
static int ascii_wcsnicmp(const WCHAR *a, const WCHAR *b, size_t n)
{
    for (size_t i = 0; i < n; i++)
    {
        if (a[i] != b[i])
        {
            if (a[i] > 127 || b[i] > 127) return 1;
            if (tolower(a[i]) != tolower(b[i])) return 1;
        }
        if (!a[i]) break;
    }
    return 0;
}
static const WCHAR *short_wcschr(const WCHAR *s, WCHAR c)
{
    do { if (*s == c) return s; } while (*s++);
    return NULL;
}
#define wcsnicmp ascii_wcsnicmp
#define wcschr short_wcschr
static int ntdll_wcstoumbs(const WCHAR *src, unsigned int len, char *dst,
                          unsigned int capacity, BOOL strict)
{
    (void)strict;
    if (len > capacity) return -1;
    for (unsigned int i = 0; i < len; i++)
    {
        assert(src[i] < 128);
        dst[i] = src[i];
    }
    return len;
}
static NTSTATUS get_dos_device(char **name, int pos)
{
    (void)pos;
    free(*name); *name = NULL;
    return STATUS_BAD_DEVICE_TYPE;
}
/* The component lookup: the rest of the name, relative to the drive, must be exactly the file asked for. */
static NTSTATUS lookup_unix_name(int root_fd, OBJECT_ATTRIBUTES *attr, UNICODE_STRING *nt,
                                unsigned int nt_pos, char **buffer, int capacity, int pos,
                                UINT disposition, BOOL reparse, BOOL is_unix, unsigned int count)
{
    (void)nt;
    lookup_calls++;
    assert(root_fd == AT_FDCWD && !is_unix);
    assert(disposition == expected_disposition && reparse == expected_reparse);
    assert(count == expected_reparse_count && relative_file);
    unsigned int len = attr->ObjectName->Length / sizeof(WCHAR);
    assert(nt_pos <= len && len - nt_pos == strlen(relative_file));
    assert(pos + 1 + (int)strlen(relative_file) < capacity);
    char *path = *buffer;
    path[pos++] = '/';
    for (unsigned int i = nt_pos; i < len; i++)
    {
        WCHAR c = attr->ObjectName->Buffer[i];
        assert(c < 128);
        if (c == '\\') c = '/';
        assert(c == relative_file[i - nt_pos]);
        path[pos++] = c;
    }
    path[pos] = 0;
    struct stat st;
    if (stat(path, &st)) return STATUS_OBJECT_NAME_NOT_FOUND;
    return disposition == FILE_CREATE ? STATUS_OBJECT_NAME_COLLISION : STATUS_SUCCESS;
}
@FUNCTIONS@

/* A name of exactly n characters: the input is deliberately not NUL-terminated, so ASan sees every over-read. */
static UNICODE_STRING wide_n(const char *s, size_t n)
{
    assert(n <= 32767);
    WCHAR *buffer = malloc(n * sizeof(WCHAR));
    assert(buffer || !n);
    for (size_t i = 0; i < n; i++) buffer[i] = (unsigned char)s[i];
    return (UNICODE_STRING){n * sizeof(WCHAR), n * sizeof(WCHAR), buffer};
}
static int resolve(const char *request, NTSTATUS expected, const char *file,
                   UINT disposition, BOOL reparse, unsigned int count)
{
    UNICODE_STRING name = wide_n(request, strlen(request)), nt = {0};
    OBJECT_ATTRIBUTES attr = {0};
    attr.Length = sizeof(attr); attr.ObjectName = &name;
    attr.Attributes = OBJ_CASE_INSENSITIVE;
    OBJECT_ATTRIBUTES saved = attr;
    char *unix_name = NULL;
    expected_disposition = disposition; expected_reparse = reparse;
    expected_reparse_count = count; relative_file = file;
    int before = lookup_calls;
    NTSTATUS status = nt_to_unix_file_name_no_root(&attr, &nt, &unix_name, disposition, reparse, count);
    if (status != expected)
    {
        fprintf(stderr, "GLOBALROOT resolver: %s returned %08x, expected %08x\n",
                request, (unsigned int)status, (unsigned int)expected);
        free(unix_name); free(name.Buffer); free(nt.Buffer);
        return 1;
    }
    assert(!memcmp(&attr, &saved, sizeof(attr)) && !nt.Buffer);     /* the caller's attributes are not touched */
    for (size_t i = 0; i < strlen(request); i++) assert(name.Buffer[i] == (unsigned char)request[i]);
    if (!status)
    {
        struct stat actual, expected_stat;
        assert(lookup_calls == before + 1 && unix_name && file);
        assert(!stat(unix_name, &actual) && !stat(file, &expected_stat));
        assert(actual.st_dev == expected_stat.st_dev && actual.st_ino == expected_stat.st_ino);
        FILE *f = fopen(unix_name, "rb"); assert(f);
        char bytes[16] = {0}; assert(fread(bytes, 1, sizeof(bytes) - 1, f) == 7);
        assert(!strcmp(bytes, "fixture") && !fclose(f));
    }
    else assert(!unix_name);
    free(name.Buffer);
    return 0;
}
static void prefix(const char *text, int expected)
{
    UNICODE_STRING name = wide_n(text, strlen(text));
    assert(get_dos_prefix_len(&name) == expected);
    free(name.Buffer);
}
int main(void)
{
    _Static_assert(sizeof(WCHAR) == 2, "Windows UTF-16 ABI");
    const char *base = "Program Files (x86)/Steam/steamapps/common/Example Game/Game/Binaries/Win64/";
    const char *names[] = {"Data-Shipping.dat", "Game-Win64-Shipping.exe"};
    const char *wrappers[] = {"\\??\\", "\\DosDevices\\", "\\??\\GLOBALROOT\\??\\",
        "\\??\\gLoBaLrOoT\\dOsDeViCeS\\", "\\DosDevices\\GLOBALROOT\\??\\",
        "\\??\\GLOBALROOT\\??\\GLOBALROOT\\??\\"};
    for (size_t i = 0; i < ARRAY_SIZE(names); i++)
    {
        char file[512]; snprintf(file, sizeof(file), "%s%s", base, names[i]);
        for (size_t j = 0; j < ARRAY_SIZE(wrappers); j++)
        {
            char request[1024]; snprintf(request, sizeof(request), "%sC:\\%s", wrappers[j], file);
            for (char *p = request; *p; p++) if (*p == '/') *p = '\\';
            NTSTATUS expected = STATUS_SUCCESS;
#if MODE != 1
            if (j >= 2) expected = STATUS_BAD_DEVICE_TYPE;     /* old behaviour: the client gives the name to the object manager */
#endif
            if (resolve(request, expected, file, FILE_OPEN, FALSE, 0)) return 1;
            if (!expected && resolve(request, expected, file, FILE_OPEN_IF, TRUE, 7)) return 1;
        }
    }
    prefix("", 0);
    prefix("\\??\\", 4);
    prefix("\\??\\C:\\GLOBALROOT\\x", 4);
    prefix("\\??\\GLOBALROOT", 4);
    prefix("\\??\\GLOBALROOTsuffix\\??\\C:\\x", 4);
    prefix("\\??\\GLOBALROOT/??\\C:\\x", 4);
#if MODE == 1
    prefix("\\??\\GLOBALROOT\\??\\C:\\x", 18);
    prefix("\\??\\GLOBALROOT\\DosDevices\\C:\\x", 26);
    prefix("\\??\\GLOBALROOT\\", 4);                     /* a wrapper with no DOS prefix behind it: the old answer */
    prefix("\\??\\GLOBALROOT\\??\\", 18);
    prefix("\\??\\GLOBALROOT\\Device\\Null", 4);
    prefix("\\DosDevices\\GLOBALROOT\\Device\\Null", 12);
    prefix("\\??\\GLOBALROOT\\??\\GLOBALROOT\\Device\\Null", 4);   /* the last wrapper has no DOS prefix behind it either */
#endif
    /* Names the client leaves to the object manager, wrapped or not: the answer is the same STATUS_BAD_DEVICE_TYPE before and after
       (the server finds NUL, PIPE and the like in its own \\GLOBAL?? directory, so what worked before still reaches it). */
    const char *native[] = {"\\Device\\Null", "\\??\\GLOBALROOT\\Device\\Null",
        "\\??\\GLOBALROOTsuffix\\??\\C:\\x", "\\??\\GLOBALROOT\\", "\\??\\GLOBALROOT",
        "\\??\\GLOBALROOT\\??\\NUL", "\\??\\GLOBALROOT\\??\\PIPE\\name", "\\DosDevices\\GLOBALROOT\\??\\UNC\\host\\share\\f",
        "\\??\\GLOBALROOT\\??\\Global\\NUL", "\\??\\PIPE\\name", "\\??\\NUL"};
    for (size_t i = 0; i < ARRAY_SIZE(native); i++)
        if (resolve(native[i], STATUS_BAD_DEVICE_TYPE, NULL, FILE_OPEN, FALSE, 0)) return 1;
    const char *truncated = "\\??\\GLOBALROOT\\??\\GLOBALROOT\\DosDevices\\C:\\x";
    for (size_t i = 0; i <= strlen(truncated); i++)
    {
        UNICODE_STRING name = wide_n(truncated, i);
        int n = get_dos_prefix_len(&name);
        assert(n >= 0 && (unsigned int)n <= i);
        free(name.Buffer);
    }
#if MODE == 1
    puts("resolver: wrapped drive paths read the same inode and contents; offsets and options preserved; native names, lookalikes and truncated names handled");
#else
    puts("resolver: plain drive paths read; wrapped ones fail with STATUS_BAD_DEVICE_TYPE; native names, lookalikes and truncated names handled");
#endif
    return 0;
}
'''

# Every old/new answer of get_dos_prefix_len over a grammar of name pieces, each name in a buffer of exactly its length.
differential = r'''
#include <assert.h>
#include <ctype.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
#include "ntstatus.h"
#include "windef.h"
#include "winternl.h"
#define ARRAY_SIZE(a) (sizeof(a) / sizeof((a)[0]))
static const WCHAR nt_prefixW[] = {'\\','?','?','\\'};
static int ascii_wcsnicmp(const WCHAR *a, const WCHAR *b, size_t n)
{
    for (size_t i = 0; i < n; i++)
    {
        if (a[i] != b[i])
        {
            if (a[i] > 127 || b[i] > 127) return 1;
            if (tolower(a[i]) != tolower(b[i])) return 1;
        }
        if (!a[i]) break;
    }
    return 0;
}
#define wcsnicmp ascii_wcsnicmp
@OLD@
@NEW@
/* The same rule written over a C string, as a second opinion: skip any number of \GLOBALROOT\ wrappers (iOS only). */
static int model(const char *s, int ios)
{
    int pos = 0, first = 0;
    for (;;)
    {
        int p;
        if (!strncmp(s + pos, "\\??\\", 4)) p = 4;
        else if (!strncasecmp(s + pos, "\\DosDevices\\", 12)) p = 12;
        else return pos ? first : 0;
        if (ios && !strncasecmp(s + pos + p, "GLOBALROOT\\", 11)) { if (!pos) first = p; pos += p + 10; continue; }
        return pos + p;
    }
}
static int has_globalroot(const char *s)
{
    for (; *s; s++) if (!strncasecmp(s, "globalroot", 10)) return 1;
    return 0;
}
int main(void)
{
    const char *pieces[] = {"\\??\\", "\\DosDevices\\", "\\dosdevices\\", "GLOBALROOT\\", "globalroot\\", "GlobalRoot\\",
                            "GLOBALROOT", "C:\\", "x\\", "\\Device\\", "?", "\\", "\\??", "GLOBALROO"};
    const int n = ARRAY_SIZE(pieces);
    unsigned long names = 0, changed = 0;
    for (int len = 0; len <= 4; len++)
    {
        long total = 1;
        for (int i = 0; i < len; i++) total *= n;
        for (long code = 0; code < total; code++)
        {
            char s[256] = "";
            long c = code;
            for (int i = 0; i < len; i++) { strcat(s, pieces[c % n]); c /= n; }
            size_t size = strlen(s);
            WCHAR *buffer = malloc(size * sizeof(WCHAR) + 1);          /* exact: a read past the end is an ASan error */
            for (size_t i = 0; i < size; i++) buffer[i] = (unsigned char)s[i];
            UNICODE_STRING name = {size * sizeof(WCHAR), size * sizeof(WCHAR), buffer};
            int before = old_prefix_len(&name), after = new_prefix_len(&name);
            free(buffer);
            names++;
            if (before != model(s, 0)) { fprintf(stderr, "old function differs from the model: %s\n", s); return 1; }
            if (after != model(s, MODE == 1)) { fprintf(stderr, "new function differs from the model: %s (%d)\n", s, after); return 1; }
            if (!has_globalroot(s) && before != after) { fprintf(stderr, "changed without GLOBALROOT: %s\n", s); return 1; }
            if (before != after)
            {
                changed++;
                /* only a name with a wrapper right behind a DOS prefix AND a DOS prefix behind the wrapper: the answer moves right */
                assert(MODE == 1 && after > before && (after - before) >= 14);
            }
        }
    }
    printf("differential: %lu names, %lu changed (all of them wrapped), the others answer as before\n", names, changed);
    return 0;
}
'''

cc = os.environ.get('CC', 'cc')
env = {**os.environ, 'ASAN_OPTIONS': 'detect_leaks=' + ('0' if sys.platform == 'darwin' else '1')}
flags = ['-std=c11', '-D_GNU_SOURCE', '-D_WIN64', '-Wall', '-Wextra', '-Werror', '-fsanitize=address,undefined',
         '-fno-omit-frame-pointer', '-O1', '-I', str(WINE / 'include')]

with tempfile.TemporaryDirectory(prefix='bcd-globalroot-') as temp:
    temp = Path(temp)
    directory = temp / 'Program Files (x86)/Steam/steamapps/common/Example Game/Game/Binaries/Win64'
    directory.mkdir(parents=True)
    for filename in ('Data-Shipping.dat', 'Game-Win64-Shipping.exe'):
        (directory / filename).write_text('fixture')
    (temp / 'dosdevices').mkdir()
    (temp / 'dosdevices/c:').symlink_to('..')
    # label, source text, WINE_IOS, MODE (0 = old, 1 = patched for iOS, 2 = patched, not iOS)
    for label, text, ios, mode in (('old', source, True, 0), ('patched', patched, True, 1), ('patched, not iOS', patched, False, 2)):
        functions = function(text, prefix_signature) + '\n\n' + function(text, resolver_signature)
        unit = temp / 'resolver.c'
        unit.write_text(harness.replace('@FUNCTIONS@', functions))
        binary = temp / 'resolver'
        command = [cc] + flags + ['-DMODE=%d' % mode] + (['-DWINE_IOS=1'] if ios else [])
        subprocess.run(command + [str(unit), '-o', str(binary)], check=True)
        result = subprocess.run([str(binary)], cwd=temp, env=env, capture_output=True, text=True)
        assert result.returncode == 0, label + ': ' + result.stdout + result.stderr
        print('%s: %s' % (label, result.stdout.strip().splitlines()[-1]))

        old = function(source, prefix_signature).replace('get_dos_prefix_len', 'old_prefix_len')
        new = function(text, prefix_signature).replace('get_dos_prefix_len', 'new_prefix_len')
        unit = temp / 'differential.c'
        unit.write_text(differential.replace('@OLD@', old).replace('@NEW@', new))
        binary = temp / 'differential'
        subprocess.run(command + [str(unit), '-o', str(binary)], check=True)
        result = subprocess.run([str(binary)], cwd=temp, env=env, capture_output=True, text=True)
        assert result.returncode == 0, label + ': ' + result.stdout + result.stderr
        print('%s: %s' % (label, result.stdout.strip()))

    # The old resolver is what failed: show the failing status on the wrapped name, side by side with the patched one.
    unit = temp / 'resolver.c'
    unit.write_text(harness.replace('@FUNCTIONS@', function(source, prefix_signature) + '\n\n' + function(source, resolver_signature)))
    subprocess.run([cc] + flags + ['-DMODE=1', '-DWINE_IOS=1', str(unit), '-o', str(temp / 'old-as-new')], check=True)
    result = subprocess.run([str(temp / 'old-as-new')], cwd=temp, env=env, capture_output=True, text=True)
    assert result.returncode == 1 and 'GLOBALROOT resolver:' in result.stderr and 'c00000cb' in result.stderr, result.stderr
    ok('the unpatched resolver fails on the wrapped name (STATUS_BAD_DEVICE_TYPE) before any component lookup')

ok('GLOBALROOT-wrapped DOS paths: only names that failed before change; everything else answers as it did')

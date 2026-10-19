#!/usr/bin/env python3
"""Exercise the production opt-in DLL resolver with real Wine types and files.

The name translator below models a prefix's C: drive, not the DLL policy.
The production helper selects the path, preserves fallback/ownership, and
its returned files are checked by content and inode. No Wine process runs.
"""
from pathlib import Path
import importlib.util
import os
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("app_dll_patch", ROOT / "tools/patch-wine-app-local-dll.py")
patcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(patcher)

source = (ROOT / "wine/dlls/ntdll/unix/file.c").read_text()
# CI can run this after the earlier text-log/file-trace patches.
patched = patcher.patch(source)
assert patcher.patch(patched) == patched
assert patched.count("else status = madeira_dll_read_names(") == 1
assert patched.count("if (!(status = madeira_dll_read_names(") == 2
for anchor in (" *           get_full_path\n", "    else status = get_nt_and_unix_names( &new_attr", "FILE_OPEN, TRUE )))"):
    if patcher.MARKER not in source:
        try:
            patcher.patch(source.replace(anchor, "anchor removed", 1))
        except ValueError:
            pass
        else:
            raise AssertionError("partial patch must be rejected")

harness = r'''
#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>
#include "ntstatus.h"
#include "windef.h"
#include "winternl.h"
#define ARRAY_SIZE(a) (sizeof(a) / sizeof((a)[0]))
static TEB teb;
static TEB *current_teb = &teb;
static PEB peb;
static RTL_USER_PROCESS_PARAMETERS params;
#undef NtCurrentTeb
#define NtCurrentTeb() (current_teb)
static int calls, owned_translation, fail_helper_malloc;

static UNICODE_STRING wide(const char *text, WCHAR *storage, size_t capacity)
{
    size_t n = strlen(text);
    assert(n + 1 <= capacity);
    for (size_t i = 0; i <= n; i++) storage[i] = (unsigned char)text[i];
    return (UNICODE_STRING){n * sizeof(WCHAR), (n + 1) * sizeof(WCHAR), storage};
}
static const char *debugstr_us(const UNICODE_STRING *s)
{
    static char ring[4][1024];
    static unsigned int next;
    char *out = ring[next++ % 4];
    unsigned int n = s->Length / sizeof(WCHAR);
    assert(n < 1024);
    for (unsigned int i = 0; i < n; i++) out[i] = s->Buffer[i];
    out[n] = 0;
    return out;
}
static NTSTATUS get_nt_and_unix_names(OBJECT_ATTRIBUTES *attr, UNICODE_STRING *nt,
                                    char **unix_name, UINT disposition, BOOL reparse)
{
    (void)disposition; (void)reparse;
    char path[1024];
    const UNICODE_STRING *name = attr->ObjectName;
    unsigned int start = 0, n = name->Length / sizeof(WCHAR);
    calls++;
    nt->Buffer = NULL; *unix_name = NULL;
    if (n >= 4 && name->Buffer[0] == '\\' && name->Buffer[1] == '?') start = 4;
    if (n < start + 3 || name->Buffer[start + 1] != ':') return STATUS_OBJECT_NAME_INVALID;
    strcpy(path, "drive_c/");
    unsigned int p = strlen(path);
    for (unsigned int i = start + 3; i < n; i++)
    {
        char c = name->Buffer[i];
        if (c == '\\') c = '/';
        if (c >= 'A' && c <= 'Z') c += 'a' - 'A';
        assert(p + 1 < sizeof(path)); path[p++] = c;
    }
    path[p] = 0;
    *unix_name = strdup(path);
    struct stat st;
    if (stat(path, &st)) return STATUS_OBJECT_NAME_NOT_FOUND;
    if (owned_translation)
    {
        nt->Buffer = malloc(name->MaximumLength);
        memcpy(nt->Buffer, name->Buffer, name->MaximumLength);
        nt->Length = name->Length; nt->MaximumLength = name->MaximumLength;
        attr->ObjectName = nt;
    }
    return STATUS_SUCCESS;
}
static void *helper_malloc(size_t size)
{
    if (fail_helper_malloc) return NULL;
    return malloc(size);
}
#define malloc helper_malloc
#define WINE_IOS 1
#include "app_local_dll_ios.h"
#undef malloc

static void put(const char *path, const char *contents)
{
    FILE *f = fopen(path, "wb"); assert(f);
    assert(fwrite(contents, 1, strlen(contents), f) == strlen(contents));
    assert(!fclose(f));
}
static void resolve(const char *request, const char *expected, ACCESS_MASK access,
                    ULONG disposition, ULONG options, HANDLE root, int selected)
{
    WCHAR request_buffer[512];
    UNICODE_STRING name = wide(request, request_buffer, ARRAY_SIZE(request_buffer)), nt;
    OBJECT_ATTRIBUTES attr;
    memset(&attr, 0, sizeof(attr));
    attr.Length = sizeof(attr); attr.ObjectName = &name; attr.RootDirectory = root;
    char *unix_name;
    NTSTATUS status = madeira_dll_read_names(&attr, &nt, &unix_name, disposition,
                                            FALSE, access, options);
    assert(status == STATUS_SUCCESS);
    assert(!strcmp(unix_name, expected));
    if (selected)
    {
        assert(attr.ObjectName == &nt && nt.Buffer && nt.Buffer != request_buffer);
        /* Reading this after the helper returned also tests name ownership. */
        assert(strstr(debugstr_us(&nt), "\\games\\a\\") || strstr(debugstr_us(&nt), "\\Games\\A\\") ||
               strstr(debugstr_us(&nt), "\\games\\b\\"));
        const char *filename = strrchr(expected, '/') + 1;
        size_t length = strlen(filename), nt_length = nt.Length / sizeof(WCHAR);
        assert(nt_length >= length + 4 && nt.Buffer[0] == '\\' && nt.Buffer[1] == '?' &&
               nt.Buffer[2] == '?' && nt.Buffer[3] == '\\');
        for (size_t i = 0; i < length; i++)
            assert(madeira_dll_path_char(nt.Buffer[nt_length - length + i]) == filename[i]);
    }
    struct stat a, b;
    assert(!stat(unix_name, &a) && !stat(expected, &b));
    assert(a.st_dev == b.st_dev && a.st_ino == b.st_ino);
    FILE *f = fopen(unix_name, "rb"); assert(f);
    char contents[32] = {0}; assert(fread(contents, 1, sizeof(contents) - 1, f));
    assert(!fclose(f));
    if (strstr(expected, "/games/a/proxy.dll")) assert(!strcmp(contents, "proxy-a"));
    else if (strstr(expected, "/games/b/proxy.dll")) assert(!strcmp(contents, "proxy-b"));
    else if (strstr(expected, "/games/a/")) assert(!strcmp(contents, "local-a"));
    else if (strstr(expected, "/games/b/")) assert(!strcmp(contents, "local-b"));
    else assert(!strcmp(contents, "installed"));
    free(nt.Buffer); free(unix_name);
}
int main(void)
{
    _Static_assert(sizeof(WCHAR) == 2, "Windows UTF-16 ABI");
    teb.Peb = &peb; peb.ProcessParameters = &params;
    WCHAR image[512], request[512], output[512];
    params.ImagePathName = wide("C:\\games\\a\\game.exe", image, ARRAY_SIZE(image));
    const char *installed = "\\??\\C:\\installed\\widget.dll";
    assert(!mkdir("drive_c", 0700)); assert(!mkdir("drive_c/installed", 0700));
    assert(!mkdir("drive_c/games", 0700)); assert(!mkdir("drive_c/games/a", 0700));
    assert(!mkdir("drive_c/games/b", 0700));
    put("drive_c/installed/widget.dll", "installed");
    put("drive_c/games/a/widget.dll", "local-a"); put("drive_c/games/b/widget.dll", "local-b");
    put("drive_c/games/a/proxy.dll", "proxy-a"); put("drive_c/games/b/proxy.dll", "proxy-b");
    put("drive_c/installed/missing.dll", "installed");
    put("drive_c/installed/folder.dll", "installed"); assert(!mkdir("drive_c/games/a/folder.dll", 0700));
    unsetenv("MADEIRA_DLL_LOCAL");
    resolve(installed, "drive_c/installed/widget.dll", GENERIC_READ, FILE_OPEN, 0, NULL, 0);
    setenv("MADEIRA_DLL_LOCAL", "", 1);
    resolve(installed, "drive_c/installed/widget.dll", GENERIC_READ, FILE_OPEN, 0, NULL, 0);
    setenv("MADEIRA_DLL_LOCAL", " unrelated.dll ; WIDGET.DLL ; missing.dll;folder.dll ", 1);
    resolve(installed, "drive_c/games/a/widget.dll", GENERIC_READ, FILE_OPEN, 0, NULL, 1);
    resolve("C:\\installed\\WIDGET.DLL", "drive_c/games/a/widget.dll", FILE_READ_ATTRIBUTES, FILE_OPEN, 0, NULL, 1);
    owned_translation = 1;
    resolve(installed, "drive_c/games/a/widget.dll", GENERIC_READ, FILE_OPEN, 0, NULL, 1);
    owned_translation = 0;
    resolve("\\??\\C:\\installed\\missing.dll", "drive_c/installed/missing.dll", GENERIC_READ, FILE_OPEN, 0, NULL, 0);
    resolve("\\??\\C:\\installed\\folder.dll", "drive_c/installed/folder.dll", GENERIC_READ, FILE_OPEN, 0, NULL, 0);
    const ACCESS_MASK writes[] = {FILE_WRITE_DATA, FILE_APPEND_DATA, FILE_WRITE_EA, FILE_WRITE_ATTRIBUTES,
                                 DELETE, WRITE_DAC, WRITE_OWNER, GENERIC_WRITE, GENERIC_ALL, MAXIMUM_ALLOWED};
    for (size_t i = 0; i < ARRAY_SIZE(writes); i++)
        resolve(installed, "drive_c/installed/widget.dll", GENERIC_READ | writes[i], FILE_OPEN, 0, NULL, 0);
    for (ULONG disp = FILE_SUPERSEDE; disp <= FILE_OVERWRITE_IF; disp++)
        if (disp != FILE_OPEN) resolve(installed, "drive_c/installed/widget.dll", GENERIC_READ, disp, 0, NULL, 0);
    const ULONG options[] = {FILE_DELETE_ON_CLOSE, FILE_DIRECTORY_FILE, FILE_OPEN_REPARSE_POINT};
    for (size_t i = 0; i < ARRAY_SIZE(options); i++)
        resolve(installed, "drive_c/installed/widget.dll", GENERIC_READ, FILE_OPEN, options[i], NULL, 0);
    resolve(installed, "drive_c/installed/widget.dll", GENERIC_READ, FILE_OPEN, 0, (HANDLE)1, 0);
    fail_helper_malloc = 1;
    resolve(installed, "drive_c/installed/widget.dll", GENERIC_READ, FILE_OPEN, 0, NULL, 0);
    fail_helper_malloc = 0;
    params.ImagePathName = wide("C:\\games\\b\\game.exe", image, ARRAY_SIZE(image));
    resolve(installed, "drive_c/games/b/widget.dll", GENERIC_READ, FILE_OPEN, 0, NULL, 1);
    params.ImagePathName = wide("C:\\games\\a\\game.exe", image, ARRAY_SIZE(image));

    /* A proxy is a different loaded image, not another copy of the initial SDK.
     * Keep that initial app-local load while redirecting later external paths. */
    const char *alias = "widget.dll=proxy.dll";
    setenv("MADEIRA_DLL_LOCAL", alias, 1);
    resolve(installed, "drive_c/games/a/proxy.dll", GENERIC_READ, FILE_OPEN, 0, NULL, 1);
    resolve("C:\\installed\\WIDGET.DLL", "drive_c/games/a/proxy.dll", FILE_READ_ATTRIBUTES, FILE_OPEN, 0, NULL, 1);
    resolve("\\??\\C:\\games\\a\\widget.dll", "drive_c/games/a/widget.dll", GENERIC_READ, FILE_OPEN, 0, NULL, 0);
    resolve("C:/Games/A/WIDGET.DLL", "drive_c/games/a/widget.dll", GENERIC_READ, FILE_OPEN, 0, NULL, 0);
    resolve("\\??\\C:\\games\\a\\proxy.dll", "drive_c/games/a/proxy.dll", GENERIC_READ, FILE_OPEN, 0, NULL, 0);
    owned_translation = 1;
    resolve(installed, "drive_c/games/a/proxy.dll", GENERIC_READ, FILE_OPEN, 0, NULL, 1);
    owned_translation = 0;
    for (size_t i = 0; i < ARRAY_SIZE(writes); i++)
        resolve(installed, "drive_c/installed/widget.dll", GENERIC_READ | writes[i], FILE_OPEN, 0, NULL, 0);
    for (ULONG disp = FILE_SUPERSEDE; disp <= FILE_OVERWRITE_IF; disp++)
        if (disp != FILE_OPEN) resolve(installed, "drive_c/installed/widget.dll", GENERIC_READ, disp, 0, NULL, 0);
    for (size_t i = 0; i < ARRAY_SIZE(options); i++)
        resolve(installed, "drive_c/installed/widget.dll", GENERIC_READ, FILE_OPEN, options[i], NULL, 0);
    resolve(installed, "drive_c/installed/widget.dll", GENERIC_READ, FILE_OPEN, 0, (HANDLE)1, 0);
    fail_helper_malloc = 1;
    resolve(installed, "drive_c/installed/widget.dll", GENERIC_READ, FILE_OPEN, 0, NULL, 0);
    fail_helper_malloc = 0;
    setenv("MADEIRA_DLL_LOCAL", "widget.dll=missing.dll", 1);
    resolve(installed, "drive_c/installed/widget.dll", GENERIC_READ, FILE_OPEN, 0, NULL, 0);
    setenv("MADEIRA_DLL_LOCAL", "widget.dll=folder.dll", 1);
    resolve(installed, "drive_c/installed/widget.dll", GENERIC_READ, FILE_OPEN, 0, NULL, 0);
    setenv("MADEIRA_DLL_LOCAL", "unrelated.dll; WIDGET.DLL = PROXY.DLL ; other.dll", 1);
    resolve(installed, "drive_c/games/a/proxy.dll", GENERIC_READ, FILE_OPEN, 0, NULL, 1);
    params.ImagePathName = wide("\\??\\C:\\games\\b\\game.exe", image, ARRAY_SIZE(image));
    resolve(installed, "drive_c/games/b/proxy.dll", GENERIC_READ, FILE_OPEN, 0, NULL, 1);
    resolve("C:\\games\\b\\widget.dll", "drive_c/games/b/widget.dll", GENERIC_READ, FILE_OPEN, 0, NULL, 0);
    params.ImagePathName = wide("C:\\Games\\A\\game.exe", image, ARRAY_SIZE(image));
    resolve("\\??\\C:\\games\\a\\widget.dll", "drive_c/games/a/widget.dll", GENERIC_READ, FILE_OPEN, 0, NULL, 0);
    resolve(installed, "drive_c/games/a/proxy.dll", GENERIC_READ, FILE_OPEN, 0, NULL, 1);
    params.ImagePathName = wide("C:\\games\\a\\game.exe", image, ARRAY_SIZE(image));

    UNICODE_STRING alias_req = wide(installed, request, ARRAY_SIZE(request));
    unsigned int alias_n = madeira_dll_local_path(alias, &alias_req, &params.ImagePathName, output, ARRAY_SIZE(output));
    assert(alias_n && !madeira_dll_local_path(alias, &alias_req, &params.ImagePathName, output, alias_n));
    assert(!madeira_dll_local_path(alias, &alias_req, &params.ImagePathName, output, alias_n - 1));
    const char *bad_alias[] = {"widget.dll=", "widget.dll=proxy", "widget.dll=../proxy.dll", "widget.dll=..\\proxy.dll",
                              "widget.dll=C:\\proxy.dll", "widget.dll=\\\\server\\proxy.dll", "widget.dll=*.dll",
                              "widget.dll=pro?xy.dll", "widget.dll=proxy.dll=other.dll", "widget.dll=pro xy.dll"};
    for (size_t i = 0; i < ARRAY_SIZE(bad_alias); i++)
    {
        assert(!madeira_dll_local_path(bad_alias[i], &alias_req, &params.ImagePathName, output, ARRAY_SIZE(output)));
        setenv("MADEIRA_DLL_LOCAL", bad_alias[i], 1);
        resolve(installed, "drive_c/installed/widget.dll", GENERIC_READ, FILE_OPEN, 0, NULL, 0);
    }
    setenv("MADEIRA_DLL_LOCAL", "widget.dll=../proxy.dll;widget.dll=proxy.dll", 1);
    resolve(installed, "drive_c/games/a/proxy.dll", GENERIC_READ, FILE_OPEN, 0, NULL, 1);
    const char *ambiguous[] = {"C:\\games\\a\\.\\widget.dll", "C:\\games\\a\\..\\a\\widget.dll",
                               "C:\\games\\a\\\\widget.dll", "C:\\installed\\..\\installed\\widget.dll"};
    for (size_t i = 0; i < ARRAY_SIZE(ambiguous); i++)
    {
        alias_req = wide(ambiguous[i], request, ARRAY_SIZE(request));
        assert(!madeira_dll_local_path(alias, &alias_req, &params.ImagePathName, output, ARRAY_SIZE(output)));
    }
    alias_req = wide(installed, request, ARRAY_SIZE(request));
    params.ImagePathName = wide("C:\\games\\a\\.\\game.exe", image, ARRAY_SIZE(image));
    assert(!madeira_dll_local_path(alias, &alias_req, &params.ImagePathName, output, ARRAY_SIZE(output)));
    params.ImagePathName = wide("C:\\games\\a\\game.exe", image, ARRAY_SIZE(image));
    alias_req.Length--;
    assert(!madeira_dll_local_path(alias, &alias_req, &params.ImagePathName, output, ARRAY_SIZE(output)));
    alias_req.Length++; request[7] = 0;
    assert(!madeira_dll_local_path(alias, &alias_req, &params.ImagePathName, output, ARRAY_SIZE(output)));
    setenv("MADEIRA_DLL_LOCAL", "widget.dll;missing.dll;folder.dll", 1);

    UNICODE_STRING req = wide(installed, request, ARRAY_SIZE(request));
    unsigned int n = madeira_dll_local_path("widget.dll", &req, &params.ImagePathName, output, ARRAY_SIZE(output));
    assert(n && !madeira_dll_local_path("widget.dll", &req, &params.ImagePathName, output, n));
    assert(!madeira_dll_local_path("widget.dll", &req, &params.ImagePathName, output, n - 1));
    const char *bad[] = {"widget.dll", "\\??\\pipe\\widget.dll", "\\??\\unix\\tmp\\widget.dll", "\\\\server\\share\\widget.dll",
                         "\\??\\C:\\installed\\widget.dll.bak", "C:\\installed\\other.dll", "C:\\games\\a\\widget.dll"};
    for (size_t i = 0; i < ARRAY_SIZE(bad); i++)
    {
        req = wide(bad[i], request, ARRAY_SIZE(request));
        assert(!madeira_dll_local_path("widget.dll", &req, &params.ImagePathName, output, ARRAY_SIZE(output)));
        assert(!madeira_dll_local_path(alias, &req, &params.ImagePathName, output, ARRAY_SIZE(output)));
    }
    req = wide(installed, request, ARRAY_SIZE(request));
    for (const char **p = (const char *[]) {"*", "widget", "C:\\widget.dll", "xwidget.dll", "widget.dllx", NULL}; *p; p++)
        assert(!madeira_dll_local_path(*p, &req, &params.ImagePathName, output, ARRAY_SIZE(output)));
    req.Length--;
    assert(!madeira_dll_local_path("widget.dll", &req, &params.ImagePathName, output, ARRAY_SIZE(output)));
    req.Length++; request[7] = 0;
    assert(!madeira_dll_local_path("widget.dll", &req, &params.ImagePathName, output, ARRAY_SIZE(output)));
    teb.Peb = NULL;
    resolve(installed, "drive_c/installed/widget.dll", GENERIC_READ, FILE_OPEN, 0, NULL, 0);
    current_teb = NULL;
    resolve(installed, "drive_c/installed/widget.dll", GENERIC_READ, FILE_OPEN, 0, NULL, 0);
    current_teb = &teb; teb.Peb = &peb; peb.ProcessParameters = NULL;
    resolve(installed, "drive_c/installed/widget.dll", GENERIC_READ, FILE_OPEN, 0, NULL, 0);
    puts("PASS: default and bare-name behavior; proxy inode/content with initial local SDK preserved; owned names, per-process image, missing/non-file/OOM fallback, mutation/device/path refusals");
    return 0;
}
'''

with tempfile.TemporaryDirectory(prefix="madeira-app-dll-") as temp:
    temp = Path(temp)
    unit = temp / "check.c"
    unit.write_text(harness)
    command = [os.environ.get("CC", "cc"), "-std=c11", "-D_GNU_SOURCE", "-D_WIN64", "-Wall", "-Wextra", "-Werror",
               "-Wno-unused-parameter", "-fsanitize=address,undefined", "-fno-omit-frame-pointer",
               "-I", str(ROOT / "wine/include"), "-I", str(ROOT / "build/ntdll-unix"), str(unit), "-o", str(temp / "check")]
    subprocess.run(command, check=True, cwd=temp)
    leaks = "0" if sys.platform == "darwin" else "1"
    subprocess.run([str(temp / "check")], check=True, cwd=temp,
                   env={**os.environ, "ASAN_OPTIONS": "detect_leaks=" + leaks})
print("PASS: patch applies once at the pinned file API; drift fails before edits")

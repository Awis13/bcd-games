#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""[steam-api] (build/ntdll-unix/thread_ios.c): what a process sees of Steam when steam_api says there is none.

The Rockstar Games Launcher prints "[S_API] SteamAPI_Init(): SteamAPI_IsSteamRunning() did not locate a running
instance of Steam.." in 20 of the 40 distinct Rockstar-launcher Dock sessions of 2026-10-19/20 and stops with
LAUNCHER_ERR_STEAM_FAILED; in 13 others the same call loads steamclient64.dll. NtRaiseException now reads the Steam
registry values, the pid's process and the DIPC objects when that debug string passes, and logs them (reads only).

Checks, on the real region of thread_ios.c compiled on the host against stand-ins for the Nt calls:
  - the message test accepts exactly steam_api's text (one safe read, only 80-200 byte strings of code 0x40010006),
    and nothing else: other debug strings, other codes, a null or short or long string;
  - with the values published: the lines say pid, ActiveUser as zero / not zero (the account id never appears),
    the client DLL, the paths, the process's exit status and which objects open; with an empty registry every line says
    why it is empty; no write call exists in the region; at most two reports per process;
  - in thread_ios.c the hook is inside NtRaiseException's WINE_IOS block, before send_debug_event, for a first-chance
    exception only, and the workflow runs this check.
"""
from pathlib import Path
import re, subprocess, sys, tempfile

root = Path(__file__).resolve().parents[2]
src = (root / "build/ntdll-unix/thread_ios.c").read_text()
workflow = (root / ".github/workflows/build-ipa.yml").read_text()
ok = True


def check(what, cond):
    global ok
    print(("ok   " if cond else "FAIL ") + what)
    ok &= bool(cond)


region = src[src.index("/* steam-api-diag-test:begin"):src.index("/* steam-api-diag-test:end */")]
fn = src[src.index("NTSTATUS WINAPI NtRaiseException("):]
fn = fn[:fn.index("\n}\n")]
check("the region has no write, create, delete or terminate call",
      not re.search(r"NtSetValueKey|NtCreateKey|NtDeleteKey|NtDeleteValueKey|NtWriteVirtualMemory|NtTerminate|NtSuspend|NtSetInformation",
                    region))
check("the hook is in NtRaiseException's WINE_IOS code, for a first-chance exception, before send_debug_event",
      "if (rec && first_chance && ios_steam_api_message( rec )) ios_steam_api_diag();" in fn and
      fn.index("ios_steam_api_message( rec )") < fn.index("status = send_debug_event(") and
      fn.rfind("#ifdef WINE_IOS", 0, fn.index("ios_steam_api_message( rec )")) >
      fn.rfind("#endif", 0, fn.index("ios_steam_api_message( rec )")))
check("the report is capped at two per process", "if (InterlockedIncrement( &told ) > 2) return;" in region)
check("the account id is only reported as zero / not zero",
      '"HKCU ActiveProcess ActiveUser", 1, NULL' in region and 'v ? "not zero" : "zero"' in region)
check("the workflow runs this check", "python3 tests/host/check-steam-api-diag.py" in workflow)

harness = r'''
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
typedef int NTSTATUS; typedef unsigned int ULONG, ACCESS_MASK; typedef unsigned short WCHAR, USHORT;
typedef long LONG; typedef unsigned long long SIZE_T, ULONG_PTR; typedef void *HANDLE; typedef int BOOL;
typedef struct { USHORT Length, MaximumLength; WCHAR *Buffer; } UNICODE_STRING;
typedef struct { ULONG Length; HANDLE RootDirectory; UNICODE_STRING *ObjectName; ULONG Attributes; void *a, *b; } OBJECT_ATTRIBUTES;
#define InitializeObjectAttributes(p, n, at, r, s) do { (p)->Length = sizeof(*(p)); (p)->RootDirectory = (r); \
    (p)->ObjectName = (n); (p)->Attributes = (at); (p)->a = (s); (p)->b = NULL; } while (0)
typedef struct { ULONG TitleIndex, Type, DataLength; unsigned char Data[1]; } KEY_VALUE_PARTIAL_INFORMATION;
typedef struct { HANDLE UniqueProcess, UniqueThread; } CLIENT_ID;
typedef struct { NTSTATUS ExitStatus; void *Peb; ULONG_PTR Affinity; LONG Priority; ULONG_PTR Pid, Parent; } PROCESS_BASIC_INFORMATION;
typedef struct { ULONG ExceptionCode, ExceptionFlags; void *ExceptionRecord, *ExceptionAddress; ULONG NumberParameters;
                 ULONG_PTR ExceptionInformation[15]; } EXCEPTION_RECORD;
#define KEY_QUERY_VALUE 1
#define KEY_WOW64_64KEY 0x100
#define KEY_WOW64_32KEY 0x200
#define OBJ_CASE_INSENSITIVE 0x40
#define REG_SZ 1
#define REG_EXPAND_SZ 2
#define REG_DWORD 4
#define SYNCHRONIZE 0x100000
#define PROCESS_QUERY_LIMITED_INFORMATION 0x1000
#define DBG_PRINTEXCEPTION_C 0x40010006
#define KeyValuePartialInformation 2
#define ProcessBasicInformation 0
#define WINAPI
#define min(a, b) ((a) < (b) ? (a) : (b))
#define NtCurrentProcess() ((HANDLE)(long long)-1)
#define HandleToULong(h) ((ULONG)(ULONG_PTR)(h))
#define ULongToHandle(u) ((HANDLE)(ULONG_PTR)(u))
#define InterlockedIncrement(p) (__sync_add_and_fetch((p), 1))
static struct { struct { HANDLE UniqueProcess, UniqueThread; } ClientId; } fake_teb = {{ (HANDLE)0xd4, 0 }};
#define NtCurrentTeb() (&fake_teb)
static void dbg_err(const char *fmt, ...) { va_list ap; va_start(ap, fmt); vfprintf(stdout, fmt, ap); va_end(ap); }
#define ERR_(ch) dbg_err
#define seh 0

/* A registry of strings: "path|value" -> type + bytes. Scenario "full" has everything, "empty" nothing. */
static int scenario_full;
static const char *last_path;
static NTSTATUS NtOpenKey(HANDLE *key, ACCESS_MASK access, const OBJECT_ATTRIBUTES *attr)
{
    static char path[160]; size_t i, n = attr->ObjectName->Length / 2;
    for (i = 0; i < n; i++) path[i] = (char)attr->ObjectName->Buffer[i];
    path[n] = 0;
    if (!scenario_full) return 0xc0000034;
    if (!strstr(path, "\\Valve\\Steam")) return 0xc0000034;
    if (strstr(path, "\\Registry\\Machine") && !(access & (KEY_WOW64_32KEY | KEY_WOW64_64KEY))) return 0xc0000034;
    if (strstr(path, "\\Registry\\Machine") && (access & KEY_WOW64_64KEY)) return 0xc0000034;   /* Dock wrote the 32-bit view */
    last_path = strstr(path, "ActiveProcess") ? "active" : strstr(path, "\\Registry\\User") ? "user" : "machine";
    *key = (HANDLE)path; return 0;
}
static NTSTATUS NtClose(HANDLE h) { (void)h; return 0; }
static NTSTATUS NtQueryValueKey(HANDLE key, const UNICODE_STRING *name, int cls, void *out, ULONG len, ULONG *got)
{
    char v[64]; size_t i, n = name->Length / 2;
    KEY_VALUE_PARTIAL_INFORMATION *info = out; (void)key; (void)cls; (void)len;
    for (i = 0; i < n; i++) v[i] = (char)name->Buffer[i];
    v[n] = 0; *got = 0;
#define DW(val) do { ULONG x = (val); info->Type = REG_DWORD; info->DataLength = 4; memcpy(info->Data, &x, 4); return 0; } while (0)
#define SZ(text) do { const char *t = (text); WCHAR *w = (WCHAR *)info->Data; size_t k; for (k = 0; t[k]; k++) w[k] = (unsigned char)t[k]; \
    w[k] = 0; info->Type = REG_SZ; info->DataLength = (ULONG)((k + 1) * 2); return 0; } while (0)
    if (!strcmp(last_path, "active"))
    {
        if (!strcmp(v, "pid")) DW(52);
        if (!strcmp(v, "ActiveUser")) DW(123456789u);       /* a made-up account id: must never be printed */
        if (!strcmp(v, "SteamClientDll64")) SZ("C:\\Program Files (x86)\\Steam\\steamclient64.dll");
    }
    else if (!strcmp(last_path, "user") && !strcmp(v, "SteamPath")) SZ("C:\\Program Files (x86)\\Steam");
    else if (!strcmp(last_path, "machine") && !strcmp(v, "SteamPID")) DW(52);
    return 0xc0000034;
}
static NTSTATUS NtOpenProcess(HANDLE *h, ACCESS_MASK a, const OBJECT_ATTRIBUTES *attr, const CLIENT_ID *id)
{ (void)attr; (void)a; if (!scenario_full || HandleToULong(id->UniqueProcess) != 52) return 0xc0000008; *h = (HANDLE)1; return 0; }
static NTSTATUS NtQueryInformationProcess(HANDLE h, int cls, void *out, ULONG len, ULONG *got)
{ PROCESS_BASIC_INFORMATION *p = out; (void)h; (void)cls; (void)len; (void)got; p->ExitStatus = 259; return 0; }
static NTSTATUS object_open(const OBJECT_ATTRIBUTES *attr, int consume_only)
{
    static char name[160]; size_t i, n = attr->ObjectName->Length / 2;
    for (i = 0; i < n; i++) name[i] = (char)attr->ObjectName->Buffer[i];
    name[n] = 0;
    return scenario_full && strstr(name, consume_only ? "CONSUME" : "") && !strstr(name, "PRODUCE") ? 0 : 0xc0000034;
}
static NTSTATUS NtOpenSemaphore(HANDLE *h, ACCESS_MASK a, const OBJECT_ATTRIBUTES *attr)
{ (void)a; if (object_open(attr, 1)) return 0xc0000034; *h = (HANDLE)2; return 0; }
static NTSTATUS NtOpenEvent(HANDLE *h, ACCESS_MASK a, const OBJECT_ATTRIBUTES *attr)
{ (void)h; (void)a; (void)attr; return 0xc0000034; }
static NTSTATUS NtReadVirtualMemory(HANDLE p, const void *addr, void *buf, SIZE_T n, SIZE_T *got)
{ (void)p; if (addr == (const void *)1) return 0xc0000005; memcpy(buf, addr, n); *got = n; return 0; }
''' + region + r'''
static int message(const char *text, ULONG code, int params)
{
    EXCEPTION_RECORD rec; memset(&rec, 0, sizeof(rec));
    rec.ExceptionCode = code; rec.NumberParameters = params;
    rec.ExceptionInformation[0] = strlen(text) + 1; rec.ExceptionInformation[1] = (ULONG_PTR)text;
    return ios_steam_api_message(&rec);
}
int main(int argc, char **argv)
{
    static const char steam[] = "[S_API] SteamAPI_Init(): SteamAPI_IsSteamRunning() did not locate a running instance of Steam..";
    static const char loaded[] = "[S_API] SteamAPI_Init(): Loaded 'C:\\Program Files (x86)\\Steam\\steamclient64.dll' OK..";
    EXCEPTION_RECORD rec; memset(&rec, 0, sizeof(rec));
    if (argc > 1 && !strcmp(argv[1], "message"))
    {
        printf("steam=%d loaded=%d other=%d code=%d params=%d\n", message(steam, DBG_PRINTEXCEPTION_C, 2),
               message(loaded, DBG_PRINTEXCEPTION_C, 2), message("hello from a game", DBG_PRINTEXCEPTION_C, 2),
               message(steam, 0x4001000a, 2), message(steam, DBG_PRINTEXCEPTION_C, 1));
        rec.ExceptionCode = DBG_PRINTEXCEPTION_C; rec.NumberParameters = 2; rec.ExceptionInformation[0] = 100;
        printf("null=%d", ios_steam_api_message(&rec));
        rec.ExceptionInformation[1] = 1;
        printf(" bad_pointer=%d", ios_steam_api_message(&rec));
        rec.ExceptionInformation[0] = 79; rec.ExceptionInformation[1] = (ULONG_PTR)steam;
        printf(" short=%d", ios_steam_api_message(&rec));
        rec.ExceptionInformation[0] = 201;
        printf(" long=%d\n", ios_steam_api_message(&rec));
        return 0;
    }
    scenario_full = argc > 1 && !strcmp(argv[1], "full");
    ios_steam_api_diag(); ios_steam_api_diag(); ios_steam_api_diag();      /* the third prints nothing */
    return 0;
}
'''
with tempfile.TemporaryDirectory() as t:
    c = Path(t) / "sd.c"; c.write_text(harness)
    exe = Path(t) / "sd"
    r = subprocess.run(["cc", "-std=gnu11", "-Wall", "-Wno-unused-function", "-Wno-unused-variable", "-fsanitize=address,undefined",
                        str(c), "-o", str(exe)], capture_output=True, text=True)
    check("the region compiles on the host" + ("" if r.returncode == 0 else ": " + r.stderr[:600]), r.returncode == 0)
    if r.returncode == 0:
        def run(arg):
            p = subprocess.run([str(exe), arg], capture_output=True, text=True)
            assert p.returncode == 0, (arg, p.stderr)
            return p.stdout
        m = run("message")
        check("the test accepts steam_api's text and nothing else: " + m.strip(),
              m.splitlines()[0] == "steam=1 loaded=0 other=0 code=0 params=0" and
              m.splitlines()[1] == "null=0 bad_pointer=0 short=0 long=0")
        out = run("full")
        lines = [l for l in out.splitlines() if l.startswith("[steam-api]")]
        check("two reports, not three", sum(1 for l in lines if "found no running Steam" in l) == 2)
        one = out.split("found no running Steam", 2)[1]
        check("the pid, the client DLL, the paths and the process are reported",
              "HKCU ActiveProcess pid: DWORD 52" in one and "steamclient64.dll" in one and
              'HKCU Steam SteamPath: SZ "C:\\Program Files (x86)\\Steam"' in one and
              "HKLM(32) Steam SteamPID: DWORD 52" in one and
              "OpenProcess(52): opens, exit status 259 (259 = still running)" in one)
        check("the account id is reported only as not zero, never as a number",
              "ActiveUser: DWORD, not zero" in one and "123456789" not in out)
        check("a missing value or key, and the objects, are reported by name",
              "HKLM(32) Steam InstallPath: no value" in one and "HKLM(64) Steam InstallPath: key not opened" in one and
              "STEAM_DIPC_CONSUME: opens" in one and "STEAM_DIPC_PRODUCE: not opened" in one)
        out = run("empty")
        one = out.split("found no running Steam", 2)[1]
        check("an empty registry: every key says it is not opened, no process line, objects not found",
              one.count("key not opened, status 0xc0000034") == 7 and "OpenProcess" not in one and
              one.count("not opened, status 0xc0000034") == 9)
print("PASS: steam api diag" if ok else "FAILED: steam api diag")
sys.exit(0 if ok else 1)

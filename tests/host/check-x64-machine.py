#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""tools/patch-wine-x64-machine.py: env.MADEIRA_X64_MACHINE = 1, a 32-bit program hears AMD64 as the native machine
(BeamNG.drive's launcher started its native ARM64 build, BinArm64\\BeamNG.drive.arm64.exe, which cannot load: SYDJDY,
DZ6BN4, VV1FQ4, PEVMEQ).

Checks:
  1. the patch applies once to Wine's kernelbase/process.c and ntdll/env.c (when the Wine sources are there), is
     idempotent, reports a moved anchor by name, keeps every original line but the three it replaces, and touches
     nothing below those two answers (not the unix library's SystemSupportedProcessorArchitectures, not wow64, which
     reads it at start-up to pick the CPU backend);
  2. both files compile for i686 (llvm-mingw, syntax only, when the toolchain and the configured i386 includes exist);
  3. the patched functions, compiled on the host with small stand-ins, in forked children (a static caches the first
     read): IsWow64Process2 hears ARM64 as native -> AMD64 only with the variable set to exactly 1; the process machine,
     other native machines, failures and a NULL out parameter are untouched; the log line is limited to 4; the
     environment helper of ntdll reads the variable from the environment block it is given;
  4. the workflow applies the patch only around the i386 farm (the arm64ec extra-DLL builds compare an unpatched
     ntdll/kernelbase byte for byte), the farm's cache key hashes the script, and the key is wired through the catalog,
     the recommendation list's policy, the session's unsetenv, auto-tune (rule, fixture) and the BeamNG rule.
Needs a C compiler (cc) and the Wine source tree (BCD_WINE_SRC, or wine/ in the repository, or ~/madeira-bcd/wine).
"""
from pathlib import Path
import ctypes
import fnmatch
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[2]
ok = True


def check(what, cond, extra=""):
    global ok
    print(("ok   " if cond else "FAIL ") + what + ((" :: " + str(extra)) if (extra and not cond) else ""))
    ok &= bool(cond)


spec = importlib.util.spec_from_file_location("patch_x64", root / "tools/patch-wine-x64-machine.py")
patch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(patch)


def wine_tree():
    for c in (os.environ.get("BCD_WINE_SRC"), root / "wine", Path.home() / "madeira-bcd/wine"):
        if c and (Path(c) / "dlls/kernelbase/process.c").is_file():
            return Path(c)
    return None


def between(text, start, end):
    i = text.index(start)
    return text[i:text.index(end, i) + len(end)]


KB_STUBS = r'''
#include <stdio.h>
#include <string.h>
#include <stdlib.h>
typedef int BOOL; typedef unsigned short USHORT; typedef unsigned int DWORD; typedef int LONG; typedef void *HANDLE;
typedef unsigned short WCHAR; typedef unsigned int NTSTATUS;
#define WINAPI
#define DECLSPEC_HOTPATCH
#define ARRAY_SIZE(a) (sizeof(a) / sizeof((a)[0]))
#define IMAGE_FILE_MACHINE_I386 0x014c
#define IMAGE_FILE_MACHINE_AMD64 0x8664
#define IMAGE_FILE_MACHINE_ARM64 0xaa64
static int err_calls;
#define ERR(...) (err_calls++)
static WCHAR g_value[16]; static int g_set;
static DWORD GetEnvironmentVariableW( const WCHAR *name, WCHAR *buf, DWORD size )
{
    DWORD len = 0;
    if (!g_set) return 0;
    while (g_value[len]) len++;
    if (len + 1 > size) return len + 1;
    memcpy( buf, g_value, (len + 1) * sizeof(WCHAR) );
    return len;
}
static LONG InterlockedIncrement( LONG volatile *p ) { return ++*p; }
static USHORT g_host_native, g_host_process; static NTSTATUS g_status;
static NTSTATUS RtlWow64GetProcessMachines( HANDLE process, USHORT *machine, USHORT *native )
{
    if (g_status) return g_status;
    if (machine) *machine = g_host_process;
    if (native) *native = g_host_native;
    return 0;
}
static BOOL set_ntstatus( NTSTATUS status ) { return !status; }
'''
KB_EXPORTS = r'''
void set_key( const char *v ) { int i = 0; g_set = v != NULL; if (v) { for (; v[i]; i++) g_value[i] = (WCHAR)v[i]; g_value[i] = 0; } }
int call( unsigned host_native, unsigned host_process, unsigned status, int null_native, unsigned *out )
{
    USHORT machine = 0x1111, native = 0x2222;
    BOOL ret;
    g_host_native = host_native; g_host_process = host_process; g_status = status;
    ret = IsWow64Process2( (HANDLE)0x10, &machine, null_native ? NULL : &native );
    out[0] = machine; out[1] = null_native ? 0x2222 : native;
    return ret;
}
int err_count( void ) { return err_calls; }
'''
ENV_STUBS = r'''
#include <stdio.h>
#include <string.h>
#include <stdlib.h>
typedef int BOOL; typedef unsigned short USHORT; typedef unsigned short WCHAR; typedef unsigned int NTSTATUS;
typedef struct { USHORT Length, MaximumLength; WCHAR *Buffer; } UNICODE_STRING;
#define RTL_CONSTANT_STRING(s) { sizeof(s) - sizeof(WCHAR), sizeof(s), (WCHAR *)(s) }
static NTSTATUS RtlQueryEnvironmentVariable_U( WCHAR *env, UNICODE_STRING *name, UNICODE_STRING *value )
{
    unsigned n = name->Length / sizeof(WCHAR);
    for (; *env; env += wcslen16( env ) + 1)
    {
        unsigned i;
        for (i = 0; i < n && env[i] && (env[i] | 0x20) == (name->Buffer[i] | 0x20); i++) ;
        if (i == n && env[n] == '=')
        {
            unsigned len = wcslen16( env + n + 1 );
            if ((len + 1) * sizeof(WCHAR) > value->MaximumLength) { value->Length = len * sizeof(WCHAR); return 0xC0000023; }
            memcpy( value->Buffer, env + n + 1, (len + 1) * sizeof(WCHAR) );
            value->Length = len * sizeof(WCHAR);
            return 0;
        }
    }
    return 0xC0000100;
}
'''
ENV_PRE = "static unsigned wcslen16( const unsigned short *s ) { unsigned n = 0; while (s[n]) n++; return n; }\n"
ENV_EXPORTS = r'''
int wanted( WCHAR *env ) { return madeira_x64_machine( env ); }
'''


def fork_run(fn):
    r, w = os.pipe()
    pid = os.fork()
    if pid == 0:
        os.close(r)
        try:
            out = json.dumps(fn())
        except BaseException as e:      # noqa
            out = json.dumps({"error": repr(e)})
        os.write(w, out.encode())
        os._exit(0)
    os.close(w)
    data = b""
    while True:
        chunk = os.read(r, 65536)
        if not chunk:
            break
        data += chunk
    os.close(r)
    os.waitpid(pid, 0)
    return json.loads(data.decode()) if data else {"error": "no answer (child died)"}


wine = wine_tree()
if wine is None or not shutil.which("cc"):
    print("SKIP: no Wine source tree or no C compiler (set BCD_WINE_SRC)")
else:
    texts, patched_texts = {}, {}
    for name, edits in patch.FILES:
        text = (wine / name).read_text()
        if patch.MARKER in text:          # a clone the workflow already patched: step back to the pinned text
            for old, new in edits:
                text = text.replace(new, old)
        texts[name] = text
        patched_texts[name] = patch.patch(text, edits)
        check(name + ": the patch changes the pinned file and a second run changes nothing",
              patched_texts[name] != text and patch.MARKER in patched_texts[name] and
              patch.patch(patched_texts[name], edits) == patched_texts[name])
        try:
            patch.patch(text.replace(edits[-1][0].splitlines()[0], "/* moved */", 1), edits)
            check(name + ": a moved anchor is reported", False)
        except ValueError as error:
            check(name + ": a moved anchor is reported by name", "anchor missing" in str(error))
        gone = [l for l in text.splitlines() if l.strip() and l not in patched_texts[name].splitlines()]
        check(name + ": only the replaced line goes (%d)" % len(gone), len(gone) <= 2, gone)
    check("the patch touches kernelbase/process.c and ntdll/env.c only",
          sorted(n for n, _ in patch.FILES) == ["dlls/kernelbase/process.c", "dlls/ntdll/env.c"])
    wow = (wine / "dlls/wow64/syscall.c").read_text()
    check("wow64's start-up still reads the native machine from RtlWow64GetProcessMachines (so that call must keep "
          "saying ARM64)", "RtlWow64GetProcessMachines( GetCurrentProcess(), &current_machine, &native_machine )" in wow)
    check("the patched kernelbase answer is only IsWow64Process2's out parameter",
          patched_texts["dlls/kernelbase/process.c"].count("*native_machine = IMAGE_FILE_MACHINE_AMD64;") == 1 and
          "native_machine != IMAGE_FILE_MACHINE_AMD64" in (wine / "dlls/kernelbase/memory.c").read_text())

    # 2. the two files still compile for i686
    cc = next(iter(sorted(list((root / "toolchains").glob("llvm-mingw-*/bin/i686-w64-mingw32-clang")) +
                          list((Path.home() / "madeira-bcd/toolchains").glob("llvm-mingw-*/bin/i686-w64-mingw32-clang")))), None)
    inc = wine / "build-i386/include"
    if cc and inc.is_dir():
        with tempfile.TemporaryDirectory() as t:
            for name, text in patched_texts.items():
                src = Path(t) / Path(name).name
                src.write_text(text)
                mod = Path(name).parts[1]
                r = subprocess.run([str(cc), "-fsyntax-only", "-w", "-D__WINESRC__", "-D_KERNEL32_", "-D_NTSYSTEM_",
                                    "-DWINE_NO_UNICODE_MACROS", "-I", str(wine / "dlls" / mod), "-I", str(wine / "include"),
                                    "-I", str(inc), str(src)], capture_output=True, text=True)
                check(name + " compiles for i686 (syntax)", r.returncode == 0, r.stderr[-1200:])
    else:
        print("SKIP: no i686 llvm-mingw or configured build-i386 includes to compile with")

    # 3. behaviour of the patched functions
    kb = patched_texts["dlls/kernelbase/process.c"]
    kb_snip = between(kb, "static BOOL madeira_x64_machine(void)", "    return ret;\n}\n")
    env = patched_texts["dlls/ntdll/env.c"]
    env_snip = between(env, "static BOOL madeira_x64_machine( WCHAR *env )", "buf[0] == '1';\n}\n")
    ext = ".dylib" if sys.platform == "darwin" else ".so"
    with tempfile.TemporaryDirectory() as t:
        t = Path(t)
        (t / "kb.c").write_text(KB_STUBS + kb_snip + KB_EXPORTS)
        (t / "env.c").write_text(ENV_STUBS.replace("static NTSTATUS RtlQueryEnvironmentVariable_U",
                                                    ENV_PRE + "static NTSTATUS RtlQueryEnvironmentVariable_U", 1)
                                 + env_snip + ENV_EXPORTS)
        libs = {}
        for tag in ("kb", "env"):
            r = subprocess.run(["cc", "-shared", "-fPIC", "-fshort-wchar", "-w", "-O0", str(t / (tag + ".c")), "-o",
                                str(t / (tag + ext))], capture_output=True, text=True)
            check("the extracted " + tag + " functions compile on the host", r.returncode == 0, r.stderr[-1500:])
            if r.returncode == 0:
                libs[tag] = str(t / (tag + ext))
        if "kb" in libs:
            ARM, AMD, I386 = 0xAA64, 0x8664, 0x14C

            def scenario(key, native=ARM, process=I386, status=0, null_native=False, calls=1):
                def run():
                    lib = ctypes.CDLL(libs["kb"])
                    lib.set_key.argtypes = [ctypes.c_char_p]
                    lib.call.argtypes = [ctypes.c_uint, ctypes.c_uint, ctypes.c_uint, ctypes.c_int,
                                         ctypes.POINTER(ctypes.c_uint * 2)]
                    lib.set_key(key.encode() if key is not None else None)
                    out = (ctypes.c_uint * 2)()
                    ret = None
                    for _ in range(calls):
                        ret = lib.call(native, process, status, int(null_native), ctypes.byref(out))
                    return {"ret": ret, "machine": out[0], "native": out[1], "errs": lib.err_count()}
                return fork_run(run)

            r = scenario(None)
            check("variable unset: IsWow64Process2 still says native ARM64 (process machine i386)",
                  r == {"ret": 1, "machine": I386, "native": ARM, "errs": 0}, r)
            for key in ("0", "11", "", "yes", "true"):
                r = scenario(key)
                check("variable = %r: unchanged" % key, r["native"] == ARM and r["errs"] == 0, r)
            r = scenario("1")
            check("variable = 1: native ARM64 is reported as AMD64, the process machine stays i386",
                  r == {"ret": 1, "machine": I386, "native": AMD, "errs": 1}, r)
            r = scenario("1", calls=10)
            check("the log line is limited to 4", r["errs"] == 4 and r["native"] == AMD, r)
            for native in (AMD, I386):
                r = scenario("1", native=native)
                check("variable = 1 and native %#x: unchanged" % native, r["native"] == native and r["errs"] == 0, r)
            r = scenario("1", status=0xC0000005)
            check("a failing query is passed on as it was (FALSE, outputs not rewritten)",
                  r["ret"] == 0 and r["native"] == 0x2222 and r["errs"] == 0, r)
            r = scenario("1", null_native=True)
            check("a NULL native-machine pointer is left alone", r["ret"] == 1 and r["errs"] == 0, r)
        if "env" in libs:
            def wanted(block):
                def run():
                    lib = ctypes.CDLL(libs["env"])
                    data = ("\0".join(block) + "\0\0").encode("utf-16-le")
                    buf = ctypes.create_string_buffer(data, len(data))
                    lib.wanted.argtypes = [ctypes.c_void_p]
                    return lib.wanted(ctypes.addressof(buf))
                return fork_run(run)
            check("ntdll helper: MADEIRA_X64_MACHINE=1 in the block it is given is wanted",
                  wanted(["PATH=C:\\x", "MADEIRA_X64_MACHINE=1", "A=B"]) == 1)
            for block in (["PATH=C:\\x"], ["MADEIRA_X64_MACHINE=0"], ["MADEIRA_X64_MACHINE=11"], ["MADEIRA_X64_MACHINE="],
                          ["MADEIRA_X64_MACHINE2=1"]):
                check("ntdll helper: %r is not wanted" % block[0], wanted(block) == 0)
        env_use = between(env, "RtlWow64GetProcessMachines( GetCurrentProcess(), NULL, &machine );",
                          "get_machine_name( machine, &nameW ));")
        check("set_wow64_environment swaps the machine before PROCESSOR_ARCHITEW6432 is set, only from ARM64, only with "
              "the key", "if (machine == IMAGE_FILE_MACHINE_ARM64 && madeira_x64_machine( *env ))" in env_use and
              "machine = IMAGE_FILE_MACHINE_AMD64;" in env_use and
              env_use.index("madeira_x64_machine") < env_use.index("RtlSetEnvironmentVariable"))

# 4. the workflow and the app side
wf = (root / ".github/workflows/build-ipa.yml").read_text()
apply_step = "python3 tools/patch-wine-x64-machine.py wine"
restore = "git -C wine checkout -- dlls/ntdll/env.c dlls/kernelbase/process.c"
check("the workflow checks and applies the patch, and puts the two files back", apply_step in wf and restore in wf and
      "python3 tests/host/check-x64-machine.py" in wf)
if apply_step in wf and restore in wf:
    farm = wf.index("- name: Build i386 farm (WoW64)")
    extra = wf.index("- name: Build extra Wine DLLs for arm64ec")
    check("applied before the i386 farm is built, put back before the arm64ec extra DLLs (which need an unpatched "
          "ntdll/kernelbase)", wf.index(apply_step) < farm < wf.index(restore) < extra)
    check("the put-back step runs even when the farm build failed", "if: always()" in wf[wf.index(restore) - 160:wf.index(restore)])
check("the script is named so that the i386 farm's cache key (tools/patch-wine-*.py) hashes it",
      fnmatch.fnmatch("patch-wine-x64-machine.py", "patch-wine-*.py") and "'tools/patch-wine-*.py'" in wf)
gen = (root / "build/tools/gen-config-catalog.py").read_text()
entry = gen[gen.index('"env.MADEIRA_X64_MACHINE"'):][:900]
check("the catalog documents env.MADEIRA_X64_MACHINE, off by default", '"kind": "bool", "default": "0"' in entry)
generated = (root / "app/Madeira/ConfigCatalog.generated.swift").read_text()
check("the generated catalog has it", 'ConfigOption(key: "env.MADEIRA_X64_MACHINE"' in generated and
      generated.count('key: "env.MADEIRA_X64_MACHINE"') == 1)
lst = (root / "app/Madeira/GameRecommendationsList.swift").read_text()
check("a downloaded list may set it, to 0 or 1", '"MADEIRA_X64_MACHINE"' in lst and
      'case "MADEIRA_WINTYPES", "MADEIRA_X64_MACHINE"' in lst)   # 578: the same 0/1 case also lists TINY_WINDOW_FIX
bridge = (root / "app/Madeira/WineProcessBridge.m").read_text()
check("the switch ends with the session", 'unsetenv("MADEIRA_X64_MACHINE");' in bridge)
sw = (root / "tests/host/check-session-switches.py").read_text()
check("check-session-switches names it", '"MADEIRA_X64_MACHINE"' in sw)
rules = (root / "build/tools/autotune_rules.py").read_text()
rid = rules.find('"id": "x64-machine-arm64-launcher"')
block = rules[rid:rid + 3200]
check("auto-tune adds the line after a native ARM64 image fails its imports with c0000135, once",
      rid >= 0 and '"lines": ["env.MADEIRA_X64_MACHINE = 1"]' in block and "failed, status c0000135" in block and
      '"noRetry": True' in block and '[x64-machine] IsWow64Process2' in block)
check("its fixture exists and proposes the line",
      '"env.MADEIRA_X64_MACHINE = 1"' in (root / "tests/fixtures/autotune/beamng-arm64-launcher-2026-10-21.expect").read_text())
recs = json.loads((root / "recommended/recommendations.json").read_text())
bn = [r for r in recs["rules"] if r["id"] == "beamng-drive"]
check("BeamNG.drive (Steam 284160) is recommended the line from build 577, and only that", len(bn) == 1 and
      bn[0]["match"]["steamAppIDs"] == [284160] and
      {"line": "env.MADEIRA_X64_MACHINE = 1", "minBuild": 577} in bn[0]["config"] and
      "switches" not in bn[0] and
      [c for c in bn[0]["config"] if isinstance(c, str) and not c.startswith("#")] == ["env.MADEIRA_EC_HOOK_TRACE = 0"])
sys.exit(0 if ok else 1)

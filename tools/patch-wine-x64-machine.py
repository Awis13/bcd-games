#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""env.MADEIRA_X64_MACHINE = 1 (opt-in): a 32-bit program asks "which machine is native?" and hears AMD64 (madeira-bcd).

BeamNG.drive's launcher (BeamNG.drive.exe, a 32-bit x86 program) shows "Current mode: native ARM (click to change)"
on an ARM machine and then starts BinArm64\\BeamNG.drive.arm64.exe, a native ARM64 Windows image. BCD's native
ARM64 set (app/Madeira/aarch64-windows) has no d3dcompiler_47.dll, so the loader stops with c000007b and the launcher
reports STATUS_DLL_NOT_FOUND (SYDJDY, DZ6BN4, VV1FQ4 and PEVMEQ, iPhone16,2 on iOS 27.2: every time). The same
launcher on iPhone18,2 (SRWY27) started Bin64\\BeamNG.drive.x64.exe, the emulated x64 game. Nothing in the logs says
what makes the launcher choose; the label proves it knows the machine is ARM. Wine already answers
GetNativeSystemInfo with AMD64 for a WoW64 process on a non-AMD64 host (kernelbase/memory.c), so what is left that
tells a 32-bit program the host is ARM64 is IsWow64Process2 (its native-machine out parameter) and the
PROCESSOR_ARCHITEW6432 environment variable. With env.MADEIRA_X64_MACHINE = 1 in the session's environment:

  kernelbase IsWow64Process2    reports AMD64 where it would report ARM64 as the native machine, and logs
                                "[x64-machine] IsWow64Process2 ..." (the first four calls), so the next log shows
                                whether the launcher asked at all;
  ntdll set_wow64_environment   sets PROCESSOR_ARCHITEW6432 = AMD64 instead of ARM64 for a child process.

Only these two answers an application can see are changed. The query below them (NtQuerySystemInformation
SystemSupportedProcessorArchitectures, RtlWow64GetProcessMachines) is untouched: WoW64's own start-up reads it to pick
the CPU backend and the native machine type, so it must keep saying ARM64. Without the variable, or with any value
but 1, nothing changes. Built into the i386 farm only (a 64-bit program, and the arm64ec DLLs, keep the old answer).

Usage: patch-wine-x64-machine.py <wine source dir>. Idempotent; fails by name if an anchor moves.
"""
import pathlib
import sys

MARKER = "madeira-bcd x64-machine"

# ---- kernelbase/process.c
KB_OLD = """BOOL WINAPI DECLSPEC_HOTPATCH IsWow64Process2( HANDLE process, USHORT *machine, USHORT *native_machine )
{
    return set_ntstatus( RtlWow64GetProcessMachines( process, machine, native_machine ));
}
"""
KB_NEW = """/* """ + MARKER + """ (tools/patch-wine-x64-machine.py): env MADEIRA_X64_MACHINE = 1 makes the native machine
 * an application sees AMD64 instead of ARM64 (BeamNG.drive's launcher picks its native ARM64 build otherwise).
 * Nothing changes without it. */
static BOOL madeira_x64_machine(void)
{
    static LONG state;    /* 0 not read yet, 1 off, 2 on */
    WCHAR buf[4];
    LONG s = state;

    if (!s)
    {
        DWORD len = GetEnvironmentVariableW( L"MADEIRA_X64_MACHINE", buf, ARRAY_SIZE(buf) );
        s = (len == 1 && buf[0] == '1') ? 2 : 1;
        state = s;
    }
    return s == 2;
}

BOOL WINAPI DECLSPEC_HOTPATCH IsWow64Process2( HANDLE process, USHORT *machine, USHORT *native_machine )
{
    BOOL ret = set_ntstatus( RtlWow64GetProcessMachines( process, machine, native_machine ));

    if (ret && native_machine && *native_machine == IMAGE_FILE_MACHINE_ARM64 && madeira_x64_machine())
    {
        static LONG logged;
        if (InterlockedIncrement( &logged ) <= 4)
            ERR( "[x64-machine] IsWow64Process2(process=%p) machine=%04x: native ARM64 reported as AMD64 "
                 "(env.MADEIRA_X64_MACHINE = 1)\\n", process, machine ? *machine : 0 );
        *native_machine = IMAGE_FILE_MACHINE_AMD64;
    }
    return ret;
}
"""

# ---- ntdll/env.c
ENV_HEAD_OLD = """/***********************************************************************
 *           set_wow64_environment
"""
ENV_HEAD_NEW = """/* """ + MARKER + """ (tools/patch-wine-x64-machine.py): is env.MADEIRA_X64_MACHINE = 1 in this environment? */
static BOOL madeira_x64_machine( WCHAR *env )
{
    UNICODE_STRING nameW = RTL_CONSTANT_STRING( L"MADEIRA_X64_MACHINE" );
    WCHAR buf[4];
    UNICODE_STRING valW = { 0, sizeof(buf), buf };

    return !RtlQueryEnvironmentVariable_U( env, &nameW, &valW ) && valW.Length == sizeof(WCHAR) && buf[0] == '1';
}

""" + ENV_HEAD_OLD

ENV_OLD = """        RtlWow64GetProcessMachines( GetCurrentProcess(), NULL, &machine );
        RtlSetEnvironmentVariable( env, &arch6432_strW, get_machine_name( machine, &nameW ));
"""
ENV_NEW = """        RtlWow64GetProcessMachines( GetCurrentProcess(), NULL, &machine );
        if (machine == IMAGE_FILE_MACHINE_ARM64 && madeira_x64_machine( *env ))   /* """ + MARKER + """ */
            machine = IMAGE_FILE_MACHINE_AMD64;
        RtlSetEnvironmentVariable( env, &arch6432_strW, get_machine_name( machine, &nameW ));
"""

FILES = (
    ("dlls/kernelbase/process.c", ((KB_OLD, KB_NEW),)),
    ("dlls/ntdll/env.c", ((ENV_HEAD_OLD, ENV_HEAD_NEW), (ENV_OLD, ENV_NEW))),
)


def patch(source, edits):
    """The patched text of one file; raises ValueError naming the anchor that moved."""
    if MARKER in source:
        return source
    for old, _ in edits:
        if source.count(old) != 1:
            raise ValueError("x64-machine: anchor missing or ambiguous: " + old.strip().splitlines()[0])
    for old, new in edits:
        source = source.replace(old, new)
    return source


def main():
    wine = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "wine")
    done = []
    results = []
    for name, edits in FILES:
        path = wine / name
        source = path.read_text()
        try:
            results.append((path, source, patch(source, edits)))
        except ValueError as error:
            sys.exit(str(error))
    for path, source, result in results:
        if result != source:
            path.write_text(result)
        done.append(path.name + (": patched" if result != source else ": already patched"))
    print(", ".join(done))
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright 2026 mckenna654 (the C code that this script writes into Wine: thebcd69/bcd-wine#1 and thebcd69/bcd-games#4, signed off,
#   LGPL-2.1-or-later; used under the ordinary GPL as LGPL-2.1 section 3 permits, like the rest of the Wine copy)
# Copyright 2026 thebcd69 (BCD) (this script and the changes to that C code)
# Modified by BCD (thebcd69), 2026-10-24: ported behind the opt-in ntdll-xsec.dll variant (anchored edits, executable regions only, marker, verify).
# Not BCD Material: LICENSE-BCD.md section 1 keeps the other author's header and licence on a file that mixes in their text.
"""madeira-bcd: ntdll-xsec.dll, Wine's PE ntdll with executable section views and two code-probe recoveries (opt-in).

Based on thebcd69/bcd-games#4 and thebcd69/bcd-wine#1 by mckenna654 (Diablo II: Resurrected).

The PE ntdll that ships (app/Madeira/arm64ec-windows/ntdll.dll) is a tracked binary: BCD's CI does not compile it into the IPA, it only
proves, for ntdll-sync.dll, that its tree rebuilds it byte for byte. Three things in dlls/ntdll/signal_arm64ec.c are wrong for a program
like D2R's loader, and none of them is reachable without a different ntdll:
  1. notify_map_view_of_section tells the emulator about IMAGE sections only. D2R's loader maps ONE pagefile section twice (a write
     view and a run view) and jumps into the run view; the emulator (FEX) only runs code from ranges its interval tracker holds as
     executable, and that tracker is fed by NtProtectVirtualMemory, NtAllocateVirtualMemory and image maps, never by an executable
     view of a data section ("[map-notify] SKIP (no ArbitraryUserPointer)", then "[iOS-xquery] MISS", NoExec, c0000005). The change
     tells the emulator, right after the map and before the loader-name gate, about every committed region of a non-image section
     whose protection is executable ("[section-notify]"). Image sections keep the old path unchanged. The unix side keeps the
     view's shared backing with env.MADEIRA_SHARED_SECTION_EXEC = 1 (build/ntdll-unix/virtual_ios.c, ios_guest_section_is_host_data).
  2. arm64x_check_call (the native check before a call from ARM64EC code) reads the first bytes of a non-EC target to recognise
     optional fast-forward, syscall and jump sequences. When the target page is not readable (a guest page made PAGE_NOACCESS) that
     read faults in native code, and the guest's exception handler receives the NATIVE pc (which can resume it in the middle of an x86
     instruction). The change completes the exit-thunk selection of .Lexit for a read access violation inside the labelled probe
     ("[ec-probe-fallback]"); the emulator then runs the call and the guest gets its own fault at the guest address.
  3. The same for the emulator's ExitToX64 (xtajit64.dll), whose first instructions read the target to recognise the same sequences:
     a read access violation at one of its three target loads continues at the exported DispatchJump (the ordinary entry: the same
     link-register push and the same emulator entry, without the probe) ("[ec-emulator-probe-fallback]"). Only with
     env.MADEIRA_D2R_PROBE_RECOVERY = 1 and only when the instructions at the export are exactly the prefix this was written for
     (tests/host/check-ntdll-xsec.py compares them with the shipped xtajit64.dll).
Anything else is as before: other exception codes, writes, instruction fetches, guard pages, a pc outside the probes, a fault in
translated code, an unreadable ntdll state. The change is compiled into ntdll-xsec.dll only, which ships NEXT TO the untouched
ntdll.dll (tools/build-wine-extra-dlls.sh builds it after checking, as for ntdll-sync.dll, that the unpatched tree rebuilds the shipped
ntdll.dll byte for byte apart from its time stamps). build/ntdll-unix/loader_ios.c maps it only with env.MADEIRA_NTDLL_XSEC = 1, and
app/Madeira/WineProcessBridge.m links system32\ntdll.dll to the same file.

Usage:
  patch-wine-ntdll-xsec.py WINE_SRC              patch dlls/ntdll/signal_arm64ec.c (idempotent; a moved anchor fails loudly)
  patch-wine-ntdll-xsec.py --verify DLL [STOCK]  check a built ntdll-xsec.dll (ARM64EC image, marker and log strings present,
      RtlPcToFileHeader's trampoline still fits after its section; with STOCK, the export names are exactly the shipped ntdll.dll's)
"""
import importlib.util
import os
import struct
import sys

MARK = "madeira-bcd ntdll-xsec"
FILE = os.path.join("dlls", "ntdll", "signal_arm64ec.c")
MARKER = b"madeira-ntdll-xsec v1"
LOG_STRINGS = (b"[section-notify]", b"[ec-probe-fallback]", b"[ec-emulator-probe-fallback]")
# the linker's x64 thunk for an ARM64EC export: mov rax,rsp; mov [rax+20h],rbx; push rbp; pop rbp; jmp rel32
FFS = bytes.fromhex("488bc448895820555de9")
# the bytes from the probe's `ldr x16, [x11]` to .Lexit in arm64x_check_call (40 instructions)
EXIT_AFTER_BEGIN = 0xa0

# (name, old text that occurs exactly once in the stock file, new text)
EDITS = [
    ('DECLS',
     r'''static void     (WINAPI *pBeginSimulation)(void);
''',
     r'''static void     (WINAPI *pBeginSimulation)(void);
/* Native dispatcher exports, retained before hybrid pointer redirection. */
static void *ios_exit_to_x64, *ios_dispatch_jump;
/* madeira-bcd ntdll-xsec (tools/patch-wine-ntdll-xsec.py), from thebcd69/bcd-wine#1 by mckenna654 (modified 2026-10-24):
 * what --verify looks for in a built ntdll-xsec.dll. */
static const char madeira_ntdll_xsec_marker[] __attribute__((used)) = "madeira-ntdll-xsec v1";
'''),
    ('EXPORTS',
     r'''    __os_arm64x_dispatch_ret = RtlFindExportedRoutineByName( module, "RetToEntryThunk" );
''',
     r'''    __os_arm64x_dispatch_ret = RtlFindExportedRoutineByName( module, "RetToEntryThunk" );
    ios_exit_to_x64 = __os_arm64x_dispatch_call_no_redirect;
    ios_dispatch_jump = __os_arm64x_dispatch_fptr;
'''),
    ('NOTIFY',
     r'''static void notify_map_view_of_section( HANDLE handle, void *addr, SIZE_T size, ULONG alloc,
                                        ULONG protect, NTSTATUS *ret_status )
{
    SECTION_IMAGE_INFORMATION info;
    NTSTATUS status;

''',
     r'''/* Non-image sections can contain generated guest code too. Register only
 * committed regions (SEC_RESERVE may have holes), using their actual page
 * protections. The image callback parses PE headers and cannot handle these
 * mappings. Keep callbacks paired and inside the caller's syscall guard.
 *
 * madeira-bcd ntdll-xsec (modified 2026-10-24): only regions that are executable are
 * told to the emulator. A non-executable region cannot make a range executable, and
 * the emulator takes its interval lock for every notification; this DLL maps the
 * large data files of a game too. */
static void notify_mapped_section_protection( void *addr, SIZE_T size )
{
    MEMORY_BASIC_INFORMATION info;
    char *cursor = addr;
    SIZE_T remaining = size, length;

    TRACE( "mapped-section protections addr=%p size=%Iu\n", addr, size );
    if (!pNotifyMemoryProtect) return;
    while (remaining)
    {
        if (NtQueryVirtualMemory( GetCurrentProcess(), cursor, MemoryBasicInformation,
                                  &info, sizeof(info), NULL )) break;
        if ((char *)info.BaseAddress > cursor ||
            (SIZE_T)(cursor - (char *)info.BaseAddress) >= info.RegionSize) break;
        length = min( remaining, info.RegionSize - (SIZE_T)(cursor - (char *)info.BaseAddress) );
        if (info.State == MEM_COMMIT &&
            (info.Protect & (PAGE_EXECUTE | PAGE_EXECUTE_READ | PAGE_EXECUTE_READWRITE | PAGE_EXECUTE_WRITECOPY)))
        {
            static LONG section_n;
            if (InterlockedIncrement( &section_n ) <= 16)
                ERR( "[section-notify] %p size=%p prot=%x: executable view of a non-image section told to the emulator\n",
                     cursor, (void *)length, (unsigned int)info.Protect );
            pNotifyMemoryProtect( cursor, length, info.Protect, FALSE, 0 );
            pNotifyMemoryProtect( cursor, length, info.Protect, TRUE, STATUS_SUCCESS );
        }
        cursor += length;
        remaining -= length;
    }
}

static void notify_map_view_of_section( HANDLE handle, void *addr, SIZE_T size, ULONG alloc,
                                        ULONG protect, NTSTATUS *ret_status )
{
    SECTION_IMAGE_INFORMATION info;
    SECTION_BASIC_INFORMATION basic;
    NTSTATUS status;

    /* Do this before the loader-name gate: ordinary section mappings do not
     * have ArbitraryUserPointer. Both NtMapViewOfSection variants reach here
     * only after a successful map into the current process. */
    if (!NtQuerySection( handle, SectionBasicInformation, &basic, sizeof(basic), NULL ) &&
        !(basic.Attributes & SEC_IMAGE))
    {
        notify_mapped_section_protection( addr, size );
        return;
    }

'''),
    ('RECOVER',
     r'''static void * __attribute__((used)) prepare_exception_arm64ec( EXCEPTION_RECORD *rec, ARM64EC_NT_CONTEXT *context, ARM64_NT_CONTEXT *arm_ctx )
{
    if (rec->ExceptionCode == STATUS_EMULATION_SYSCALL) dispatch_syscall( arm_ctx );
''',
     r'''/* madeira-bcd ntdll-xsec (tools/patch-wine-ntdll-xsec.py): the two recoveries below are thebcd69/bcd-wine#1 by mckenna654, applied
 * as they are (modified 2026-10-24: ported behind the opt-in ntdll-xsec.dll variant; nothing here is in the shipped ntdll.dll). */
extern char arm64x_check_call_probe_begin[], arm64x_check_call_probe_end[];

/* The check-call code reads a non-EC destination to recognise optional x64
 * fast-forward, syscall and indirect-jump sequences. An inaccessible target
 * must fault in the emulator, rather than expose this native probe's PC to
 * an x64 exception hook (which can resume it at an x86 instruction boundary).
 * Finish the same exit-thunk selection as .Lexit, without changing the page
 * protection or delivering/suppressing the eventual guest exception.
 *
 * Only the iOS native probe path is eligible. In particular, a fault in
 * translated code, a bitmap lookup or an unrelated native function is not a
 * failed optional probe. Labels keep the range independent of image layout. */
static BOOL recover_arm64x_code_probe( EXCEPTION_RECORD *rec, ARM64_NT_CONTEXT *ctx )
{
    ULONG_PTR pc, begin, end;

    if (!p_ios_jit_reverse_translate_addr || !pBeginSimulation ||
        !get_arm64ec_cpu_area() || get_arm64ec_cpu_area()->InSimulation ||
        rec->ExceptionCode != STATUS_ACCESS_VIOLATION ||
        rec->NumberParameters != 2 || rec->ExceptionInformation[0] != 0 ||
        !ctx->Lr || (ctx->Lr & 3) || !ctx->X10 || (ctx->X10 & 3))
        return FALSE;

    pc = (ULONG_PTR)xlate_ios_jit_rev( (void *)(ULONG_PTR)ctx->Pc );
    begin = (ULONG_PTR)xlate_ios_jit_rev( arm64x_check_call_probe_begin );
    end = (ULONG_PTR)xlate_ios_jit_rev( arm64x_check_call_probe_end );
    if (!begin || end <= begin || pc < begin || pc >= end || (pc & 3)) return FALSE;

    ctx->X9 = ctx->X11;
    ctx->X11 = ctx->X10;
    ctx->Pc = ctx->Lr;
    return TRUE;
}

static BOOL emulator_probe_recovery_enabled(void)
{
    UNICODE_STRING name, value;
    WCHAR buffer[2];

    RtlInitUnicodeString( &name, L"MADEIRA_D2R_PROBE_RECOVERY" );
    value.Buffer = buffer;
    value.Length = 0;
    value.MaximumLength = sizeof(buffer);
    return !RtlQueryEnvironmentVariable_U( NULL, &name, &value ) &&
           value.Length == sizeof(WCHAR) && buffer[0] == '1';
}

/* The iOS emulator's ExitToX64 also inspects a destination before entering
 * emulation, to recognise an optional fast-forward sequence. A failed read
 * there must take the ordinary exported DispatchJump path. Both dispatchers
 * receive the target in X9 and push LR exactly once; none of the argument,
 * stack or nonvolatile registers have changed at these three reads.
 *
 * This is an integration guard for the known dispatcher ABI, not a generic
 * native-fault recovery. Validate the entire probe prefix at its export,
 * recognise only its target reads, and decline unknown emulator versions.
 * In particular, bitmap and alias-table faults must remain exceptions. */
static BOOL recover_emulator_code_probe( EXCEPTION_RECORD *rec, ARM64_NT_CONTEXT *ctx )
{
    static const ULONG prefix[] =
    {
        0xf9400130, /* ldr x16,[x9] */
        0xd2916911, 0xf2a91891, 0xf2cb1131, 0xf2eaa411, /* FFS pattern in x17 */
        0xeb11021f, /* cmp x16,x17 */
        0x54000001, /* b.ne ordinary entry (displacement checked separately) */
        0x79401130, /* ldrh w16,[x9,#8] */
        0x529d2bb1, /* mov w17,#0xe95d */
        0x6b11021f, /* cmp w16,w17 */
        0x54000001, /* b.ne same ordinary entry */
        0xb880a130  /* ldursw x16,[x9,#10] */
    };
    const ULONG *code;
    ULONG_PTR pc, entry, jump, offset, length;
    unsigned int i;

    if (!p_ios_jit_reverse_translate_addr || !pBeginSimulation ||
        !emulator_probe_recovery_enabled() ||
        !ios_exit_to_x64 || !ios_dispatch_jump ||
        !get_arm64ec_cpu_area() || get_arm64ec_cpu_area()->InSimulation ||
        rec->ExceptionCode != STATUS_ACCESS_VIOLATION || rec->NumberParameters != 2 ||
        rec->ExceptionInformation[0] != 0 || !ctx->Lr || (ctx->Lr & 3) ||
        ctx->X9 >> 39 || RtlIsEcCode( ctx->X9 ))
        return FALSE;

    pc = (ULONG_PTR)xlate_ios_jit_rev( (void *)(ULONG_PTR)ctx->Pc );
    entry = (ULONG_PTR)xlate_ios_jit_rev( ios_exit_to_x64 );
    jump = (ULONG_PTR)xlate_ios_jit( ios_dispatch_jump );
    if (!entry || (entry & 3) || !jump || (jump & 3)) return FALSE;

    if (pc == entry) { offset = 0; length = 8; }
    else if (pc == entry + 7 * sizeof(ULONG)) { offset = 8; length = 2; }
    else if (pc == entry + 11 * sizeof(ULONG)) { offset = 10; length = 4; }
    else return FALSE;
    if (rec->ExceptionInformation[1] - ctx->X9 - offset >= length) return FALSE;

    code = (const ULONG *)entry;
    for (i = 0; i < ARRAY_SIZE(prefix); i++)
    {
        ULONG mask = (i == 6 || i == 10) ? 0xff00001f : ~0u;
        if ((code[i] & mask) != prefix[i]) return FALSE;
    }
    /* Both forward mismatch branches must select the same fallback, after
     * the inspected prefix and before the branch-immediate sign bit. */
    if ((code[6] & 0x00800000) || (code[10] & 0x00800000) ||
        6 + ((code[6] >> 5) & 0x7ffff) != 10 + ((code[10] >> 5) & 0x7ffff) ||
        6 + ((code[6] >> 5) & 0x7ffff) < ARRAY_SIZE(prefix)) return FALSE;

    /* Preserve the target, LR, SP, arguments and flags. DispatchJump performs
     * the existing stack push and emulator entry; the page stays protected. */
    ctx->Pc = jump;
    return TRUE;
}

static void * __attribute__((used)) prepare_exception_arm64ec( EXCEPTION_RECORD *rec, ARM64EC_NT_CONTEXT *context, ARM64_NT_CONTEXT *arm_ctx )
{
    if (rec->ExceptionCode == STATUS_ACCESS_VIOLATION)
    {
        ARM64_NT_CONTEXT probe_ctx = *arm_ctx;
        if (recover_arm64x_code_probe( rec, &probe_ctx ))
        {
            static LONG probe_n;
            if (InterlockedIncrement( &probe_n ) <= 16)
                ERR( "[ec-probe-fallback] native probe %p target %p unreadable; returning to x64 exit thunk\n",
                     (void *)(ULONG_PTR)arm_ctx->Pc, (void *)(ULONG_PTR)arm_ctx->X11 );
            /* Use the native syscall: NtContinue's public EC wrapper converts an
             * x64 context. Leave the original context intact if continuation fails. */
            syscall_NtContinue( &probe_ctx, FALSE );
        }
        probe_ctx = *arm_ctx;
        if (recover_emulator_code_probe( rec, &probe_ctx ))
        {
            static LONG emulator_probe_n;
            if (InterlockedIncrement( &emulator_probe_n ) <= 16)
                ERR( "[ec-emulator-probe-fallback] native probe %p target %p unreadable; entering dispatcher\n",
                     (void *)(ULONG_PTR)arm_ctx->Pc, (void *)(ULONG_PTR)arm_ctx->X9 );
            syscall_NtContinue( &probe_ctx, FALSE );
        }
    }
    if (rec->ExceptionCode == STATUS_EMULATION_SYSCALL) dispatch_syscall( arm_ctx );
'''),
    ('LABEL_BEGIN',
     r'''         "b.ne .Ljmp\n\t"
         "ldr x16, [x11]\n\t"               /* dest code */
''',
     r'''         "b.ne .Ljmp\n\t"
         ".globl arm64x_check_call_probe_begin\n"
         "arm64x_check_call_probe_begin:\n\t"
         "ldr x16, [x11]\n\t"               /* dest code */
'''),
    ('LABEL_END',
     r'''         /* not a special sequence, call the exit thunk */
         ".Lexit:\n\t"
''',
     r'''         /* not a special sequence, call the exit thunk */
         ".globl arm64x_check_call_probe_end\n"
         "arm64x_check_call_probe_end:\n\t"
         ".Lexit:\n\t"
'''),
]


def apply_text(text):
    """The patched text of signal_arm64ec.c; SystemExit when an anchor has moved."""
    if MARK in text:
        return text
    for name, old, new in EDITS:
        if text.count(old) != 1:
            sys.exit("patch-wine-ntdll-xsec: anchor %s occurs %d times in %s (expected 1); the pin moved, update the script"
                     % (name, text.count(old), FILE))
        text = text.replace(old, new)
    return text


def patch(src):
    path = os.path.join(src, FILE)
    with open(path) as f:
        text = f.read()
    new = apply_text(text)
    if new == text:
        print("patch-wine-ntdll-xsec: %s already carries the change" % path)
        return
    with open(path, "w") as f:
        f.write(new)
    print("patch-wine-ntdll-xsec: %s patched (%d edits)" % (path, len(EDITS)))


def load_pe():
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "patch-wine-ntdll-sync.py")
    spec = importlib.util.spec_from_file_location("ntdllsync_pe", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.Pe


def verify(path, stock=None):
    Pe = load_pe()
    img = Pe(path)
    bad = []
    exps = img.exports()
    ranges = img.code_map()
    # an ARM64EC image says AMD64 in its header and carries a CHPE code map
    if img.machine != 0x8664 or not any(k == 1 for s, l, k in ranges):
        bad.append("not an ARM64EC image (machine %#x, code map %s)" % (img.machine, ranges))
    if MARKER not in img.d:
        bad.append("marker %r missing" % MARKER.decode())
    for s in LOG_STRINGS:
        if s not in img.d:
            bad.append("log string %r missing" % s.decode())
    # The code-probe recovery in arm64x_check_call rests on two labels in its assembly: probe_begin at the `ldr x16, [x11]` that reads the
    # target (tst x11,#15; b.ne; ldr), probe_end at .Lexit (mov x9,x11; mov x11,x10; ret), 0xa0 bytes later. The labels are not exported, so
    # this finds both instruction sequences in the code and requires each once, exactly 0xa0 bytes apart (the shipped ntdll.dll has them so).
    words = struct.unpack_from("<%dI" % (len(img.d) // 4), img.d, 0)
    begins = [i * 4 for i in range(len(words) - 2)
              if words[i] == 0xf2400d7f and (words[i + 1] & 0xff00001f) == 0x54000001 and words[i + 2] == 0xf9400170]
    exits = [i * 4 for i in range(len(words) - 2) if words[i:i + 3] == (0xaa0b03e9, 0xaa0a03eb, 0xd65f03c0)]
    if len(begins) != 1 or len(exits) != 1:
        bad.append("probe sequences: %d `tst x11,#15; b.ne; ldr x16,[x11]` and %d `.Lexit` triples (one of each expected)" % (len(begins), len(exits)))
    elif exits[0] - (begins[0] + 8) != EXIT_AFTER_BEGIN or (words[exits[0] // 4 - 1] >> 26) != 5:
        bad.append("the probe `ldr x16,[x11]` at file %#x and .Lexit at %#x are %#x bytes apart (%#x expected), or no branch precedes .Lexit"
                   % (begins[0] + 8, exits[0], exits[0] - (begins[0] + 8), EXIT_AFTER_BEGIN))
    # ios_patch_rtl_pc_to_file_header (virtual_ios.c) puts a 48-byte trampoline 16 bytes past the 16-aligned end of the
    # section that holds the function, inside that section's last 16 KB page
    pc2fh = exps.get("RtlPcToFileHeader")
    if pc2fh is not None:
        o = img.off(pc2fh)   # the export is the fast-forward sequence; follow its jmp
        if img.d[o:o + 10] == FFS:
            pc2fh = pc2fh + 14 + struct.unpack_from("<i", img.d, o + 10)[0]
        else:
            pc2fh = None
    sec = img.section_of(pc2fh) if pc2fh is not None else None
    if not sec:
        bad.append("RtlPcToFileHeader: not found")
    else:
        lo = ((sec[1] + sec[2] + 15) & ~15) + 16
        hi = (sec[1] + sec[2] + 0x3fff) & ~0x3fff
        nxt = min([s[1] for s in img.secs if s[1] > sec[1]] + [img.size_of_image])
        if hi > nxt:
            hi = nxt
        if lo + 48 > hi:
            bad.append("no room for the RtlPcToFileHeader trampoline after %s (%#x..%#x)" % (sec[0], lo, hi))
    if stock:
        ref = Pe(stock).exports()
        extra = sorted(set(exps) - set(ref))
        missing = sorted(set(ref) - set(exps))
        if missing or extra:
            bad.append("exports differ from %s: missing %s, extra %s" % (stock, missing[:8], extra[:8]))
    if bad:
        sys.exit("verify %s:\n  %s" % (path, "\n  ".join(bad)))
    print("verify %s: ARM64EC image, marker and the three log strings present, RtlPcToFileHeader's trampoline fits%s"
          % (path, ", exports = the stock set" if stock else ""))


if __name__ == "__main__":
    if len(sys.argv) in (3, 4) and sys.argv[1] == "--verify":
        verify(sys.argv[2], sys.argv[3] if len(sys.argv) == 4 else None)
    elif len(sys.argv) == 2 and not sys.argv[1].startswith("-"):
        patch(sys.argv[1])
    else:
        sys.exit(__doc__)

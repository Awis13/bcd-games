#!/usr/bin/env python3
"""Record bounded, per-thread x64 exits in the ARM64EC Launcher build.

Keep the pinned submodule intact. The native reader obtains the appended frame
layout from a DATA export; it never calls an ARM64EC PE export from Mach-O.
Only Launcher.exe emits the recorder, with MADEIRA_FEX_BRANCH_HISTORY=0 as
an opt-out. No guest branch, instruction or exception is redirected.
"""
from pathlib import Path
import sys

MARKER = "madeira-bcd: guest branch history v1"
FRAME = "FEXCore/include/FEXCore/Core/CoreState.h"
BRANCH = "FEXCore/Source/Interface/Core/JIT/BranchOps.cpp"
MODULE = "Source/Windows/ARM64EC/Module.cpp"
EXPORT = "Source/Windows/ARM64EC/libarm64ecfex.def"

FRAME_ADD = '''
  /* madeira-bcd: guest branch history v1. Appended after all existing fields;
   * no implicit TLS, and no architectural or prebuilt ntdll offsets move. */
  struct IosGuestBranchHistoryType {
    uint64_t Magic {0x314744454742444dULL};
    uint64_t Serial {};
    struct Edge { uint64_t Source, Target, Block, Hint; } Edges[8] {};
  } IosGuestBranchHistory {};
'''
FRAME_DECL = '''
#if defined(FEX_IOS_HOST) && defined(ARCHITECTURE_arm64ec)
extern uint32_t IosGuestBranchTraceEnabled;
static_assert(sizeof(CpuStateFrame::IosGuestBranchHistoryType) == 272);
static_assert(offsetof(CpuStateFrame, IosGuestBranchHistory) % 8 == 0);
static_assert(offsetof(CpuStateFrame, IosGuestBranchHistory) + 272 <= 32760);
#endif
'''
BRANCH_ADD = '''
#if defined(FEX_IOS_HOST) && defined(ARCHITECTURE_arm64ec)
  /* madeira-bcd: guest branch history v1. Executed exits, not a stack scan.
   * Reserved TMP1-3 only; ADD/AND/loads/stores leave guest NZCV untouched.
   * Save the real target and the last mapped guest opcode; commit Serial last. */
  if (FEXCore::Core::IosGuestBranchTraceEnabled) {
    using Frame = FEXCore::Core::CpuStateFrame;
    constexpr auto History = offsetof(Frame, IosGuestBranchHistory);
    constexpr auto Edges = History + offsetof(Frame::IosGuestBranchHistoryType, Edges);
    ldr(TMP2, STATE, History + 8);
    and_(ARMEmitter::Size::i64Bit, TMP3, TMP2, 7);
    add(TMP3, STATE, TMP3, ARMEmitter::ShiftType::LSL, 5);
    uint64_t Target;
    if (IsInlineConstant(Op->NewRIP, &Target) || IsInlineEntrypointOffset(Op->NewRIP, &Target)) {
      InsertGuestRIPMove(TMP1, Target);
      str(TMP1, TMP3, Edges + 8);
    } else {
      str(GetReg(Op->NewRIP).X(), TMP3, Edges + 8);
    }
    const uint64_t Source = DebugData->GuestOpcodes.empty() ? 0 : Entry + DebugData->GuestOpcodes.back().GuestEntryOffset;
    InsertGuestRIPMove(TMP1, Source);
    str(TMP1, TMP3, Edges);
    ldr(TMP1, STATE, offsetof(Frame, State.InlineJITBlockHeader));
    str(TMP1, TMP3, Edges + 16);
    movz(ARMEmitter::Size::i64Bit, TMP1, static_cast<uint64_t>(Op->Hint));
    str(TMP1, TMP3, Edges + 24);
    add(ARMEmitter::Size::i64Bit, TMP2, TMP2, 1);
    str(TMP2, STATE, History + 8);
  }
#endif
'''
MODULE_ADD = '''
#ifdef FEX_IOS_HOST
/* madeira-bcd: guest branch history v1. DATA, never a native-callable thunk. */
/* Keep a word-sized global, matching the module's iOS relocation guards. */
namespace FEXCore::Core { uint32_t IosGuestBranchTraceEnabled = 0; }
extern "C" const uint64_t IosGuestBranchTraceLayout[3] = {
  0x314744454742444dULL,
  offsetof(FEXCore::Core::CpuStateFrame, IosGuestBranchHistory) |
    (static_cast<uint64_t>(sizeof(FEXCore::Core::CpuStateFrame)) << 32),
  sizeof(FEXCore::Core::CpuStateFrame::IosGuestBranchHistoryType) | (8ULL << 32),
};
#endif
'''
MODULE_INIT = '''
#ifdef FEX_IOS_HOST
  /* Recorder emitted only for this executable, before CTX/JIT creation. */
  const auto HistoryName = fextl::string {ExecutableName};
  const char* HistoryOpt = getenv("MADEIRA_FEX_BRANCH_HISTORY");
  FEXCore::Core::IosGuestBranchTraceEnabled =
    _stricmp(HistoryName.c_str(), "Launcher.exe") == 0 && !(HistoryOpt && HistoryOpt[0] == '0');
  LogMan::Msg::IFmt("FEX: branch-history-v1 madeira-bcd enabled={} exe={}",
                   FEXCore::Core::IosGuestBranchTraceEnabled != 0, ExecutableName);
#endif
'''

EDITS = {
    FRAME: (
        ("  uint64_t IosLastCallbackLR {};\n};", "  uint64_t IosLastCallbackLR {};\n" + FRAME_ADD + "};"),
        ('static_assert(offsetof(CpuStateFrame, State) == 0, "CPUState must be first member in CpuStateFrame");',
         FRAME_DECL + 'static_assert(offsetof(CpuStateFrame, State) == 0, "CPUState must be first member in CpuStateFrame");'),
    ),
    BRANCH: (
        ('#include "Interface/Core/JIT/JITClass.h"', '#include "Interface/Core/JIT/JITClass.h"\n#include "Interface/Core/JIT/DebugData.h"'),
        ('  auto Op = IROp->C<IR::IROp_ExitFunction>();\n\n  ResetStack();',
         '  auto Op = IROp->C<IR::IROp_ExitFunction>();\n' + BRANCH_ADD + '\n  ResetStack();'),
    ),
    MODULE: (
        ('NTSTATUS ProcessInit() {', MODULE_ADD + '\nNTSTATUS ProcessInit() {'),
        ('  FEX::Windows::Logging::Init();', '  FEX::Windows::Logging::Init();\n' + MODULE_INIT),
    ),
    EXPORT: (('  IosXpFex DATA', '  IosXpFex DATA\n  ; madeira-bcd: guest branch history v1\n  IosGuestBranchTraceLayout DATA'),),
}


def patch(sources):
    results = {}
    for name, edits in EDITS.items():
        source = sources[name]
        if MARKER in source:
            if not all(source.count(after) == 1 for _, after in edits):
                raise ValueError("partial or changed overlay: " + name)
            results[name] = source
            continue
        for before, _ in edits:
            if source.count(before) != 1:
                raise ValueError("anchor changed: " + name + ": " + before[:80])
        for before, after in edits:
            source = source.replace(before, after, 1)
        results[name] = source
    return results


if __name__ == "__main__":
    root = Path(sys.argv[1])
    sources = {name: (root / name).read_text() for name in EDITS}
    try:
        results = patch(sources)
    except ValueError as error:
        sys.exit("patch-fex-ios-branch-history: " + str(error))
    for name, result in results.items():
        if result != sources[name]:
            (root / name).write_text(result)
    print("FEX ARM64EC: Launcher-only eight-exit history, versioned DATA layout; guest control flow unchanged")

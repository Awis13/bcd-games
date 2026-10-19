#!/usr/bin/env python3
"""Keep the iOS Launcher full code validator as an explicit trial.

The page tracker, instruction semantics and other executables stay intact.
MADEIRA_LAUNCHER_FULL_SMC=1 opts in; explicit FEX_SMCCHECKS takes precedence.
Apply to the temporary ARM64EC build copy, never update the pinned submodule.
"""
from pathlib import Path
import sys

MARKER = "madeira-bcd: Launcher full SMC v1"
ANCHOR = '  FEXCore::Config::Set(FEXCore::Config::CONFIG_IS64BIT_MODE, "1");'
ADD = '''#ifdef FEX_IOS_HOST
  /* madeira-bcd: Launcher full SMC v1. Keep the configured SMC policy by
   * default: the full-validator trial did not fix the HLT and produces
   * larger JIT blocks. Explicit opt-in still checks original bytes before
   * each guest instruction and recompiles changed blocks with FEX's
   * existing validator. This is not a trap bypass or a single step. */
  {
    const auto SmcName = fextl::string {ExecutableName};
    const char* SmcOpt = getenv("MADEIRA_LAUNCHER_FULL_SMC");
    const char* SmcExplicit = getenv("FEX_SMCCHECKS");
    const bool SmcEnable = _stricmp(SmcName.c_str(), "Launcher.exe") == 0 &&
                          SmcOpt && strcmp(SmcOpt, "1") == 0 && !(SmcExplicit && SmcExplicit[0]);
    if (SmcEnable) {
      FEXCore::Config::Set(FEXCore::Config::CONFIG_SMCCHECKS,
                          fextl::fmt::format("{}", static_cast<unsigned>(FEXCore::Config::CONFIG_SMC_FULL)));
    }
    FEX_CONFIG_OPT(SmcEffective, SMCCHECKS);
    LogMan::Msg::IFmt("FEX: launcher-smc-v1 madeira-bcd enabled={} mode={} exe={}",
                     SmcEnable, static_cast<unsigned>(SmcEffective()), ExecutableName);
  }
#endif
'''


def patch(source):
    after = ADD + ANCHOR
    if MARKER in source:
        if source.count(after) != 1:
            raise ValueError("partial or changed Launcher SMC overlay")
        return source
    if source.count(ANCHOR) != 1:
        raise ValueError("ProcessInit configuration anchor changed")
    return source.replace(ANCHOR, after, 1)


if __name__ == "__main__":
    path = Path(sys.argv[1])
    source = path.read_text()
    try:
        result = patch(source)
    except ValueError as error:
        sys.exit("patch-fex-ios-launcher-smc: " + str(error))
    if result != source:
        path.write_text(result)
    print("FEX ARM64EC: Launcher FullSMC opt-in; configured policy and other processes preserved")

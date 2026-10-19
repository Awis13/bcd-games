#!/usr/bin/env python3
"""FEX CPUID: keep the host CPU index inside the per-CPU table.

CPUIDEmu's brand-string leaves (0x80000002-4) and the hybrid leaf (0x1A) index
PerCPUData with GetCPUID(), the host CPU number. PerCPUData has one entry per
MIDR FEX found; on the iOS host that is fewer than the CPU numbers the kernel
reports (build 243 log: "PerCPUData entries=1 host cpu now=3"), so the lookup
read past the vector and strlen() ran on garbage. 32-bit Crysis (log
2026-09-30 11:57): the guest's CPUID 0x80000002 took FEX's WOW64 module into
strlen(0xfff68000) -- an access violation before the game's first frame.
RunFunctionName() already wraps the index with `% PerCPUData.size()`; the
leaf entry points did not.

Usage: patch-fex-ios-cpuid-index.py FEX/FEXCore/Source/Interface/Core/CPUID.cpp
"""
import sys

path = sys.argv[1]
src = open(path).read()
marker = "madeira-bcd: CPU index wrapped into PerCPUData"
if marker in src:
    print("already patched")
    sys.exit(0)
if "CurrentCPUIndex()" in src and "WrapCPUIndex" in open(path.replace("CPUID.cpp", "CPUID.h")).read():
    # FEX #5 (125hz/pr/cpuid-table-bound, pinned 2026-09-30) bounds the index itself.
    print("CPUID.cpp: FEX bounds the per-CPU index itself (CurrentCPUIndex); nothing to do")
    sys.exit(0)

edits = [
    ("""FEXCore::CPUID::FunctionResults CPUIDEmu::Function_8000_0002h(uint32_t Leaf) const {
  return Function_8000_0002h(Leaf, GetCPUID());
}

FEXCore::CPUID::FunctionResults CPUIDEmu::Function_8000_0003h(uint32_t Leaf) const {
  return Function_8000_0003h(Leaf, GetCPUID());
}

FEXCore::CPUID::FunctionResults CPUIDEmu::Function_8000_0004h(uint32_t Leaf) const {
  return Function_8000_0004h(Leaf, GetCPUID());
}
""", """// madeira-bcd: CPU index wrapped into PerCPUData (tools/patch-fex-ios-cpuid-index.py), as
// RunFunctionName() does: the iOS host reports more CPU numbers than FEX has MIDR entries.
FEXCore::CPUID::FunctionResults CPUIDEmu::Function_8000_0002h(uint32_t Leaf) const {
  return Function_8000_0002h(Leaf, GetCPUID() % PerCPUData.size());
}

FEXCore::CPUID::FunctionResults CPUIDEmu::Function_8000_0003h(uint32_t Leaf) const {
  return Function_8000_0003h(Leaf, GetCPUID() % PerCPUData.size());
}

FEXCore::CPUID::FunctionResults CPUIDEmu::Function_8000_0004h(uint32_t Leaf) const {
  return Function_8000_0004h(Leaf, GetCPUID() % PerCPUData.size());
}
"""),
    ("""    uint32_t CPU = GetCPUID();
    auto& Data = PerCPUData[CPU];
    // 0x40 is a big CPU""", """    uint32_t CPU = GetCPUID() % PerCPUData.size();
    auto& Data = PerCPUData[CPU];
    // 0x40 is a big CPU"""),
]
for old, new in edits:
    if src.count(old) != 1:
        sys.exit("patch-fex-ios-cpuid-index: anchor not found:\n" + old[:120])
    src = src.replace(old, new)
open(path, "w").write(src)
print("CPUID.cpp: the brand-string and hybrid leaves wrap the host CPU index into PerCPUData")

// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
//
// [fault-dump]: the Windows glue of the 64-bit guest fault report (ajan17-ds2, 2026-10-23). The report itself, and why it
// exists, is d3d9-x64-fault-dump.hpp.
//
// This file is compiled into d3d9-dxmt.dll by tools/build-d3d9-x64-dll.sh and into nothing else: the 32-bit Direct3D 9 build
// (d3d9-emulated.dll), Wine's d3d9.dll, the other DXMT DLLs and the unix side never see it. The DLL itself loads only in a
// game whose environment has MADEIRA_D3D9_X64=dxmt. Inside it, this arms itself only when the same game's environment has
// MADEIRA_D3D9_LAST=1 (the switch that arms DXMT's own [d3d9-last] call ring and its vectored handler), so a game that does not
// ask for it pays nothing: no handler is registered.
//
// What it does when armed: one vectored handler, first in the chain, that only observes (EXCEPTION_CONTINUE_SEARCH always).
// For an access violation, an in-page error or an illegal instruction it writes the report to the session log through DXMT's
// Logger ("info:  [fault-dump] ..."). At most 4 reports per process, never two for the same RIP, never two at once.
// Every read of process memory goes through VirtualQuery first, so a bad pointer prints "unreadable" instead of faulting
// inside the handler; the module list is walked from the PEB without the loader lock (a thread that holds it could never
// be waited for from a fault handler).

#include "d3d9-x64-fault-dump.hpp"

#include "log/log.hpp"

#include <windows.h>

#include <atomic>
#include <cstdint>
#include <cstring>
#include <string>

namespace {

// The x64 PEB / loader structures (the TEB of an ARM64EC guest thread has the x64 layout): TEB+0x60 = PEB, PEB+0x18 = Ldr,
// PEB_LDR_DATA+0x20 = InMemoryOrderModuleList.
struct CounterString { // UNICODE_STRING, x64 layout (winternl.h is not needed for anything else here)
  USHORT Length;
  USHORT MaximumLength;
  PWSTR Buffer;
};

struct LdrEntry {
  LIST_ENTRY InLoadOrderLinks;
  LIST_ENTRY InMemoryOrderLinks;
  LIST_ENTRY InInitializationOrderLinks;
  PVOID DllBase;
  PVOID EntryPoint;
  ULONG SizeOfImage;
  CounterString FullDllName;
  CounterString BaseDllName;
};

struct LiveReader {
  static bool readableByte(uint64_t a, uint64_t &regionEnd) {
    MEMORY_BASIC_INFORMATION mbi;
    if (!VirtualQuery((const void *)a, &mbi, sizeof(mbi)) || mbi.State != MEM_COMMIT)
      return false;
    if (mbi.Protect & (PAGE_NOACCESS | PAGE_GUARD))
      return false;
    if (!(mbi.Protect & (PAGE_READONLY | PAGE_READWRITE | PAGE_WRITECOPY | PAGE_EXECUTE_READ | PAGE_EXECUTE_READWRITE |
                         PAGE_EXECUTE_WRITECOPY)))
      return false;
    regionEnd = (uint64_t)mbi.BaseAddress + mbi.RegionSize;
    return true;
  }

  bool read(uint64_t a, void *buf, size_t len) {
    if (a < 0x10000 || a >= 0x800000000000ULL || a + len < a)
      return false;
    for (uint64_t p = a, end = a + len, re; p < end; p = re) {
      if (!readableByte(p, re) || re <= p)
        return false;
    }
    memcpy(buf, (const void *)a, len);
    return true;
  }

  bool image(uint64_t a, bcdfd::Image &out) {
    char *teb = (char *)NtCurrentTeb();
    uint64_t re;
    if (!readableByte((uint64_t)teb + 0x60, re))
      return false;
    char *peb = *(char **)(teb + 0x60);
    if (!peb || !readableByte((uint64_t)peb + 0x18, re))
      return false;
    char *ldr = *(char **)(peb + 0x18);
    if (!ldr || !readableByte((uint64_t)ldr + 0x20, re))
      return false;
    LIST_ENTRY *head = (LIST_ENTRY *)(ldr + 0x20);
    LIST_ENTRY *cur = head->Flink;
    for (int n = 0; cur && cur != head && n < 1024; n++) {
      if (!readableByte((uint64_t)cur, re))
        return false;
      LdrEntry *e = (LdrEntry *)((char *)cur - offsetof(LdrEntry, InMemoryOrderLinks));
      if (!readableByte((uint64_t)e, re) || !readableByte((uint64_t)(e + 1) - 1, re))
        return false;
      uint64_t base = (uint64_t)e->DllBase;
      if (base && a >= base && a < base + e->SizeOfImage) {
        out.base = base;
        out.size = e->SizeOfImage;
        out.name[0] = 0;
        const WCHAR *w = e->BaseDllName.Buffer;
        unsigned wl = e->BaseDllName.Length / sizeof(WCHAR), i = 0;
        if (w && readableByte((uint64_t)w, re))
          for (; i < wl && i + 1 < sizeof(out.name); i++)
            out.name[i] = (w[i] >= 0x20 && w[i] < 0x7f) ? (char)w[i] : '?';
        out.name[i] = 0;
        return true;
      }
      cur = cur->Flink;
    }
    return false;
  }
};

std::atomic<unsigned> g_dumps{0};
std::atomic<int> g_busy{0};
std::atomic<uint64_t> g_seen[4];

LONG CALLBACK faultDumpVeh(EXCEPTION_POINTERS *ep) {
  if (!ep || !ep->ExceptionRecord || !ep->ContextRecord)
    return EXCEPTION_CONTINUE_SEARCH;
  const DWORD code = ep->ExceptionRecord->ExceptionCode;
  if (code != EXCEPTION_ACCESS_VIOLATION && code != EXCEPTION_IN_PAGE_ERROR && code != EXCEPTION_ILLEGAL_INSTRUCTION)
    return EXCEPTION_CONTINUE_SEARCH;
  const CONTEXT *c = ep->ContextRecord;
  const uint64_t rip = c->Rip;
  for (auto &s : g_seen)
    if (s.load(std::memory_order_relaxed) == rip)
      return EXCEPTION_CONTINUE_SEARCH;
  if (g_busy.exchange(1))
    return EXCEPTION_CONTINUE_SEARCH;
  unsigned n = g_dumps.fetch_add(1) + 1;
  if (n <= 4) {
    g_seen[n - 1].store(rip, std::memory_order_relaxed);
    bcdfd::Regs g = {c->Rax, c->Rcx, c->Rdx, c->Rbx, c->Rsp, c->Rbp, c->Rsi, c->Rdi, c->R8,
                     c->R9,  c->R10, c->R11, c->R12, c->R13, c->R14, c->R15, c->Rip};
    NT_TIB *tib = (NT_TIB *)NtCurrentTeb();
    uint64_t hi = (uint64_t)tib->StackBase, lo = (uint64_t)tib->StackLimit;
    if (hi <= g.rsp || lo > g.rsp)
      lo = g.rsp, hi = g.rsp + 0x10000; // the TEB's bounds do not hold RSP: a window above RSP is all that is known
    uint64_t access = ep->ExceptionRecord->NumberParameters > 0 ? ep->ExceptionRecord->ExceptionInformation[0] : 0xff;
    uint64_t addr = ep->ExceptionRecord->NumberParameters > 1 ? ep->ExceptionRecord->ExceptionInformation[1] : 0;
    auto emit = [](const char *line) { dxmt::Logger::info(std::string("[fault-dump] ") + line); };
    char head[160];
    snprintf(head, sizeof(head), "context flags=0x%08x exception address=%p (a CONTEXT whose Rip is the exception address is the x64 one)",
             (unsigned)c->ContextFlags, ep->ExceptionRecord->ExceptionAddress);
    emit(head);
    LiveReader r;
    bcdfd::dumpFault(r, g, n, (unsigned)GetCurrentThreadId(), (uint32_t)code, access, addr, lo, hi, emit);
  }
  g_busy.store(0);
  return EXCEPTION_CONTINUE_SEARCH;
}

bool armFaultDump() {
  char v[16];
  DWORD len = GetEnvironmentVariableA("MADEIRA_D3D9_LAST", v, sizeof(v));
  if (!len || len >= sizeof(v))
    return false;
  if (strcmp(v, "1") && strcmp(v, "on") && strcmp(v, "yes") && strcmp(v, "true"))
    return false;
  return AddVectoredExceptionHandler(1, faultDumpVeh) != nullptr;
}

// A dynamic initializer: it runs while this DLL is loaded, long before the game can fault in code that matters, and only
// registers a handler (nothing is logged here: the Logger may not be constructed yet, see d3d9_census.cpp).
[[maybe_unused]] __attribute__((used)) const bool g_faultDumpArmed = armFaultDump();

} // namespace

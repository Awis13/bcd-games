#!/usr/bin/env python3
"""Keep NotifyMapViewOfSection from waiting on the thread's own shared hold.

NotifyMapViewOfSection -> HandleImageMap -> ImageTracker::HandleImageMap takes
FEX's CodeInvalidationMutex EXCLUSIVELY. A thread that is running translated
x64 code already holds that mutex SHARED, and the mutex has no read-to-write
upgrade, so when such a thread loads a DLL (x64 code -> LoadLibrary / a
delay-load thunk -> LdrLoadDll -> NtMapViewOfSection's syscall notification)
it parks behind itself forever, with wine's loader lock held.

God of War on madeira-bcd builds 226-230 (log 2026-09-30 08:01,
[guest-stk]): FEX dispatcher -> ntdll loader -> FEX notify -> ntdll
NtWaitForAlertByThreadId, the only guest thread, presents 0. Upstream's
loader-boundary path (NotifyImageMap, "intervals-only") exists for exactly
this reason (Marvel Cosmic Invasion, Book of the Dead), but the syscall path
still takes the exclusive lock.

With the patch, NotifyMapViewOfSection checks the per-thread shared-hold
record the mutex keeps in the TEB (depth at +0x16f0, mutex address at
+0x16f8, WritePriorityMutex::NoteReadAcquired) and, when this thread holds
the code-invalidation mutex shared, registers only the executable intervals
(what NotifyImageMap does) instead of waiting on itself.

Usage: patch-fex-ios-mapview-selfshared.py FEX/Source/Windows/ARM64EC/Module.cpp
"""
import sys

path = sys.argv[1]
src = open(path).read()
marker = "madeira-bcd: self-shared map notification"
if marker in src:
    print("already patched")
    sys.exit(0)

anchor = """  {
    std::scoped_lock Lock(ThreadCreationMutex);
    HandleImageMap(reinterpret_cast<uint64_t>(Address));
  }


  return STATUS_SUCCESS;
}"""
if src.count(anchor) != 1:
    sys.exit("patch-fex-ios-mapview-selfshared: NotifyMapViewOfSection anchor not found")

src = src.replace(anchor, """  /* madeira-bcd: self-shared map notification (tools/patch-fex-ios-mapview-selfshared.py).
   * A thread executing translated code holds CodeInvalidationMutex SHARED; the
   * ImageTracker half of HandleImageMap would take it EXCLUSIVELY and wait on
   * itself forever (no read-to-write upgrade) with wine's loader lock held.
   * Register the intervals only, as NotifyImageMap does, in that case. */
  if (CTX) {
    uint64_t Teb = 0;
    __asm__ volatile("mov %0, x18" : "=r"(Teb));
    const uint64_t Stamp = CTX->GetCodeInvalidationMutex().IosStampAddress();
    auto SelfShared = [Stamp](uint64_t T) {
      return T && *reinterpret_cast<const uint32_t*>(T + 0x16f0) != 0 &&
             *reinterpret_cast<const uint64_t*>(T + 0x16f8) == Stamp;
    };
#ifdef FEX_IOS_HOST
    const uint64_t TebTsd = reinterpret_cast<uint64_t>(IOSLoadTEB());
#else
    const uint64_t TebTsd = 0;
#endif
    if (SelfShared(Teb) || (TebTsd != Teb && SelfShared(TebTsd))) {
      static std::atomic<uint32_t> Skips {0};
      const auto N = ++Skips;
      fextl::string ModulePath = FEX::Windows::GetSectionFilePath(reinterpret_cast<uint64_t>(Address));
      fextl::string ModuleName = fextl::string {FEX::Windows::BaseName(ModulePath)};
      InvalidationTracker->HandleImageMap(ModuleName, reinterpret_cast<uint64_t>(Address));
      if (N <= 32) {
        LogMan::Msg::EFmt("[img-map] madeira-bcd #{} {} base={}: thread holds the code-invalidation mutex "
                          "shared -- intervals only, no ImageTracker (would wait on itself)",
                          N, ModuleName, Address);
      }
      return STATUS_SUCCESS;
    }
  }

  {
    std::scoped_lock Lock(ThreadCreationMutex);
    HandleImageMap(reinterpret_cast<uint64_t>(Address));
  }


  return STATUS_SUCCESS;
}""")
open(path, "w").write(src)
print("Module.cpp: NotifyMapViewOfSection no longer waits on the thread's own shared hold")

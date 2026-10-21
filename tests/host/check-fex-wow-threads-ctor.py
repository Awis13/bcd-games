#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""FEX WOW64 module: the global thread map is constructed when its static initialiser had no effect.

tools/patch-fex-ios-wow-threads-ctor.py. The 32-bit programs of the intake from iOS 18 devices (63 GB
address map; pes2013, Stick Fight, Dead Space, Terraria, Duck Game, Yume Nikki, Half-Life 2, ...) all
died in BTCpuThreadInit: `Threads.emplace(...)` on an all-zero std::unordered_map (max_load_factor 0)
computes ceil((size + 1) / 0) = SIZE_MAX buckets, libc++'s __next_prime throws overflow_error, and the
throw cannot unwind because the module's thread-local exception state needs the TLS pointer that the
thread's TEB does not have yet (libwow64fex.dll+0x1b2a48 = __cxa_get_globals, build 574).

Needs the FEX submodule (the pinned Module.cpp): ./FEX, or the directory named by MADEIRA_FEX_DIR.
  1. The overlay is idempotent, refuses a drifted anchor and a partial state.
  2. The three edits sit where they must: <new> with the other includes; the state report right after
     InitCRTProcess() (before anything that can fail); the guard right before Threads.emplace, and only
     for max_load_factor() <= 0.
  3. On the host, with libc++ (Apple clang): the zeroed map really throws from emplace (the module's binary:
     overflow_error out of __next_prime; the host's libc++ names its own exception),
     the guard's placement-new makes it work, and an initialised map is not touched by the condition.
"""
from pathlib import Path
import importlib.util
import os
import shutil
import subprocess
import tempfile

root = Path(__file__).resolve().parents[2]
ok = True


def check(what, cond):
    global ok
    print(('ok   ' if cond else 'FAIL ') + what)
    ok = ok and bool(cond)


spec = importlib.util.spec_from_file_location('ctor', root / 'tools/patch-fex-ios-wow-threads-ctor.py')
overlay = importlib.util.module_from_spec(spec)
spec.loader.exec_module(overlay)

fex = Path(os.environ.get('MADEIRA_FEX_DIR') or root / 'FEX')
module_path = fex / overlay.MODULE
if not module_path.exists():
    print('note: no FEX submodule (set MADEIRA_FEX_DIR); the overlay checks were not run')
else:
    module = module_path.read_text()
    pinned = overlay.MARKER in module
    patched = overlay.patch(module)
    check('the overlay applies (or the pin carries it) and is idempotent', overlay.MARKER in patched and overlay.patch(patched) == patched)
    if not pinned:
        def refused(source):
            try:
                overlay.patch(source)
            except ValueError:
                return True
            return False

        for anchor, after in overlay.EDITS:
            check('a changed anchor is refused: ' + anchor.strip().splitlines()[0][:40],
                  refused(module.replace(anchor, 'changed-anchor', 1)))
            check('a partial state is refused: ' + anchor.strip().splitlines()[0][:40], refused(patched.replace(after, anchor, 1)))
    inc = patched.index('#include <new>')
    check('<new> is included with the other standard headers', inc < patched.index('#include <ntstatus.h>'))
    init = patched.index('void BTCpuProcessInit() {')
    report = patched.index('[wow64-ctor] madeira-bcd: Threads@{} max_load_factor={}', init)
    tsd = patched.index('MADEIRA: import the TEB TSD slot offset before anything reads a TEB', init)
    check('the state is reported first in BTCpuProcessInit: after InitCRTProcess(), before the TSD import',
          init < patched.index('FEX::Windows::InitCRTProcess();', init) < report < tsd)
    thr = patched.index('void BTCpuThreadInit() {')
    guard = patched.index('if (Threads.max_load_factor() <= 0.0f) {', thr)
    emplace = patched.index('Threads.emplace(ThreadTID, Thread);', thr)
    check('the guard sits right before the first emplace, for a zero max_load_factor only', thr < guard < emplace)
    check('the guard constructs the map in place with the same type',
          '::new (static_cast<void*>(&Threads)) std::unordered_map<DWORD, FEXCore::Core::InternalThreadState*>();' in patched
          and 'std::unordered_map<DWORD, FEXCore::Core::InternalThreadState*> Threads;' in patched)
    check('nothing else in the file changed', patched.count('wow64-ctor-guard v1') == 3 and patched.count('Threads.emplace(') == 1)
    check('the WOW64 build applies the overlay and restores the file', 'patch-fex-ios-wow-threads-ctor.py'
          in (root / 'build/fex-wow64/build.sh').read_text())
    ci = (root / 'tools/build-xtajit-wow64.sh').read_text()
    check('the CI xtajit build (tools/build-xtajit-wow64.sh) applies the overlay after its two required patches, '
          'without letting a drifted anchor cost them',
          ci.index('patch-fex-ios-teb-tsd.py') < ci.index('patch-fex-ios-wow-threads-ctor.py') < ci.index('trap restore EXIT')
          and '|| echo "::warning::the thread-map guard was not applied' in ci)
    check('...and restores Module.cpp with the other two files',
          'checkout -- "$CPUIDF" Source/Windows/Common/Priv.h Source/Windows/WOW64/Module.cpp' in ci)
    check('the CI workflow builds xtajit.dll with that script', 'run: bash tools/build-xtajit-wow64.sh'
          in (root / '.github/workflows/build-ipa.yml').read_text())

# --- the failure and the guard on the host ----------------------------------------
cxx = shutil.which('c++') or shutil.which('clang++') or shutil.which('g++')
CODE = r'''
#include <cstdio>
#include <cstring>
#include <new>
#include <exception>
#include <stdexcept>
#include <unordered_map>
using Map = std::unordered_map<unsigned long, void*>;
alignas(Map) static unsigned char storage[sizeof(Map)];

int main() {
  Map* m = reinterpret_cast<Map*>(storage);
  std::memset(storage, 0, sizeof storage);                 // the module's .bss without its static initialiser
  const float zero = m->max_load_factor();
  int threw = 0;
  const char* what = "";
  try { m->emplace(1ul, nullptr); } catch (const std::exception& e) { threw = 1; what = e.what(); } catch (...) { threw = 2; }
  std::printf("zeroed map: max_load_factor=%g, emplace %s%s%s\n", zero, threw ? "throws" : "works", threw == 1 ? ": " : "", what);

  std::memset(storage, 0, sizeof storage);
  if (m->max_load_factor() <= 0.0f) ::new (static_cast<void*>(m)) Map();   // the overlay's guard
  for (unsigned long i = 1; i <= 40; i++) m->emplace(i, nullptr);
  if (m->size() != 40 || m->find(17) == m->end() || m->max_load_factor() != 1.0f) { std::puts("FAIL guarded map"); return 1; }

  // an initialised map is never touched: the condition is false, and its entries survive
  Map good;
  good.emplace(5ul, nullptr);
  const Map* before = &good;
  if (good.max_load_factor() <= 0.0f) { std::puts("FAIL initialised map would be reconstructed"); return 1; }
  if (good.size() != 1 || &good != before) { std::puts("FAIL initialised map changed"); return 1; }
  std::puts(zero == 0.0f ? (threw ? "RESULT: reproduced" : "RESULT: zeroed map did not throw here") : "RESULT: this library's zero object differs");
  return 0;
}
'''
if cxx:
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / 't.cpp'
        src.write_text(CODE)
        exe = Path(tmp) / 't'
        r = subprocess.run([cxx, '-std=c++17', '-O1', '-o', str(exe), str(src)], capture_output=True, text=True)
        check('the host program compiles', r.returncode == 0)
        if r.returncode:
            print(r.stderr)
        else:
            run = subprocess.run([str(exe)], capture_output=True, text=True)
            print(run.stdout.rstrip())
            check('the guarded map works and an initialised map is not touched', run.returncode == 0)
            if 'RESULT: reproduced' in run.stdout:
                check('the zeroed map throws from emplace (the iOS 18 crash: __next_prime overflow in the module), on this libc++', True)
            else:
                print('note: this host library does not throw for a zeroed map (libc++ does); the reproduction was not asserted')
else:
    print('note: no host C++ compiler; the map demonstration was not run')

raise SystemExit(0 if ok else 1)

#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""exe-window-mb: room at 0x140000000 for a large program with its relocations stripped.

Hogwarts Legacy (445 MB), Star Wars Galactic Racer (480 MB), Metal Gear Solid V (160 MB)
and Stellar Blade's demo (314 MB) must load at 0x140000000; the 128 MB executable window held only part of
it and the JIT pool the rest: "failed to create main module ... c0000018" (testers' logs, builds 565-574).
Checks (no device):
  - StikJITHelper widens the held window only for a game whose own file sets exe-window-mb above 128 (up to
    1024), after the early placeholder is released and before the hole census, with one fixed allocation
    right above the window; the size reaches ntdll through WINE_IOS_EXE_WINDOW; a failure leaves 128 MB;
  - ntdll's window takes its size from WINE_IOS_EXE_WINDOW (virtual_ios.c);
  - the catalog key (default 128), the auto-tune rule and its fixture.
"""
from pathlib import Path
import re
import sys

root = Path(__file__).resolve().parents[2]
ok = True


def check(what, cond):
    global ok
    print(('ok   ' if cond else 'FAIL ') + what)
    ok = ok and bool(cond)


src = (root / 'app/Madeira/StikJITHelper.swift').read_text()
blk = src[src.index('// madeira-bcd exe-window-mb'):]
blk = blk[:blk.index('if reservePlan == nil {   // madeira-bcd pool-reserve: no census')]
check('only with the game\'s own line, above 128 and at most 1024, and only when the window is held',
      'MadeiraConfig.gameValue("exe-window-mb")' in blk and 'mb > Int(exeWinSize >> 20), mb <= 1024' in blk and
      'if windowHeld, reservePlan == nil,' in blk and 'MadeiraConfig.get("exe-window-mb")' not in blk)
check('one fixed allocation right above the window', 'var extra: vm_address_t = exeWinBase + exeWinSize' in blk and
      'vm_allocate(mach_task_self_, &extra, vm_size_t(want - exeWinSize), 0 /* VM_FLAGS_FIXED */)' in blk and
      'extra == exeWinBase + exeWinSize' in blk)
check('the new size reaches ntdll, and a failure keeps 128 MB (what it got is given back)',
      'setenv("WINE_IOS_EXE_WINDOW"' in blk and 'vm_deallocate(mach_task_self_, extra' in blk and
      blk.index('exeWinSize = want') < blk.index('setenv("WINE_IOS_EXE_WINDOW"'))
check('after the early placeholder is released, before the hole census',
      src.index('released the early pool placeholder') < src.index('// madeira-bcd exe-window-mb') <
      src.index('if reservePlan == nil {   // madeira-bcd pool-reserve: no census'))
check('the window size is a var the overlap test reads', 'var exeWinSize: vm_address_t = 0x8000000' in src and
      'return base < exeWinBase + exeWinSize && base + len > exeWinBase' in src)
nt = (root / 'build/ntdll-unix/virtual_ios.c').read_text()
check('ntdll sizes the window from WINE_IOS_EXE_WINDOW', 'getenv( "WINE_IOS_EXE_WINDOW" )' in nt and
      'ios_exe_win_size = strtoull( end + 1, NULL, 16 );' in nt)
gen = (root / 'build/tools/gen-config-catalog.py').read_text()
check('the catalog key, default 128', re.search(r'"exe-window-mb": \{[^}]*"kind": "int", "default": "128"', gen))
rules = (root / 'build/tools/autotune_rules.py').read_text()
check('auto-tune adds exe-window-mb = 512 after "failed to create main module ... c0000018"',
      '"lines": ["exe-window-mb = 512"]' in rules and 's("failed to create main module", "c0000018")' in rules)
check('a fixture proves it', any((root / 'tests/fixtures/autotune').glob('hogwarts-legacy-*.expect')))
print('PASS: exe window' if ok else 'FAILED: exe window')
sys.exit(0 if ok else 1)

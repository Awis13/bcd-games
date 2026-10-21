#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""env.MADEIRA_OPTIN_DLLS: more of Wine's DLLs, each for the games that list it; no Wine runs.

Fort Solis and DRAGON BALL Sparking! ZERO import dxva2.dll, Animal Well XPSPRINT.DLL, Spider-Man: Miles
Morales' crs-video.exe EVR.dll; the 64-bit set ships none, and the programs ended at once with c0000135
(testers' logs, builds 565-574). Shipping them for every game could change a program that loads one
optionally today (Chromium loads dxva2 for video), so each is opt-in, as wer-optin.dll:

  - tools/build-wine-extra-dlls.sh builds OPTIN_WANT and ships each as <name>-optin.dll, never as
    <name>.dll, and not at all where <name>.dll already ships;
  - app/Madeira/WineProcessBridge.m links each *-optin.dll the game lists as <name>.dll (sysx64, and
    system32 in an ARM64EC session), removes only a link of ours for every one it does not list, leaves
    wer and wintypes to their own switches, says what it did, and the switch ends with the session;
  - the catalog documents the switch (empty by default); auto-tune adds dxva2 / xpsprint after the
    import failure (fixtures fort-solis-*, animal-well-*).
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


build = (root / 'tools/build-wine-extra-dlls.sh').read_text()
want = build[build.index('WANT="'):]
want = want[:want.index('"\n', 6)]
optin = re.search(r'OPTIN_WANT="([^"]*)"', build)
names = optin.group(1).split() if optin else []
check('OPTIN_WANT lists dxva2, evr and xpsprint (%s)' % names, {'dxva2', 'evr', 'xpsprint'} <= set(names))
check('none of them is in the list shipped under its own name', not any(re.search(r'\b%s\b' % n, want) for n in names))
opt = build[build.index('OPT=""'):]
opt = opt[:opt.index('\nfi\n') + 4]
check('each is built only where no <name>.dll ships, and MADEIRA_OPTIN_BUILD=0 skips them',
      '"${MADEIRA_OPTIN_BUILD:-1}" != 0' in opt and 'shipped "$d" && continue' in opt and
      'targets="$targets dlls/$d/arm64ec-windows/$d.dll"; OPT="$OPT $d"' in opt)
check('the guard "nothing to build" counts them (and setupapi-optin.dll, SDL)', '$WER$WTY$OPT$SDL" ]' in build)
ship = build[build.index('for d in $OPT; do'):]
ship = ship[:ship.index('\ndone\n') + 6]
check('each ships as <name>-optin.dll, stripped and padded, never as <name>.dll',
      'cp "$f" "$SHIP/$d-optin.dll.tmp"' in ship and 'llvm-strip" "$SHIP/$d-optin.dll.tmp"' in ship and
      '+ 0x10000' in ship and 'mv "$SHIP/$d-optin.dll.tmp" "$SHIP/$d-optin.dll"' in ship and '$SHIP/$d.dll' not in ship)

bridge = (root / 'app/Madeira/WineProcessBridge.m').read_text()
blk = bridge[bridge.index('const char *optList = getenv("MADEIRA_OPTIN_DLLS");'):]
blk = blk[:blk.index('\n            }\n')]
check('the list is split on commas, semicolons and spaces, lowercased, ".dll" optional',
      'characterSetWithCharactersInString:@",; "' in blk and 'lowercaseString' in blk and 'hasSuffix:@".dll"' in blk)
check('only shipped *-optin.dll files are considered; wer and wintypes keep their own switches',
      'contentsOfDirectoryAtPath:ecDir' in blk and 'hasSuffix:@"-optin.dll"' in blk and
      '[name isEqualToString:@"wer"] || [name isEqualToString:@"wintypes"]' in blk)
check('a listed one is linked as <name>.dll in sysx64, and system32 too in an ARM64EC session',
      'use_arm64ec\n                    ? @[ [prefix stringByAppendingPathComponent:@"drive_c/windows/sysx64"], sys32Dir ]' in blk
      and 'createSymbolicLinkAtPath:dst withDestinationPath:src' in blk)
check('one not listed loses only a link of ours', '[to.lastPathComponent isEqualToString:file]' in blk and
      blk.count('removeItemAtPath:dst') == 2)
check('it says what it linked, and what it could not', 'MADEIRA_OPTIN_DLLS: %s -> %s' in blk and
      'which this build does not ship as %s-optin.dll' in blk)
check('the switch ends with the session', 'unsetenv("MADEIRA_OPTIN_DLLS");' in bridge)

gen = (root / 'build/tools/gen-config-catalog.py').read_text()
swift = (root / 'app/Madeira/ConfigCatalog.generated.swift').read_text()
check('the catalog documents env.MADEIRA_OPTIN_DLLS (empty by default)',
      '"env.MADEIRA_OPTIN_DLLS": {' in gen and re.search(r'key: "env.MADEIRA_OPTIN_DLLS".*defaultValue: ""', swift))
rules = (root / 'build/tools/autotune_rules.py').read_text()
for name, log in (('dxva2', 'dxva2.dll'), ('xpsprint', 'XPSPRINT.DLL')):
    rule = rules[rules.index('"id": "%s-dll-optin"' % name):]
    rule = rule[:rule.index('\n    },')]
    check('auto-tune adds %s after "Library %s ... not found", not again once linked' % (name, log),
          '"lines": ["env.MADEIRA_OPTIN_DLLS = %s"]' % name in rule and 'Library %s' % log in rule and
          'MADEIRA_OPTIN_DLLS: %s.dll -> %s-optin.dll' % (name, name) in rule)
fx = root / 'tests/fixtures/autotune'
check('fixtures prove the rules', any(fx.glob('fort-solis-*.expect')) and any(fx.glob('animal-well-*.expect')))
wf = (root / '.github/workflows/build-ipa.yml').read_text()
check('CI runs this check', 'python3 tests/host/check-optin-dlls.py' in wf)
print('PASS: opt-in DLLs' if ok else 'FAILED: opt-in DLLs')
sys.exit(0 if ok else 1)

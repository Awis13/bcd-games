#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""wintypes-optin.dll (env.MADEIRA_WINTYPES = 1): Wine's wintypes.dll for the games that import it; no Wine runs.

Unity IL2CPP's GameAssembly.dll imports api-ms-win-core-winrt-robuffer-l1-1-0.dll, which Wine's apiset
maps to wintypes.dll; the 64-bit set ships none, so Megabonk, No, I'm not a Human and Over the Hill ended
at once with c0000135 (testers' logs J7VAJV, TZB1JX, HXQQN1, build 574). Steam's web helper (Chromium)
loads wintypes.dll optionally and works without it, so it is opt-in per game, exactly as wer-optin.dll
(tests/host/check-wer-optin.py):

  - tools/build-wine-extra-dlls.sh builds Wine's wintypes and ships it as wintypes-optin.dll, never as
    wintypes.dll, and not at all where a wintypes.dll already ships;
  - app/Madeira/WineProcessBridge.m links it as wintypes.dll for a session whose game has
    env.MADEIRA_WINTYPES = 1, removes only a link of ours when the line is gone, and the switch ends
    with the session;
  - the catalog documents the switch, off by default; auto-tune's rule "wintypes-dll-optin" adds it
    after that failure (tests/fixtures/autotune/megabonk-*.log).
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
check('wintypes is not in the list of DLLs shipped under their own name', not re.search(r'\bwintypes\b', want))
wty = build[build.index('WTY=""'):]
wty = wty[:wty.index('\nfi\n') + 4]
check('it is built only where no wintypes.dll ships, and MADEIRA_WTY_BUILD=0 skips it',
      '"${MADEIRA_WTY_BUILD:-1}" != 0' in wty and '! shipped wintypes' in wty and
      'targets="$targets dlls/wintypes/arm64ec-windows/wintypes.dll"; WTY=wintypes' in wty)
check('the guard "nothing to build" counts it', '$WER$WTY' in build and '"nothing to build"' in build)
ship = build[build.index('if [ -n "$WTY" ]; then'):]
ship = ship[:ship.index('\nfi\n') + 4]
check('it ships as wintypes-optin.dll, stripped and padded like the others, and never as wintypes.dll',
      'cp "$f" "$SHIP/wintypes-optin.dll.tmp"' in ship and 'llvm-strip" "$SHIP/wintypes-optin.dll.tmp"' in ship and
      '+ 0x10000' in ship and 'mv "$SHIP/wintypes-optin.dll.tmp" "$SHIP/wintypes-optin.dll"' in ship and
      '$SHIP/wintypes.dll' not in build)

bridge = (root / 'app/Madeira/WineProcessBridge.m').read_text()
blk = bridge[bridge.index('const char *wtyOptIn = getenv("MADEIRA_WINTYPES");'):]
blk = blk[:blk.index('\n            }\n')]
check('WineProcessBridge links it for MADEIRA_WINTYPES=1: sysx64 in any session, system32 too in an ARM64EC one',
      "if (wtyOptIn && wtyOptIn[0] == '1')" in blk and
      'stringByAppendingPathComponent:@"wintypes-optin.dll"' in blk and
      '[prefix stringByAppendingPathComponent:@"drive_c/windows/sysx64"]];' in blk and
      'if (use_arm64ec) [wtyDirs addObject:sys32Dir];' in blk and
      'stringByAppendingPathComponent:@"wintypes.dll"' in blk and
      'createSymbolicLinkAtPath:dst withDestinationPath:wtyDll' in blk)
check('and says so either way', 'MADEIRA_WINTYPES=1: wintypes.dll -> wintypes-optin.dll' in blk and
      'MADEIRA_WINTYPES=1 but this build has no wintypes-optin.dll' in blk)
check('without the line it removes only a link of ours (to wintypes-optin.dll) from sysx64 and system32',
      'destinationOfSymbolicLinkAtPath:dst' in blk and '[to.lastPathComponent isEqualToString:@"wintypes-optin.dll"]' in blk
      and blk.count('removeItemAtPath:dst') == 2)
check('the switch ends with the session', 'unsetenv("MADEIRA_WINTYPES");' in bridge)

gen = (root / 'build/tools/gen-config-catalog.py').read_text()
swift = (root / 'app/Madeira/ConfigCatalog.generated.swift').read_text()
check('the catalog documents env.MADEIRA_WINTYPES (off by default) and is generated with it',
      '"env.MADEIRA_WINTYPES": {' in gen and re.search(r'key: "env.MADEIRA_WINTYPES".*defaultValue: "0"', swift))
rules = (root / 'build/tools/autotune_rules.py').read_text()
rule = rules[rules.index('"id": "wintypes-dll-optin"'):]
rule = rule[:rule.index('\n    },')]
check('auto-tune adds it after the import failure, not again once linked',
      '"lines": ["env.MADEIRA_WINTYPES = 1"]' in rule and 'api-ms-win-core-winrt-robuffer-l1-1-0.dll' in rule and
      'MADEIRA_WINTYPES=1: wintypes.dll -> wintypes-optin.dll' in rule)
check('a fixture proves the rule', any((root / 'tests/fixtures/autotune').glob('megabonk-*.expect')))
print('PASS: wintypes opt-in' if ok else 'FAILED: wintypes opt-in')
sys.exit(0 if ok else 1)

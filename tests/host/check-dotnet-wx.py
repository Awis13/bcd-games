#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""env.DOTNET_EnableWriteXorExecute = 0 for a self-contained .NET 7+ game; no Wine runs.

Quaver (Steam 980610, build 574, tester log DKEQ3C) is a self-contained .NET runtime (hostfxr.dll,
hostpolicy.dll, coreclr.dll). It stopped at start: "CLR: Assert failure ... 0xf0f0f0f0f0f0f0f0 ==
*(UINT64*)m_pRegionToGenTableImmediate" (jitinterfaceamd64.cpp:514), exit c0000602. .NET 7+ keeps its code
in a write-xor-execute double mapping, one section mapped twice; the log shows the writable view (handle 2C,
prot=4) and the executable one (prot=40), and BCD turns the executable view into a fresh JIT-pool slot
("[jit-pool] anon RWX ... NEW"), so the write barrier the runtime copies through the writable view never
shows in the executable one. The runtime's own switch DOTNET_EnableWriteXorExecute=0 makes it use plain
executable memory instead. Nothing in the app reads the name; this check holds the wiring together:

  - auto-tune's rule "dotnet-wx-off" adds the line after that assert, once, and not again when the session
    already ran with it ("[madeira-env] game DOTNET_EnableWriteXorExecute=0", the line the Wine process
    bridge prints for a game's env.NAME lines);
  - a fixture from the tester's log proves the rule;
  - the settings catalog documents the line (build/tools/gen-config-catalog.py), on by default as the
    runtime's own default is;
  - a downloaded list may set the name, only to 0, and the Quaver list does from build 576.
"""
from pathlib import Path
import json
import re
import sys

root = Path(__file__).resolve().parents[2]
ok = True


def check(what, cond):
    global ok
    print(('ok   ' if cond else 'FAIL ') + what)
    ok = ok and bool(cond)


rules = (root / 'build/tools/autotune_rules.py').read_text()
rule = rules[rules.index('"id": "dotnet-wx-off"'):]
rule = rule[:rule.index('\n    },')]
check('auto-tune adds the line after the CLR assert about the write-barrier patch location',
      '"lines": ["env.DOTNET_EnableWriteXorExecute = 0"]' in rule and
      's("CLR: Assert failure", "m_pRegionToGenTableImmediate")' in rule)
check('...once: the session that already ran with it does not match again, and the failure is not retried unchanged',
      's("[madeira-env] game DOTNET_EnableWriteXorExecute=0")' in rule and '"noRetry": True' in rule and
      '"outcomes": ["crashed"]' in rule)

# The log line the "none" signal reads is the one the process bridge prints for a game's env lines,
# and the line auto-tune's own glue writes for a kept key.
bridge = (root / 'app/Madeira/WineProcessBridge.m').read_text()
glue = (root / 'app/Madeira/AutoTune.swift').read_text()
check('the "none" signal is the line the process bridge prints for a game env line',
      'fprintf(stderr, "[madeira-env] game %s=%s\\n", k.UTF8String, v.UTF8String);' in bridge and
      '"[madeira-env] game \\(key.dropFirst(4))=\\(value)"' in glue)

fix = root / 'tests/fixtures/autotune'
expect = json.loads((fix / 'quaver-2026-10-20.expect').read_text())
log = (fix / 'quaver-2026-10-20.log').read_text()
check('the fixture is the tester\'s session: the assert line is in it, the runtime was not told to switch it off',
      'm_pRegionToGenTableImmediate' in log and 'DOTNET_EnableWriteXorExecute' not in log)
check('...and gets exactly that line, from this rule',
      expect['matched'] == ['dotnet-wx-off'] and expect['next'] == ['env.DOTNET_EnableWriteXorExecute = 0'] and
      expect['outcome'] == 'crashed')

gen = (root / 'build/tools/gen-config-catalog.py').read_text()
entry = gen[gen.index('"env.DOTNET_EnableWriteXorExecute": {'):]
entry = entry[:entry.index('\n    "env.MADEIRA_FIBER_TRACE"')]
check('the catalog documents the name: a bool, the runtime\'s own default (on)',
      '"kind": "bool", "default": "1"' in entry and 'm_pRegionToGenTableImmediate' in entry)

policy = (root / 'app/Madeira/GameRecommendationsList.swift').read_text()
check('a downloaded list may set the name, to 0 only',
      '"DOTNET_EnableWriteXorExecute",' in policy and
      'case "DOTNET_EnableWriteXorExecute":' in policy and 'guard value == "0" else {' in policy)

recs = json.loads((root / 'recommended/recommendations.json').read_text())
q = [r for r in recs['rules'] if r['id'] == 'quaver']
check('Quaver\'s list (Steam 980610) carries the line from build 576 only',
      len(q) == 1 and q[0]['match']['steamAppIDs'] == [980610] and
      {'line': 'env.DOTNET_EnableWriteXorExecute = 0', 'minBuild': 576} in q[0]['config'] and
      not any(isinstance(c, str) and 'DOTNET' in c for c in q[0]['config']))
print('PASS: .NET write-xor-execute off' if ok else 'FAILED: .NET write-xor-execute off')
sys.exit(0 if ok else 1)

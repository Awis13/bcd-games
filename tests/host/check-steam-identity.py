#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""env.MADEIRA_STEAM_IDENTITY = none: a session without any Steam game identity (lead, 2026-10-24).

Outside a Dock session WineProcessBridge.m publishes one fixed Steam identity (SteamAppId 356400) to every program.
Rockstar's launcher of the Epic GTA V / RDR2 took itself for the Steam version and stopped with
LAUNCHER_ERR_STEAM_FAILED (113). The switch removes the three variables for that game only.

This check compiles the bridge's real block (from the Dock test to the two unsetenv calls after it) in a small C
harness and runs it in every case: unset and any other value give exactly the old results (fixed identity, Dock:
none, a direct Steam start: the game's own); only "none" removes all three. It also checks the list policy, the
catalog entry, the auto-tune rule and its fixture, and the CI step."""
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

root = Path(__file__).resolve().parents[2]
failed = []


def check(name, ok):
    print(("ok   " if ok else "FAIL ") + name)
    if not ok:
        failed.append(name)


bridge = (root / 'app/Madeira/WineProcessBridge.m').read_text()
start = bridge.index('        const char *dock_session = getenv("MADEIRA_DOCK_SESSION");')
end = bridge.index('        unsetenv("MADEIRA_STEAM_APPPATH");', start) + len('        unsetenv("MADEIRA_STEAM_APPPATH");')
block = bridge[start:end]
check('the switch is read in the bridge and tested first', block.index('MADEIRA_STEAM_IDENTITY') < block.index('dock_session[0]'))
check('only the exact value "none" counts', 'strcmp(steam_identity, "none") == 0' in block)
check('the fixed identity is still the last branch (Thumper)', 'setenv("SteamAppId",  "356400", 1);' in block)

harness = r'''
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#define dprintf(fd, ...) printf(__VA_ARGS__)
static void show(void) {
    const char *names[] = {"SteamAppPath", "SteamGameId", "SteamAppId"};
    for (int i = 0; i < 3; i++) { const char *v = getenv(names[i]); printf("%s=%s\n", names[i], v ? v : "(unset)"); }
}
int main(void) {
    setenv("SteamAppId", "999", 1);   /* something earlier: the switch must remove it too */
BLOCK
    show();
    return 0;
}
'''.replace('BLOCK', block)
cases = {
    'unset': {},
    'other value': {'MADEIRA_STEAM_IDENTITY': '0'},
    'none': {'MADEIRA_STEAM_IDENTITY': 'none'},
    'Dock': {'MADEIRA_DOCK_SESSION': '1'},
    'Dock + none': {'MADEIRA_DOCK_SESSION': '1', 'MADEIRA_STEAM_IDENTITY': 'none'},
    'direct start': {'MADEIRA_STEAM_APPID': '271590', 'MADEIRA_STEAM_APPPATH': 'C:\\Games\\GTAV'},
    'direct start + none': {'MADEIRA_STEAM_APPID': '271590', 'MADEIRA_STEAM_APPPATH': 'C:\\Games\\GTAV', 'MADEIRA_STEAM_IDENTITY': 'none'},
}
with tempfile.TemporaryDirectory() as tmp:
    src = Path(tmp) / 'h.c'
    src.write_text(harness)
    exe = Path(tmp) / 'h'
    b = subprocess.run(['cc', '-w', '-o', str(exe), str(src)], capture_output=True, text=True)
    check('the bridge block compiles in the harness', b.returncode == 0)
    if b.returncode:
        print(b.stderr[-1500:])
    else:
        out = {}
        for name, env in cases.items():
            e = {k: v for k, v in os.environ.items() if not k.startswith(('MADEIRA_', 'Steam'))}
            e.update(env)
            out[name] = subprocess.run([str(exe)], capture_output=True, text=True, env=e).stdout
        fixed = 'SteamAppPath=C:\\Program Files\\Thumper\nSteamGameId=356400\nSteamAppId=356400\n'
        none = 'SteamAppPath=(unset)\nSteamGameId=(unset)\nSteamAppId=(unset)\n'
        check('unset: the fixed identity, as before', out['unset'].endswith(fixed))
        check('any other value: the fixed identity, as before', out['other value'].endswith(fixed))
        check('none: no identity at all (even one set earlier)', out['none'].endswith(none))
        check('Dock: none, as before', out['Dock'].endswith(none))
        check('Dock with the switch: still none', out['Dock + none'].endswith(none))
        check("a direct Steam start: the game's own identity, as before",
              out['direct start'].endswith('SteamAppPath=C:\\Games\\GTAV\nSteamGameId=271590\nSteamAppId=271590\n'))
        check('a direct Steam start with the switch: none', out['direct start + none'].endswith(none))

policy = (root / 'app/Madeira/GameRecommendationsList.swift').read_text()
check('a downloaded list may name it, with the one value none',
      '"MADEIRA_STEAM_IDENTITY"' in policy and 'case "MADEIRA_STEAM_IDENTITY":' in policy and 'guard value == "none"' in policy)
gen = (root / 'build/tools/gen-config-catalog.py').read_text()
check('the catalog explains it', '"env.MADEIRA_STEAM_IDENTITY":' in gen)
rules = (root / 'build/tools/autotune_rules.py').read_text()
i = rules.index('"id": "rockstar-launcher-epic-steam-identity"')
rule = rules[i:rules.index('\n    },', i)]
check('auto-tune adds the line only after the 113 error in an Epic session, and not again once it ran',
      '"lines": ["env.MADEIRA_STEAM_IDENTITY = none"]' in rule and 's("LAUNCHER_ERR_STEAM_FAILED")' in rule
      and 's("[epic-launch] start")' in rule and 's("MADEIRA_STEAM_IDENTITY=none")' in rule)
fx = root / 'tests/fixtures/autotune/rockstar-launcher-epic-steam-identity-2026-10-23'
expect = json.loads(fx.with_suffix('.expect').read_text())
log = fx.with_suffix('.log').read_text(errors='replace')
check("its fixture is the tester's log and proposes the line",
      expect.get('next') == ['env.MADEIRA_STEAM_IDENTITY = none'] and 'LAUNCHER_ERR_STEAM_FAILED' in log and '[epic-launch] start' in log)
check('the fixture carries no container id or Windows user name',
      not re.search(r'[0-9A-F]{8}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{12}', log)
      and not re.search(r'users\\(?!<user>|Public|mobile)[A-Za-z]', log))
wf = (root / '.github/workflows/build-ipa.yml').read_text()
check('CI runs this check', 'python3 tests/host/check-steam-identity.py' in wf)

if failed:
    print(f'FAILED: steam identity ({len(failed)})')
    sys.exit(1)
print('PASS: steam identity')

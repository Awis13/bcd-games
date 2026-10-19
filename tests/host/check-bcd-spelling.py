#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""BCD's interface spells "license", not "licence", in everything a person reads (owner,
2026-10-19). Every string literal of a BCD*.swift file is read; what stays on purpose:

  - identifiers (BCDLicenceText, \\(licence.title)) and focus ids ("licence.<id>"), which a person never sees;
  - the credits' legal paragraph in Settings › About, which is carried over word for word
    ("The licence: gnu.org/licenses/gpl-3.0");
  - file names and the keys that name them (LICENSE-..., MIT-...): they are not spelled "licence".

Comments are not read.
"""
import re
import sys
from pathlib import Path

R = Path(__file__).resolve().parents[2]
FILES = sorted(p for p in (R / "app").rglob("BCD*.swift"))
LITERAL = re.compile(r'"(?:[^"\\]|\\.)*"')
FOCUS_ID = re.compile(r'"licence(\.\\\(licence\.id\))?"')
ok = True


def check(what, cond, detail=""):
    global ok
    print(("ok   " if cond else "FAIL ") + what + (("\n     " + str(detail)) if detail and not cond else ""))
    ok = ok and bool(cond)


found, kept = [], 0
for path in FILES:
    for number, line in enumerate(path.read_text().split("\n"), 1):
        if line.lstrip().startswith("//") or not re.search("licence", line, re.I):
            continue
        for literal in LITERAL.findall(line):
            # Text only: what sits inside \( ... ) is code (an identifier may be spelled "licence").
            if not re.search("licence", re.sub(r"\\\((?:[^()]|\([^()]*\))*\)", "", literal), re.I):
                continue
            if FOCUS_ID.fullmatch(literal) or "The licence: gnu.org/licenses/gpl-3.0" in literal:
                kept += 1
            else:
                found.append(f"{path.relative_to(R)}:{number}: {literal[:100]}")
check("no BCD*.swift string a person reads says \"licence\"", not found, "\n     ".join(found))
check("what stays is the two focus ids and the credits' legal paragraph (in the app's and the preview's About)", kept == 4, kept)
check(f"{len(FILES)} BCD*.swift files read", len(FILES) > 40)
check("Settings › About words: License texts, licenses, Microsoft's license terms",
      '"License texts"' in (R / "app/Madeira/BCDSettingsAbout.swift").read_text()
      and "Version, credits and licenses" in (R / "app/Madeira/BCDSettingsCore.swift").read_text()
      and "Microsoft's license terms" in (R / "app/Madeira/BCDSettingsSections.swift").read_text())

CI = R / ".github/workflows/build-ipa.yml"
if CI.exists():
    check("CI runs this check", "run: python3 tests/host/check-bcd-spelling.py" in CI.read_text())

print("PASS: BCD spelling" if ok else "FAIL: BCD spelling")
sys.exit(0 if ok else 1)

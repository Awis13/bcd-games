#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""The notices every BCD package carries are complete and current (legal audit 2026-10-18,
docs/research/open-fork-2026-10-18/legal.md F-9):

- the shipped short notice (app/Madeira/licenses/THIRD-PARTY-NOTICES.txt) speaks for BCD,
  a fork of Madeira; the exception is in effect (no "DRAFT"); sources point to BCD's
  repository, not "the Madeira repository";
- FreeType's credit (its licence requires it in the documentation) and the IJG credit for
  libjpeg are there, with the licence texts of FreeType and of every library Wine builds
  into its modules;
- the bundled THIRD-PARTY-NOTICES.md is the repository's, word for word;
- LICENSE-BCD.md is staged into the package and the build fails when the copy is stale.
"""
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[2]
app = root / "app" / "Madeira"
failures = 0


def check(ok, what):
    global failures
    print(("PASS: " if ok else "FAIL: ") + what)
    if not ok:
        failures += 1


short = (app / "licenses" / "THIRD-PARTY-NOTICES.txt").read_text()
check(short.startswith("BCD third-party notices") and "BCD is a fork of Madeira by Will Faust and its contributors." in short,
      "the shipped notice speaks for BCD, a fork of Madeira by Will Faust and its contributors")
check(all(stale not in short for stale in ("DRAFT", "not in effect", "the Madeira repository")),
      "no stale draft wording, no 'the Madeira repository' as the source")
check("adopted by\nMadeira on 2026-09-24" in short and "LICENSE-BCD.txt" in short
      and "https://github.com/thebcd69/bcd-games" in short,
      "the exception's adoption date, BCD's terms and BCD's repository are named")
freetype = "Portions of this software are\n  copyright (C) 2024 The FreeType Project (www.freetype.org). All rights\n  reserved."
check(freetype in short and "based in part on the work of the FreeType Team" in short,
      "FreeType's credit, as its licence asks")
check("based in\npart on the work of the Independent JPEG Group" in short, "libjpeg's IJG credit")
check("BCD does not redistribute it" in short and "accepts Microsoft's license terms" in short,
      "the Visual C++ runtime is downloaded, never redistributed")
check("BCD's modifications: LGPL-2.1-or-later" in short, "BCD's changes to Wine are declared LGPL-2.1-or-later")

full = (root / "THIRD-PARTY-NOTICES.md").read_text()
bundled = (app / "legal" / "THIRD-PARTY-NOTICES.md").read_text()
check(full == bundled, "the bundled THIRD-PARTY-NOTICES.md is the repository's, word for word")
check("**BCD** is a fork of **Madeira**" in full and "| **FreeType** 2.13.3 |" in full
      and "| **Libraries inside Wine's modules** |" in full,
      "THIRD-PARTY-NOTICES.md names BCD, FreeType and the libraries inside Wine's modules")

ftl = app / "legal" / "LICENSES" / "FTL-FreeType-2.13.3.txt"
check(ftl.exists() and "The FreeType Project LICENSE" in ftl.read_text(), "FreeType's licence text is bundled")
libs = app / "legal" / "LICENSES" / "wine-libs"
expected = {"capstone", "compiler-rt", "faudio", "fluidsynth", "gsm", "jpeg", "jxr", "lcms2", "ldap", "mpg123",
            "musl", "png", "tiff", "tomcrypt", "vkd3d", "zlib"}
have = {f.name.split("-")[0] if not f.name.startswith("compiler-rt") else "compiler-rt" for f in libs.glob("*.txt")}
check(expected <= have, f"a licence file for each library inside Wine's modules ({len(have)} of {len(expected)})")
wine_libs = root / "wine" / "libs"
if wine_libs.is_dir():
    drift = []
    for lib in sorted(expected):
        for f in (wine_libs / lib).iterdir():
            if f.name.lower().startswith(("copying", "license", "licence", "copyright")):
                copy = libs / f"{lib}-{f.name}.txt"
                if not copy.exists() or copy.read_bytes() != f.read_bytes():
                    drift.append(copy.name)
    check(not drift, "the bundled copies match Wine's own licence files" + (f": stale {drift}" if drift else ""))
else:
    print("note: no wine submodule here; the copies are not compared with Wine's")

stage = (root / "build" / "stage-licenses.sh").read_text()
project = (root / "app" / "Madeira.xcodeproj" / "project.pbxproj").read_text()
gitignore = (root / ".gitignore").read_text()
check('cp "$R/LICENSE-BCD.md" "$R/app/Madeira/licenses/LICENSE-BCD.txt"' in stage
      and "LICENSE-BCD.md:LICENSE-BCD.txt" in project
      and "app/Madeira/licenses/LICENSE-BCD.txt" in gitignore,
      "LICENSE-BCD.md is staged into the package, checked by the build, and its copy is not committed")

print("check-notices:", "FAIL" if failures else "PASS")
sys.exit(1 if failures else 0)

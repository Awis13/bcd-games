# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""The app's name in text people read: BCD (owner, 2026-10-18: no "Madeira" in the app).

display(text) turns the word Madeira into BCD (and Madeira's into BCD's) where it names
the app or one of its parts ("Restart Madeira", "Madeira Dock"), and leaves it where it
is part of a path, file name or identifier: app/Madeira/..., Madeira.app, MadeiraDock,
X-Madeira-Kind, [madeira-dock]. Used by the generators of text the app shows
(gen-config-catalog.py) and to rewrite the app's own strings once.

It also gives the files BCD writes their names since 2026-10-18 (bcd.cfg, bcd-log.txt;
madeira-env.txt is gone, its lines are env. lines of bcd.cfg). The wine, DXMT and FEX
sources keep the old names in their comments, and the catalog's notes come from them.
"""
import re

_WORD = re.compile(r"(?<![A-Za-z0-9_./\-\[])Madeira(?='s|(?![A-Za-z0-9_/\-]|\.[A-Za-z0-9]))")


# Credits and legal text name the original work and its authors as they are: Madeira is Will
# Faust's, Madeira Dock is 125hz's (legal audit, 2026-10-18). These phrases never change.
_KEEP = ("Created Madeira", "fork of Madeira", "and Madeira are built on", "Madeira Dock (BCD Dock",
         "Madeira Dock starts for games", "Madeira Converter Exception", "Copyright", "SPDX-License-Identifier")


# NAME=value in Documents/madeira-env.txt, as the submodules' comments still put it.
_ENV_FILE_LINE = re.compile(r"\b([A-Z][A-Z0-9_]*)=(\S+) in (?:Documents/)?madeira-env\.txt")


def file_names(text):
    text = _ENV_FILE_LINE.sub(r"env.\1 = \2 in bcd.cfg", text)
    return text.replace("madeira.cfg", "bcd.cfg").replace("madeira-log.txt", "bcd-log.txt")


def display(text):
    text = file_names(text)
    if any(phrase in text for phrase in _KEEP):
        return text
    return _WORD.sub("BCD", text)


if __name__ == "__main__":
    cases = {"Restart Madeira": "Restart BCD", "Madeira's code": "BCD's code", "update Madeira.": "update BCD.",
             "Madeira Dock": "BCD Dock", "iOS-Madeira: no store": "iOS-Madeira: no store",
             "app/Madeira/Library.swift": "app/Madeira/Library.swift", "Madeira.app": "Madeira.app",
             "MadeiraDock": "MadeiraDock", "X-Madeira-Kind": "X-Madeira-Kind", "[madeira-dock]": "[madeira-dock]",
             "(Madeira)": "(BCD)", "\"Madeira\"": "\"BCD\"",
             "A fork of Madeira by Will Faust": "A fork of Madeira by Will Faust",
             "role: \"Created Madeira\"": "role: \"Created Madeira\"",
             "ml1122: madeira.cfg alert-spin-us (default 0)": "ml1122: bcd.cfg alert-spin-us (default 0)",
             "MADEIRA_D3D9_LAST=1 in Documents/madeira-env.txt brings it back":
                 "env.MADEIRA_D3D9_LAST = 1 in bcd.cfg brings it back",
             "dup2'd to madeira-log.txt": "dup2'd to bcd-log.txt",
             "Documents/madeira-dxgi-budget.txt = 1": "Documents/madeira-dxgi-budget.txt = 1"}
    for a, want in cases.items():
        got = display(a)
        assert got == want, (a, got, want)
    print("bcd_brand: ok")

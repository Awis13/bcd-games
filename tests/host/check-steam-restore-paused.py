#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""A paused first-time Steam install survives closing BCD (b4625bdb; owner's report, 2026-10-20).

Source checks (no Steam): the downloader saves progress.json next to the journals, never creates the
folder (a finished install removed it), every 2 s and when the install stops; the library restores only
first-time installs (journals and no appmanifest_<id>.acf, so a cancelled update of an installed game
does not come back), as Paused, at start when signed in; env.MADEIRA_STEAM_RESTORE_PAUSED = 0 turns it off.
"""
from pathlib import Path
import sys
root = Path(__file__).resolve().parents[2]
dd = (root / "app/Madeira/SwiftSteam/Content/DepotDownloader.swift").read_text()
lib = (root / "app/Madeira/SteamOwnedLibrary.swift").read_text()
ok = True


def check(what, cond):
    global ok
    print(("ok   " if cond else "FAIL ") + what)
    ok &= bool(cond)


save = dd[dd.index("static func saveSnapshot("):]
save = save[:save.index("\n    }\n")]
check("the snapshot is written without creating the folder", "createDirectory" not in save and
      "data.write(to: journalDir.appendingPathComponent(snapshotName), options: .atomic)" in save)
check("saved every 2 s and when the install stops (defer)", "now.timeIntervalSince(lastSnapshot) >= 2" in dd and
      "defer { Self.saveSnapshot(state, journalDir: journalDir) }" in dd)
part = dd[dd.index("static func partialDownloads("):]
part = part[:part.index("\n    }\n")]
check("only folders with a journal count; done bytes never exceed the total",
      "hasPartialDownload(appID: appID, steamApps: steamApps)" in part and "min(saved.doneBytes, saved.totalBytes)" in part)
rest = lib[lib.index("private func restorePartialDownloads()"):]
rest = rest[:rest.index("\n    }\n")]
check("off with env.MADEIRA_STEAM_RESTORE_PAUSED = 0", 'SteamSignIn.flag("MADEIRA_STEAM_RESTORE_PAUSED", default: true)' in rest)
check("only first-time installs (no appmanifest), never over an entry in the list",
      'appmanifest_\\(id).acf' in rest and "downloads[id] == nil" in rest)
check("restored as Paused", "Download(state: .paused, progress: progress)" in rest)
check("run at start, after the caches, when signed in", "if signedIn { loadCaches(); restorePartialDownloads() }" in lib)
print("PASS: Steam paused download restore" if ok else "FAILED: Steam paused download restore")
sys.exit(0 if ok else 1)

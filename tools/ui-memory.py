#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""Measure the memory BCD's interface holds, in the simulator (docs/BCD-UI-ARCHITECTURE.md,
Performance: games run in BCD's own process, so every megabyte the interface keeps is one a
game cannot have).

Builds the preview app (tools/ui-preview.py), starts its memory walk (-BCDMeasure, in
app/BCDPreview/BCDPreviewMeasure.swift) on one simulator and reads the app's footprint after
each step with `footprint <pid>` (a simulator app is a process of this Mac):

  home, library-top, library-end (scrolled to the end), library-top-again, in-game (a game
  started from the Library: store.inGame), after-game, home-again, game-page (a game's page
  open), game-page-closed.

    tools/ui-memory.py                          the sample library, BCD iPhone 13 Pro, landscape
    tools/ui-memory.py --games 300              300 more games, each cover its own file
    tools/ui-memory.py --games 0,300 --empty    both, and a new install's interface (the floor),
                                                one after another under one lock
    tools/ui-memory.py --games 300 --json out.json

The simulator draws on this Mac, not on an iPhone's GPU: compare runs with each other (before
and after a change), not with a device's numbers. It shares tools/ui-preview.py's lock, so runs
from several agents queue.
"""
import argparse
import fcntl
import importlib.util
import json
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("ui_preview", ROOT / "tools" / "ui-preview.py")
preview = importlib.util.module_from_spec(spec)
spec.loader.exec_module(preview)

STEPS = ["home", "library-top", "library-end", "library-top-again", "in-game", "after-game", "home-again",
         "game-page", "game-page-closed"]
# The footprint categories worth showing beside the total (the rest is summed as "other").
SHOWN = ["MALLOC_LARGE", "MALLOC_SMALL", "MALLOC_TINY", "MALLOC_NANO", "CG image", "CoreAnimation",
         "IOSurface", "IOAccelerator", "ImageIO", "untagged (VM_ALLOCATE)"]


def mib(n):
    return f"{n / 1048576:6.1f}"


def footprint(pid):
    """phys_footprint and the dirty bytes per category, from `footprint -j`."""
    with tempfile.NamedTemporaryFile(suffix=".json") as out:
        r = subprocess.run(["footprint", "-j", out.name, str(pid)], capture_output=True, text=True)
        if r.returncode:
            return None
        data = json.loads(Path(out.name).read_text() or "{}")
    procs = data.get("processes") or []
    if not procs:
        return None
    proc = procs[0]
    total = proc.get("auxiliary", {}).get("phys_footprint") or proc.get("footprint")
    cats = {}
    for name, value in (proc.get("categories") or {}).items():
        cats[name] = value.get("dirty", 0) + value.get("swapped", 0) if isinstance(value, dict) else value
    return {"footprint": total, "categories": cats}


def app_pid(udid):
    """The preview app's process id, from the simulator's launchd."""
    listing = subprocess.run(["xcrun", "simctl", "spawn", udid, "launchctl", "list"], capture_output=True, text=True).stdout
    for line in listing.splitlines():
        parts = line.split()
        if len(parts) >= 3 and preview.BUNDLE_ID in parts[2] and parts[0].isdigit():
            return int(parts[0])
    return None


def measure(udid, args, timeout):
    subprocess.run(["xcrun", "simctl", "terminate", udid, preview.BUNDLE_ID], capture_output=True)
    with tempfile.TemporaryDirectory(prefix="bcd-ui-memory-") as tmp:
        log = Path(tmp) / "out.txt"
        # `simctl launch --stdout=` writes nothing with this Xcode's simulator; --console streams
        # the app's output to simctl's own, which runs until the app ends.
        with open(log, "w") as sink:
            console = subprocess.Popen(["xcrun", "simctl", "launch", "--console", udid, preview.BUNDLE_ID] + args,
                                       stdout=sink, stderr=subprocess.STDOUT)
        pid = None
        rows, seen = [], 0
        deadline = time.time() + timeout
        while time.time() < deadline:
            pid = pid or app_pid(udid)
            text = log.read_text(errors="ignore") if log.exists() else ""
            lines = [l for l in text.splitlines() if l.startswith("BCDMeasure: ")]
            for line in lines[seen:]:
                seen += 1
                print("  " + line, flush=True)
                step = re.match(r"BCDMeasure: (\S+) footprint=(\d+)", line)
                if step:
                    rows.append({"step": step.group(1), "self": int(step.group(2)), **((pid and footprint(pid)) or {})})
            if any(l == "BCDMeasure: done" for l in lines):
                break
            if console.poll() is not None:
                print("ui-memory: the app ended before the walk finished")
                break
            time.sleep(0.3)
        else:
            print("ui-memory: the walk did not finish in time")
        subprocess.run(["xcrun", "simctl", "terminate", udid, preview.BUNDLE_ID], capture_output=True)
        try:
            console.wait(timeout=20)
        except subprocess.TimeoutExpired:
            console.kill()
    return rows


def table(rows):
    cats = [c for c in SHOWN if any(c in r.get("categories", {}) for r in rows)]
    head = f"{'step':18} {'MiB':>6} " + " ".join(f"{c[:13]:>13}" for c in cats) + f" {'other':>7}"
    print(head)
    for r in rows:
        values = r.get("categories", {})
        shown = sum(values.get(c, 0) for c in cats)
        total = r.get("footprint") or r["self"]
        print(f"{r['step']:18} {mib(total)} " + " ".join(f"{mib(values.get(c, 0)):>13}" for c in cats)
              + f" {mib(max(0, total - shown)):>7}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--device", default="BCD iPhone 13 Pro")
    ap.add_argument("--orient", default="landscape", choices=["landscape", "portrait"])
    ap.add_argument("--games", default="0", help="sample games added to the library (-BCDSampleGames); a comma list runs each")
    ap.add_argument("--empty", action="store_true", help="also (or only, with --games none) a new install's interface")
    ap.add_argument("--out", type=Path, default=Path("/tmp/bcd-ui-memory"))
    ap.add_argument("--json", type=Path, help="write the rows here")
    ap.add_argument("--timeout", type=int, default=600)
    ap.add_argument("--no-build", action="store_true", help="measure the app already built in --out")
    a = ap.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)
    sims = preview.simulators()
    if a.device not in sims:
        sys.exit(f"ui-memory: no simulator named {a.device!r} (tools/ui-preview.py --list)")
    udid = sims[a.device][0]
    runs = [(int(n), False) for n in a.games.split(",") if n.strip() and n.strip() != "none"]
    if a.empty:
        runs.append((0, True))
    lock = open("/tmp/bcd-ui-preview.lock", "w")
    if not preview._try_lock(lock):
        print("ui-memory: waiting for the simulator lock", flush=True)
        fcntl.flock(lock, fcntl.LOCK_EX)
    print("ui-memory: lock taken", flush=True)
    app = a.out / "BCDPreview.app" if a.no_build else preview.build(a.out)
    subprocess.run(["xcrun", "simctl", "boot", udid], capture_output=True)
    preview.run(["xcrun", "simctl", "bootstatus", udid, "-b"])
    preview.run(["xcrun", "simctl", "install", udid, str(app)])
    results = []
    try:
        for games, empty in runs:
            args = ["-BCDTab", "home", "-BCDOrientation", a.orient, "-BCDMeasure", "YES",
                    "-AppleLanguages", "(en)", "-AppleLocale", "en_US"]
            if games:
                args += ["-BCDSampleGames", str(games)]
            if empty:
                args += ["-BCDEmpty", "YES"]
            print(f"{a.device}: {a.orient}, {games} sample games{', empty' if empty else ''}", flush=True)
            rows = measure(udid, args, a.timeout)
            table(rows)
            missing = [s for s in STEPS if s not in {r["step"] for r in rows}]
            if missing:
                print("ui-memory: steps missing: " + ", ".join(missing))
            results.append({"device": a.device, "orient": a.orient, "games": games, "empty": empty, "rows": rows})
    finally:
        subprocess.run(["xcrun", "simctl", "shutdown", udid], capture_output=True)
    if a.json:
        a.json.write_text(json.dumps(results, indent=1))


if __name__ == "__main__":
    main()

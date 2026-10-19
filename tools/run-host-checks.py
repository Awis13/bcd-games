#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""Run every host check (tests/host/check-*.py), not only the ones the workflow lists, and
tell new failures from old ones.

    tools/run-host-checks.py                         all checks, this tree
    tools/run-host-checks.py --baseline bbfc461e     ...and every failure again at that commit
    tools/run-host-checks.py --only jit,onboarding   checks whose name contains a word

A check that fails here and also fails at the baseline is "already failing" (often one that
needs the wine/dxmt/FEX submodules, which a worktree lacks); one that passes there is "NEW":
something since the baseline broke it. Exit status 1 when there is a new failure.
Results: /tmp/bcd-host-checks/<commit>.json and a summary on stdout.
"""
import argparse
import concurrent.futures
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = Path("/tmp/bcd-host-checks")


def checks(root, only):
    found = sorted((root / "tests" / "host").glob("check-*.py"))
    if only:
        words = [w.strip() for w in only.split(",") if w.strip()]
        found = [c for c in found if any(w in c.name for w in words)]
    return found


def run_one(root, check, timeout):
    started = time.time()
    try:
        r = subprocess.run([sys.executable, str(check)], cwd=root, capture_output=True, text=True, timeout=timeout)
        status = "pass" if r.returncode == 0 else "fail"
        tail = (r.stdout + r.stderr).strip().splitlines()[-6:]
    except subprocess.TimeoutExpired:
        status, tail = "timeout", [f"no result after {timeout} s"]
    return {"check": check.name, "status": status, "seconds": round(time.time() - started, 1), "tail": tail}


def run_all(root, names, jobs, timeout, label):
    paths = [root / "tests" / "host" / n for n in names]
    results = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=jobs) as pool:
        futures = {pool.submit(run_one, root, p, timeout): p.name for p in paths if p.exists()}
        for i, done in enumerate(concurrent.futures.as_completed(futures), 1):
            r = done.result()
            results[r["check"]] = r
            print(f"[{label} {i}/{len(futures)}] {r['status']:7} {r['seconds']:6.1f}s  {r['check']}", flush=True)
    for name in names:
        if name not in results:
            results[name] = {"check": name, "status": "missing", "seconds": 0, "tail": ["not in this tree"]}
    return results


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--baseline", help="commit to run this tree's failures at, to tell new failures from old")
    ap.add_argument("--only", help="comma-separated words: run only checks whose file name contains one")
    ap.add_argument("--jobs", type=int, default=3)
    ap.add_argument("--timeout", type=int, default=900)
    a = ap.parse_args()

    head = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    names = [c.name for c in checks(ROOT, a.only)]
    print(f"run-host-checks: {len(names)} checks at {head} ({ROOT})")
    results = run_all(ROOT, names, a.jobs, a.timeout, "here")
    failing = sorted(n for n, r in results.items() if r["status"] != "pass")

    verdict = {}
    if a.baseline and failing:
        with tempfile.TemporaryDirectory(prefix="bcd-baseline-") as tmp:
            tree = Path(tmp) / "tree"
            subprocess.run(["git", "worktree", "add", "--detach", "-q", str(tree), a.baseline], cwd=ROOT, check=True)
            try:
                before = run_all(tree, failing, a.jobs, a.timeout, "baseline")
            finally:
                subprocess.run(["git", "worktree", "remove", "--force", str(tree)], cwd=ROOT)
        for n in failing:
            verdict[n] = "already failing" if before[n]["status"] != "pass" else "NEW"

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"{head}.json").write_text(json.dumps({"head": head, "baseline": a.baseline, "results": results,
                                                    "verdict": verdict}, indent=1))
    passed = sum(1 for r in results.values() if r["status"] == "pass")
    print(f"\nrun-host-checks: {passed}/{len(results)} pass at {head}")
    for n in failing:
        tag = verdict.get(n, results[n]["status"])
        print(f"  {tag:16} {n}: {results[n]['tail'][-1] if results[n]['tail'] else ''}")
    new = [n for n, v in verdict.items() if v == "NEW"]
    if new:
        print(f"\nrun-host-checks: {len(new)} NEW failure(s) since {a.baseline}:")
        for n in new:
            print(f"--- {n}\n" + "\n".join(results[n]["tail"]))
    sys.exit(1 if new else 0)


if __name__ == "__main__":
    main()

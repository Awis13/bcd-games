#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""write-buffer-immediate and query-timestamps (madeira-d3d12, both opt-in, build 593).

Marvel's Spider-Man Remastered (build 592, the owner's phone, 2026-10-23): a black screen after
the launcher. The game's Render Thread waits for a value it asked the GPU to write with
WriteBufferImmediate, which was a stub. The owner: no other game may see any change, so both
switches are off unless a game's settings turn them on, and only Spider-Man Remastered's list
and auto-tune rule do.

Checks (read from the sources):
  - write-buffer-immediate is read with default 0; off, the method is the old stub (it notes the
    call as unimplemented and returns before recording anything);
  - on, each value is recorded in list order (MC_WBI) and replayed as a 4-byte blit copy from a
    ring slot; only such a replay sets the queue's wbi_flush, and only wbi_flush adds a commit at
    the end of ExecuteCommandLists;
  - the list's vtable points WriteBufferImmediate at the new function;
  - CheckFeatureSupport still answers OPTIONS3 with all zero (no WriteBufferImmediate support
    reported), so a game that asks first sees no change;
  - query-timestamps is read with default 0; off, timestamps resolve to zero and
    GetClockCalibration's GPU value is 0, as before;
  - the recommended list: only Marvel's Spider-Man Remastered carries either key, from build 593,
    matched by its program name without "steam": true;
  - auto-tune: the rule that adds write-buffer-immediate = 1 needs Spider-Man.exe's own log lines;
  - with the mingw toolchain present, madeira_d3d12.c passes an arm64ec syntax check.
"""
import json
import os
import re
import subprocess
import sys
from pathlib import Path

R = Path(__file__).resolve().parents[2]
SRC = R / "madeira-d3d12/src/pe/madeira_d3d12.c"
fails = []


def check(what, ok, detail=""):
    print(("ok   " if ok else "FAIL ") + what + (f"\n     {detail}" if detail and not ok else ""))
    if not ok:
        fails.append(what)


c = SRC.read_text()


def func(name):
    """The body of a top-level C function, from its name to the next line that is a lone '}'."""
    m = re.search(r"\n[^\n]*\b" + re.escape(name) + r"\([^;{]*\)\s*\{", c)
    if not m:
        return ""
    end = c.find("\n}\n", m.end())
    return c[m.start():end + 3] if end > 0 else ""


wbi = func("list_WriteBufferImmediate")
check("list_WriteBufferImmediate exists", bool(wbi))
check("write-buffer-immediate is opt-in (default 0)",
      'mad_cfg_int_pe("write-buffer-immediate", 0)' in wbi and 'mad_cfg_int_pe("write-buffer-immediate", 1)' not in c)
off = wbi.find("if (!g_wbi_on)")
rec = wbi.find("mad_list_push(l, MC_WBI)")
check("off: the old stub (noted as unimplemented, returns before recording)",
      0 < off < rec and 'madeira_d3d12_note_unimplemented("ID3D12GraphicsCommandList7", "WriteBufferImmediate"); return; }' in wbi[off:rec])
check("on: the destination is resolved by address, 4-byte aligned and inside its buffer",
      "mad_resolve_address(l->device, params[i].Dest, &off)" in wbi and "(params[i].Dest & 3)" in wbi and "off + 4 > r->size" in wbi)
check("vtable: WriteBufferImmediate -> list_WriteBufferImmediate",
      re.search(r"g_list_vtbl\.WriteBufferImmediate\s*=\s*\(void \*\)list_WriteBufferImmediate;", c) is not None)

ex = func("exec_copy")
m = re.search(r"case MC_WBI: \{(.*?)\n    \}\n", ex, re.S)
body = m.group(1) if m else ""
check("replay: a ring slot holds the value, a 4-byte blit copies it",
      "exec_ring_take_z(e, 1, 0, &src, &soff, &cpu, &gpu)" in body and "memcpy(cpu, &c->u.wbi.value, 4);" in body
      and "k.copy_length = 4;" in body and "WMTBlitCommandCopyFromBufferToBuffer" in body)
check("replay: only a written value sets wbi_flush", c.count("wbi_flush = 1") == 1 and "e->q->wbi_flush = 1;" in body)
check("replay: the MC_WBI command goes through exec_copy",
      "case MC_FILL_BB: case MC_FILL_TEX: case MC_WBI: exec_copy(&e, c);" in c)
ecl = func("mad_ecl_run")
check("ExecuteCommandLists commits early only when wbi_flush is set",
      re.search(r"if \(q && q->wbi_flush\) \{.*?q->wbi_flush = 0;\s*mad_queue_flush\(q\);", ecl, re.S) is not None)

cfs = c[c.find("case D3D12_FEATURE_D3D12_OPTIONS3:"):]
check("CheckFeatureSupport: OPTIONS3 still all zero",
      cfs.startswith("case D3D12_FEATURE_D3D12_OPTIONS3:") and "memset(data, 0, size);" in cfs[:900])

qts = func("mad_qts_on")
check("query-timestamps is opt-in (default 0)", 'mad_cfg_int_pe("query-timestamps", 0)' in qts)
rqd = func("list_ResolveQueryData")
check("off: timestamps still resolve to zero",
      "type == D3D12_QUERY_TYPE_TIMESTAMP && mad_qts_on()" in rqd and "memset((char *)r->cpu + offset, 0, (size_t)(count * each));" in rqd)
gcc = func("queue_GetClockCalibration")
check("off: GetClockCalibration's GPU value is 0", "*gpu = mad_qts_on() ? mad_qts_now_ns() : 0;" in gcc)

# The recommended list: Spider-Man Remastered only.
recs = json.loads((R / "recommended/recommendations.json").read_text())
holders = {}
for rule in recs["rules"]:
    for line in rule.get("config", []):
        text = line if isinstance(line, str) else line.get("line", "")
        key = text.split("=")[0].strip()
        if key in ("write-buffer-immediate", "query-timestamps"):
            holders.setdefault(rule["id"], []).append(line)
check("list: only Marvel's Spider-Man Remastered carries the keys", set(holders) <= {"marvels-spider-man-remastered"}, str(holders))
smr = next((r for r in recs["rules"] if r["id"] == "marvels-spider-man-remastered"), None)
check("list: Spider-Man Remastered has write-buffer-immediate = 1 from build 593, matched by name",
      smr is not None and {"line": "write-buffer-immediate = 1", "minBuild": 593} in smr["config"]
      and {"name": "spider-man.exe"} in smr["match"].get("programs", []))

# Auto-tune: the rule is this game's alone.
sys.path.insert(0, str(R / "build/tools"))
import autotune_rules  # noqa: E402

adders = [r for r in autotune_rules.RULES if any(l.split("=")[0].strip() in ("write-buffer-immediate", "query-timestamps") for l in r.get("lines", []))]
ok = len(adders) == 1 and adders[0]["id"] == "d3d12-write-buffer-immediate-spider-man"
if ok:
    anys = [" ".join(s["has"]) for s in adders[0].get("any", [])]
    ok = bool(anys) and all("Spider-Man.exe" in a for a in anys)
check("auto-tune: one rule adds the key, and it needs Spider-Man.exe's log lines", ok, str([r["id"] for r in adders]))

# Syntax check with the shipped toolchain, when it is here.
mingw = R / "toolchains/llvm-mingw-20260421-ucrt-macos-universal/bin/arm64ec-w64-mingw32-clang"
if not mingw.exists():
    mingw = Path.home() / "madeira-bcd/toolchains/llvm-mingw-20260421-ucrt-macos-universal/bin/arm64ec-w64-mingw32-clang"
winemetal = R / "dxmt/src/winemetal"
if not (winemetal / "winemetal.h").exists():
    winemetal = Path.home() / "madeira-bcd/dxmt/src/winemetal"
out_pe = R / "build/madeira-d3d12/out-pe"
if not out_pe.exists():
    out_pe = Path.home() / "madeira-bcd/build/madeira-d3d12/out-pe"
if mingw.exists() and (winemetal / "winemetal.h").exists() and os.environ.get("MADEIRA_SKIP_SYNTAX") != "1":
    p = subprocess.run([str(mingw), "-fsyntax-only", "-O2", "-Wall", '-DMAD_SC_CONVERTER_ID="x"', str(SRC),
                        f"-I{SRC.parent}", f"-I{out_pe}", f"-I{R / 'madeira-d3d12/src'}", f"-I{winemetal}"],
                       capture_output=True, text=True)
    errs = [l for l in p.stderr.splitlines() if "error:" in l]
    check("madeira_d3d12.c: arm64ec syntax check", p.returncode == 0 and not errs, "\n     ".join(errs[:5]))
else:
    print("skip syntax check: no mingw toolchain or winemetal headers here")

if fails:
    print(f"FAILED: write-buffer-immediate ({len(fails)})")
    sys.exit(1)
print("PASS: write-buffer-immediate and query-timestamps (opt-in, Spider-Man Remastered only)")

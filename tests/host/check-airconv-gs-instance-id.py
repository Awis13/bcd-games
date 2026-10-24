#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""tools/patch-airconv-gs-instance-id.py: a geometry shader that declares vGSInstanceID.

ROBOBEAT (Epic Games Store, build 592, Send Log HH0KNF) aborted in handle_signature_gs on a dcl_input that is neither the 2D
vertex input nor vPrim: "Assertion failed: (0 && "unhandled geometry shader input")", exit 0x80000101. The register is
D3D11_SB_OPERAND_TYPE_INPUT_GS_INSTANCE_ID, which a [instance(N)] geometry shader declares; the converter already does the rest
of GS instancing, so the patch only lets the declaration through.

Checks, on a copy of dxmt/src (or $DXMT_SRC_DIR/src), both as it is and after tools/patch-airconv-gs-input-siv.py:
  - the patch applies, a second run changes nothing, and there is one <cstdio> include whatever the order of the two patches;
  - in handle_signature_gs, the dcl_input switch keeps its two cases (the 2D input with its assert, vPrim), adds
    D3D11_SB_OPERAND_TYPE_INPUT_GS_INSTANCE_ID with a plain `break`, and still asserts on anything else, after a [gs-input]
    line that names the register type;
  - nothing else in the file changed (the patch only adds lines, the siv patch's lines stay);
  - the premise holds in the source: the converter reads vGSInstanceID (dxbc_instructions.cpp, nt/dxbc_converter_base.cpp),
    takes it from the threadgroup position's second component, keeps dcl_gsinstances and sizes the mesh grid by it, and the
    hull shader's fork/join instance ids take a plain `break` the same way;
  - the auto-tune rule for the old crash exists, names build 598 and has no config line; a fixture proves it;
  - CI runs the patch (after the siv patch) and this check.
SKIP (exit 0) when no dxmt sources are available.
"""
import difflib
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile

R = pathlib.Path(__file__).resolve().parents[2]
src = pathlib.Path(os.environ.get("DXMT_SRC_DIR", R / "dxmt")) / "src"
SIG = "airconv/dxbc_signature.cpp"
if not (src / SIG).is_file():
    print(f"SKIP: no dxmt sources at {src} (check out the submodule or set DXMT_SRC_DIR)")
    sys.exit(0)

ok = True


def check(what, cond):
    global ok
    print(("ok   " if cond else "FAIL ") + what)
    ok = ok and bool(cond)


def run(tool, cwd):
    return subprocess.run([sys.executable, str(R / "tools" / tool)], cwd=cwd, capture_output=True, text=True)


def gs_dcl_input(text):
    gs = text[text.index("handle_signature_gs("):]
    gs = gs[:gs.index("\nvoid handle_signature(")]
    dcl = gs[gs.index("case D3D10_SB_OPCODE_DCL_INPUT: {"):gs.index("case D3D10_SB_OPCODE_DCL_OUTPUT_SGV: {")]
    return dcl[dcl.index("switch (RegType) {"):]


for order in ("alone", "after the siv patch", "before the siv patch"):
    with tempfile.TemporaryDirectory() as tmp:
        t = pathlib.Path(tmp)
        (t / "dxmt/src/airconv").mkdir(parents=True)
        shutil.copy(src / SIG, t / "dxmt/src" / SIG)
        before = (t / "dxmt/src" / SIG).read_text()
        if order == "after the siv patch":
            run("patch-airconv-gs-input-siv.py", t)
        runs = [run("patch-airconv-gs-instance-id.py", t) for _ in range(2)]
        if order == "before the siv patch":
            run("patch-airconv-gs-input-siv.py", t)
        after = (t / "dxmt/src" / SIG).read_text()
        check(f"[{order}] the patch applies", runs[0].returncode == 0 and "madeira-bcd: geometry shader instance id" in after)
        check(f"[{order}] a second run changes nothing", runs[1].returncode == 0 and "already patched" in runs[1].stdout)
        check(f"[{order}] one <cstdio> include", after.count("#include <cstdio>") == 1 or order == "before the siv patch")
        sw = gs_dcl_input(after)
        pos_input = sw.find("case microsoft::D3D10_SB_OPERAND_TYPE_INPUT: {")
        pos_prim = sw.find("case microsoft::D3D10_SB_OPERAND_TYPE_INPUT_PRIMITIVEID:")
        pos_inst = sw.find("case microsoft::D3D11_SB_OPERAND_TYPE_INPUT_GS_INSTANCE_ID:")
        pos_dflt = sw.find("default:")
        check(f"[{order}] the 2D input keeps its assert and vPrim its break",
              0 <= pos_input < sw.find("OPERAND_INDEX_2D") < pos_prim)
        check(f"[{order}] vGSInstanceID takes a plain break after vPrim, before the default",
              pos_prim < pos_inst < pos_dflt and re.search(r"GS_INSTANCE_ID:\n      break;\n    default:", sw))
        dflt = sw[pos_dflt:]
        check(f"[{order}] anything else still asserts, after a [gs-input] line that names the type",
              dflt.index("[gs-input]") < dflt.index('assert(0 && "unhandled geometry shader input")') and "(unsigned)RegType" in dflt)
        removed = [l for l in difflib.ndiff(before.splitlines(), after.splitlines()) if l.startswith("- ")]
        check(f"[{order}] the patches only add lines", not removed)

# The premise, in the unpatched sources.
def read(rel):
    p = src / rel
    return p.read_text() if p.is_file() else None


instr = read("airconv/dxbc_instructions.cpp")
base = read("airconv/nt/dxbc_converter_base.cpp")
gsconv = read("airconv/dxbc_converter_gs.cpp")
cfg = read("airconv/dxbc_converter_cfg.cpp")
sig = read(SIG)
if instr is not None:
    check("premise: vGSInstanceID is read as InputAttribute::GSInstanceId",
          re.search(r"case D3D11_SB_OPERAND_TYPE_INPUT_GS_INSTANCE_ID: \{\s*return SrcOperandAttribute\{[^}]*GSInstanceId", instr, re.S))
if base is not None:
    check("premise: that attribute loads resource_map.gs_instance_id",
          re.search(r"case InputAttribute::GSInstanceId: \{\s*Ret = res\.gs_instance_id;", base))
if gsconv is not None:
    check("premise: the id is the threadgroup position's second component",
          "resource_map.gs_instance_id = builder.CreateExtractElement(tg_position_in_grid, (uint32_t)1);" in gsconv)
    check("premise: the mesh grid is sized by gs_instance_count",
          re.search(r"CreateSetMeshProperties\(air\.getInt3\(warp_primitive_count, pGeometryStage->gs_instance_count, 1\)\)", gsconv))
if cfg is not None:
    check("premise: dcl_gsinstances is kept",
          re.search(r"case D3D11_SB_OPCODE_DCL_GS_INSTANCE_COUNT:\s*sm50_shader->gs_instance_count = Inst\.m_GSInstanceCountDecl\.InstanceCount;", cfg))
if sig is not None:
    hs = sig[sig.index("handle_signature_hs("):]
    hs = hs[:hs.index("handle_signature_ds(")] if "handle_signature_ds(" in hs else hs
    check("premise: the hull shader's fork/join instance ids need only a break",
          re.search(r"case D3D11_SB_OPERAND_TYPE_INPUT_FORK_INSTANCE_ID:\s*case D3D11_SB_OPERAND_TYPE_INPUT_JOIN_INSTANCE_ID:[^{]*\{\s*break;", hs))

# The auto-tune rule and its fixture.
import importlib.util
spec = importlib.util.spec_from_file_location("autotune_rules", R / "build/tools/autotune_rules.py")
rules = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rules)
rule = next((r for r in rules.RULES if r["id"] == "gs-input-instance-id-assert"), None)
check("the auto-tune rule names build 598 and has no config line", bool(rule) and rule.get("fixedIn") == 598 and rule.get("lines") == [])
check("the rule reads the assertion's text",
      bool(rule) and any('unhandled geometry shader input' in h for s in rule["all"] for h in s["has"]))
fx = R / "tests/fixtures/autotune/robobeat-gs-input-assert-2026-10-23.log"
check("a fixture from the ROBOBEAT log proves it", fx.is_file() and 'unhandled geometry shader input' in fx.read_text())

wf = (R / ".github/workflows/build-ipa.yml").read_text()
check("CI runs the check and the patch, the patch after the siv patch",
      "run: python3 tests/host/check-airconv-gs-instance-id.py" in wf and "run: python3 tools/patch-airconv-gs-instance-id.py" in wf and
      wf.index("run: python3 tools/patch-airconv-gs-input-siv.py") < wf.index("run: python3 tools/patch-airconv-gs-instance-id.py"))

print("PASS: airconv gs instance id" if ok else "FAILED: airconv gs instance id")
sys.exit(0 if ok else 1)

#!/usr/bin/env python3
"""tools/patch-airconv-gs-input-siv.py: geometry shader inputs with a system value.

GTA V Legacy (build 543) aborted in handle_signature_gs on a dcl_input_siv
other than SV_Position (0x80000101 before its first frame). The patch lets
SV_ClipDistance, SV_CullDistance, SV_RenderTargetArrayIndex and
SV_ViewportArrayIndex through like SV_Position.

Checks, on a copy of dxmt/src (or $DXMT_SRC_DIR/src):
  - the patch applies, a second run changes nothing;
  - in handle_signature_gs, the four names share SV_Position's `break` in the
    dcl_input_siv switch; the default still asserts, after a [gs-siv] line;
    the dcl_input_sgv default logs too;
  - nothing else in the file changed (the patch only adds lines);
  - the premise holds in the source: the vertex stage counts every
    dcl_output_siv register in max_output_register, and the GS copies
    registers 0..max_output_register of each vertex (load_vertex);
  - CI runs the patch and this check.
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


with tempfile.TemporaryDirectory() as tmp:
    t = pathlib.Path(tmp)
    (t / "dxmt/src/airconv").mkdir(parents=True)
    shutil.copy(src / SIG, t / "dxmt/src" / SIG)
    before = (t / "dxmt/src" / SIG).read_text()
    runs = [subprocess.run([sys.executable, str(R / "tools/patch-airconv-gs-input-siv.py")],
                           cwd=t, capture_output=True, text=True) for _ in range(2)]
    after = (t / "dxmt/src" / SIG).read_text()
    check("the patch applies", runs[0].returncode == 0 and "madeira-bcd: geometry shader input system values" in after)
    check("a second run changes nothing", runs[1].returncode == 0 and "already patched" in runs[1].stdout)

    gs = after[after.index("handle_signature_gs("):]
    gs = gs[:gs.index("\nvoid handle_signature(")]
    siv = gs[gs.index("case D3D10_SB_OPCODE_DCL_INPUT_SIV: {"):gs.index("case D3D10_SB_OPCODE_DCL_INPUT: {")]
    sw = siv[siv.index("switch (siv) {"):]
    names = ("D3D10_SB_NAME_POSITION", "D3D10_SB_NAME_CLIP_DISTANCE", "D3D10_SB_NAME_CULL_DISTANCE",
             "D3D10_SB_NAME_RENDER_TARGET_ARRAY_INDEX", "D3D10_SB_NAME_VIEWPORT_ARRAY_INDEX")
    pos = [sw.find(f"case {n}:") for n in names]
    first_break = sw.find("break;")
    second_break = sw.find("break;", pos[1])
    check("SV_Position keeps its own break", 0 <= pos[0] < first_break < pos[1])
    check("clip/cull distance, RT array and viewport index share one break",
          all(p > 0 for p in pos[1:]) and max(pos[1:]) < second_break < sw.find("default:"))
    dflt = sw[sw.find("default:"):]
    check("anything else still asserts, after a [gs-siv] line",
          dflt.index("[gs-siv]") < dflt.index('assert(0 && "Unexpected/unhandled geometry shader siv")'))
    sgv = gs[gs.index("case D3D10_SB_OPCODE_DCL_INPUT_SGV: {"):gs.index("case D3D10_SB_OPCODE_DCL_INPUT_SIV: {")]
    check("the dcl_input_sgv default logs too",
          sgv.index("[gs-siv]") < sgv.index('assert(0 && "Unexpected/unhandled geometry shader sgv")'))
    removed = [l for l in difflib.ndiff(before.splitlines(), after.splitlines()) if l.startswith("- ")]
    check("the patch only adds lines", not removed)

    vs = after[after.index("void handle_signature_vs("):after.index("void handle_signature_ps(")]
    vs_siv = vs[vs.index("case D3D10_SB_OPCODE_DCL_OUTPUT_SIV: {"):]
    vs_siv = vs_siv[:vs_siv.index("\n  case ")]
    check("premise: the vertex stage counts every dcl_output_siv register in max_output_register",
          re.search(r"\n    }\n    max_output_register = std::max\(reg \+ 1, max_output_register\);\n    break;", vs_siv))

gsconv = src / "airconv/dxbc_converter_gs.cpp"
if gsconv.is_file():
    g = gsconv.read_text()
    lv = g[g.index("auto load_vertex = [&](pvalue vertex_index, uint32_t vid) {"):]
    check("premise: the GS copies registers 0..max_output_register of each vertex (load_vertex)",
          "for (unsigned reg = 0; reg < pVertexStage->max_output_register; reg++) {" in lv[:400])

wf = (R / ".github/workflows/build-ipa.yml").read_text()
check("CI runs the patch and this check",
      "run: python3 tools/patch-airconv-gs-input-siv.py" in wf and
      "run: python3 tests/host/check-airconv-gs-input-siv.py" in wf)

print("PASS: airconv gs input siv" if ok else "FAILED: airconv gs input siv")
sys.exit(0 if ok else 1)

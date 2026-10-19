#!/usr/bin/env python3
"""Geometry shader inputs declared with a system value other than SV_Position.

airconv's handle_signature_gs accepted only D3D10_SB_NAME_POSITION in a
dcl_input_siv and asserted on anything else, which aborts the whole game
process (Wine reports the abort as 0x80000101). GTA V Legacy (DirectX 11,
madeira-bcd build 543, log GTA5.exe 2026-10-15 12:00:12) died that way about
7 s after start, before its first frame:
  Assertion failed: (0 && "Unexpected/unhandled geometry shader siv"),
  function handle_signature_gs, file dxbc_signature.cpp, line 1054.

A geometry shader reads its inputs from the vertex stage's whole output
register file (dxbc_converter_gs.cpp, load_vertex copies registers
0..max_output_register of every vertex), and the vertex stage counts every
dcl_output_siv register in max_output_register, culling and clip distances
included. A system value on a GS input is therefore only a name for a register
that is already there: SV_ClipDistance, SV_CullDistance,
SV_RenderTargetArrayIndex and SV_ViewportArrayIndex now take the same path as
SV_Position (nothing extra to do). Anything else still asserts, after a
[gs-siv] line naming the value; the dcl_input_sgv assert gets the same line.

Only shaders that aborted the process before change: a shader that converted
before never reached these lines. Idempotent; fails by name if an anchor
moves. Run from the repository root.
"""
import pathlib
import sys

PATH = pathlib.Path("dxmt/src/airconv/dxbc_signature.cpp")
MARKER = "madeira-bcd: geometry shader input system values"

SIV_OLD = """    switch (siv) {
    case D3D10_SB_NAME_POSITION:
      break;
    default:
      assert(0 && "Unexpected/unhandled geometry shader siv");
      break;
    }"""

SIV_NEW = """    switch (siv) {
    case D3D10_SB_NAME_POSITION:
      break;
    /* madeira-bcd: geometry shader input system values -- the GS copies the
     * vertex stage's whole output register file (load_vertex), and the vertex
     * stage counts these registers in max_output_register, so they read like
     * SV_Position: plain input registers. GTA V Legacy aborted here. */
    case D3D10_SB_NAME_CLIP_DISTANCE:
    case D3D10_SB_NAME_CULL_DISTANCE:
    case D3D10_SB_NAME_RENDER_TARGET_ARRAY_INDEX:
    case D3D10_SB_NAME_VIEWPORT_ARRAY_INDEX:
      break;
    default:
      fprintf(stderr, "[gs-siv] madeira-bcd: geometry shader input system value %u is not handled\\n", (unsigned)siv);
      assert(0 && "Unexpected/unhandled geometry shader siv");
      break;
    }"""

SGV_OLD = """    switch (sgv) {
    default:
      assert(0 && "Unexpected/unhandled geometry shader sgv");
      break;
    }"""

SGV_NEW = """    switch (sgv) {
    default:
      fprintf(stderr, "[gs-siv] madeira-bcd: geometry shader input generated value %u is not handled\\n", (unsigned)sgv);
      assert(0 && "Unexpected/unhandled geometry shader sgv");
      break;
    }"""

INC_OLD = '#include "dxbc_converter.hpp"\n'
INC_NEW = '#include "dxbc_converter.hpp"\n#include <cstdio> /* madeira-bcd: [gs-siv] */\n'


def main():
    s = PATH.read_text()
    if MARKER in s:
        print("dxbc_signature.cpp: geometry shader input system values already patched")
        return 0
    for old, what in ((SIV_OLD, "dcl_input_siv switch"), (SGV_OLD, "dcl_input_sgv switch"), (INC_OLD, "include")):
        if s.count(old) != 1:
            print(f"::error::{PATH}: geometry shader {what} anchor not found once -- dxmt moved, review this patch")
            return 1
    s = s.replace(SIV_OLD, SIV_NEW).replace(SGV_OLD, SGV_NEW).replace(INC_OLD, INC_NEW, 1)
    if MARKER not in s:
        print(f"::error::{PATH}: marker missing after the edit")
        return 1
    PATH.write_text(s)
    print("dxbc_signature.cpp: geometry shader inputs take clip/cull distance, RT array and viewport index (patched)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

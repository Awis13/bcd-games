#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""A geometry shader that declares its instance id (dcl_input vGSInstanceID).

airconv's handle_signature_gs accepted two kinds of dcl_input, the 2D vertex input and vPrim (the primitive id), and
asserted on everything else, which aborts the whole game process (Wine reports the abort as 0x80000101). ROBOBEAT (Epic
Games Store, Direct3D 11, madeira-bcd build 592, Send Log HH0KNF, 2026-10-23, iPhone18,1) died that way about 8 s after
the shaders started to convert, with
  Assertion failed: (0 && "unhandled geometry shader input"), function handle_signature_gs, file dxbc_signature.cpp, line 1082.

A Shader Model 5 geometry shader with [instance(N)] declares dcl_gsinstances N and dcl_input vGSInstanceID, the register
type D3D11_SB_OPERAND_TYPE_INPUT_GS_INSTANCE_ID. The rest of the instancing is already in the converter, which is why only
this declaration was missing:
  - dxbc_converter_cfg.cpp keeps the count (DCL_GS_INSTANCE_COUNT -> gs_instance_count);
  - dxbc_converter_gs.cpp takes the instance id from the second component of the threadgroup position in the grid
    (resource_map.gs_instance_id) and sizes the mesh grid by gs_instance_count;
  - dxbc_instructions.cpp reads vGSInstanceID as InputAttribute::GSInstanceId and nt/dxbc_converter_base.cpp loads it from
    resource_map.gs_instance_id.
The hull shader's declarations of its fork/join instance ids take the same `break` (handle_signature_hs): a declaration of a
register that is read from a function argument needs no signature work.

Only shaders that aborted the process before change: a geometry shader with this declaration never converted. Any other
register type still asserts, after a [gs-input] line that names it (the siv/sgv asserts have their [gs-siv] line).
Idempotent; fails by name if an anchor moves. Run from the repository root, after tools/patch-airconv-gs-input-siv.py (it
works without it: the <cstdio> include is added only when the file lacks it).
"""
import pathlib
import sys

PATH = pathlib.Path("dxmt/src/airconv/dxbc_signature.cpp")
MARKER = "madeira-bcd: geometry shader instance id"

DCL_OLD = """    case microsoft::D3D10_SB_OPERAND_TYPE_INPUT_PRIMITIVEID:
      break;
    default:
      assert(0 && "unhandled geometry shader input");
      break;
    }"""

DCL_NEW = """    case microsoft::D3D10_SB_OPERAND_TYPE_INPUT_PRIMITIVEID:
      break;
    /* madeira-bcd: geometry shader instance id -- dcl_input vGSInstanceID of a [instance(N)] geometry shader. The converter
     * already reads it (InputAttribute::GSInstanceId <- resource_map.gs_instance_id, the threadgroup position's second
     * component) and sizes the mesh grid by dcl_gsinstances; like the hull shader's fork/join instance ids, the
     * declaration needs nothing else. ROBOBEAT aborted here. */
    case microsoft::D3D11_SB_OPERAND_TYPE_INPUT_GS_INSTANCE_ID:
      break;
    default:
      fprintf(stderr, "[gs-input] madeira-bcd: geometry shader input register type %u is not handled\\n", (unsigned)RegType);
      assert(0 && "unhandled geometry shader input");
      break;
    }"""

INC_ANCHOR = '#include "dxbc_converter.hpp"\n'
INC_NEW = '#include "dxbc_converter.hpp"\n#include <cstdio> /* madeira-bcd: [gs-input] */\n'


def main():
    s = PATH.read_text()
    if MARKER in s:
        print("dxbc_signature.cpp: geometry shader instance id already patched")
        return 0
    if s.count(DCL_OLD) != 1:
        print(f"::error::{PATH}: geometry shader dcl_input switch anchor not found once -- dxmt moved, review this patch")
        return 1
    if "#include <cstdio>" not in s:
        if s.count(INC_ANCHOR) != 1:
            print(f"::error::{PATH}: include anchor not found once -- dxmt moved, review this patch")
            return 1
        s = s.replace(INC_ANCHOR, INC_NEW, 1)
    s = s.replace(DCL_OLD, DCL_NEW)
    if MARKER not in s:
        print(f"::error::{PATH}: marker missing after the edit")
        return 1
    PATH.write_text(s)
    print("dxbc_signature.cpp: a geometry shader may declare vGSInstanceID (patched)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

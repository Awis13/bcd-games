#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""Never hand CAMetalLayer a pixel format it refuses.

Warcraft III (war3.exe, 32-bit D3D9, build 559, log war3.exe 2026-10-16
22:24:07) created its device with a 16-bit back buffer ("CreateDevice 800x600
R5G6B5"). The D3D9 swap chain passes the back buffer's Metal format to
Presenter::changeLayerProperties, and the first Present set it on the layer:
"*** Terminating app due to uncaught exception 'CAMetalLayerInvalid', reason:
'invalid pixel format 40'" (40 = B5G6R5Unorm). X1R5G5B5 and A1R5G5B5 back
buffers (BGR5A1Unorm) end the same way. The D3D11 chain maps its formats
first (ConvertSwapChainFormat); the D3D9 one did not.

CAMetalLayer takes BGRA8Unorm(_sRGB), RGBA16Float, RGB10A2Unorm,
BGR10A2Unorm and the XR formats. The present is a render pass whose fragment
shader samples the back buffer, so the layer needs no particular format: any
other format now presents into a BGRA8Unorm layer, and the present pipeline,
built from the same layer_props_ field, matches it. Formats on the list pass
unchanged (with their swizzle bits, as before), so only a chain that would
have thrown changes. "[layer-format]" logs the first four.

Idempotent; fails by name if the anchor moves. Run from the repository root.
"""
import pathlib
import sys

PATH = pathlib.Path("dxmt/src/dxmt/dxmt_presenter.cpp")
MARKER = "madeira-bcd: layer-safe format"

OLD_FN = """bool
Presenter::changeLayerProperties(
"""

NEW_FN = """/* madeira-bcd: layer-safe format (tools/patch-dxmt-layer-format.py). Any
   other pixelFormat raises 'CAMetalLayerInvalid' and ends the app: a D3D9
   R5G6B5 back buffer (Warcraft III) did. The present samples the back buffer,
   so such a chain presents into a BGRA8Unorm layer. */
static WMTPixelFormat
madeira_layer_format(WMTPixelFormat format) {
  switch ((uint32_t)ORIGINAL_FORMAT(format)) {
  case WMTPixelFormatInvalid:
  case WMTPixelFormatBGRA8Unorm:
  case WMTPixelFormatBGRA8Unorm_sRGB:
  case WMTPixelFormatRGBA16Float:
  case WMTPixelFormatRGB10A2Unorm:
  case WMTPixelFormatBGR10A2Unorm:
  case WMTPixelFormatBGRA10_XR:
  case WMTPixelFormatBGRA10_XR_sRGB:
  case WMTPixelFormatBGR10_XR:
  case WMTPixelFormatBGR10_XR_sRGB:
    return format;
  default:
    break;
  }
  static std::atomic<uint32_t> logged{0};
  if (logged.fetch_add(1, std::memory_order_relaxed) < 4)
    WARN("[layer-format] madeira-bcd: back buffer format ", (uint32_t)format,
         " is not a CAMetalLayer format; presenting into BGRA8Unorm");
  return WMTPixelFormatBGRA8Unorm;
}

bool
Presenter::changeLayerProperties(
"""

OLD_SET = "  layer_props_.pixel_format = Forget_sRGB(format);\n"
NEW_SET = "  layer_props_.pixel_format = madeira_layer_format(Forget_sRGB(format));\n"

OLD_INC = '#include "dxmt_mem_census.hpp"\n'
NEW_INC = '#include "dxmt_mem_census.hpp"\n#include "log/log.hpp"   /* madeira-bcd: layer-safe format */\n'


def main():
    s = PATH.read_text()
    if MARKER in s:
        print("dxmt_presenter.cpp: layer-safe format already patched")
        return 0
    for old in (OLD_FN, OLD_SET, OLD_INC):
        if s.count(old) != 1:
            print(f"::error::{PATH}: anchor not found once ({old.strip()[:50]!r}) -- dxmt moved, review this patch")
            return 1
    s = s.replace(OLD_INC, NEW_INC).replace(OLD_FN, NEW_FN).replace(OLD_SET, NEW_SET)
    PATH.write_text(s)
    print("dxmt_presenter.cpp: the layer gets BGRA8Unorm for a back buffer format CAMetalLayer refuses")
    return 0


if __name__ == "__main__":
    sys.exit(main())

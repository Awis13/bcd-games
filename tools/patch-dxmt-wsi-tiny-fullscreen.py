#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""A swapchain that asks for fullscreen gets a tiny window sized to the monitor.

On Windows a swapchain that goes fullscreen (Windowed = FALSE at creation, or
SetFullscreenState(TRUE)) makes DXGI cover the output with its window: a program may
create its window at any size and leave the rest to DXGI. DXMT's headless WSI layer
(src/util/wsi_window_headless.cpp, the one the aarch64/arm64ec PE build uses) only answers
"true" from enterFullscreenMode, so such a program keeps the window it made. American Truck
Simulator (Steam 270880, iPad16,10, build 576) makes a 132x38 window -- Wine's minimum size of a
captioned window -- and draws a 1024x768 picture into it: "opens in an incredibly tiny window".
Dying Light's main window is 132x38 as well.

With this patch enterFullscreenMode gives a window whose client area is under 160x120 the
monitor's rectangle (SetWindowPos, SWP_NOZORDER | SWP_NOACTIVATE | SWP_ASYNCWINDOWPOS: a window of
another thread is not waited for) and logs one "[wsi-fullscreen]" line (4 at most). A window that
is 160x120 or larger is left alone, exactly as before, so every game that works today is
unchanged: nothing a program that asked for fullscreen means to show lives in 160x120.

Idempotent; fails by name if the anchor moves. Run from the repository root.
"""
import pathlib
import sys

PATH = pathlib.Path("dxmt/src/util/wsi_window_headless.cpp")
MARKER = "madeira-bcd: [wsi-fullscreen]"

OLD = """bool enterFullscreenMode(HMONITOR hMonitor, HWND hWindow,
                         DXMTWindowState *pState,
                         [[maybe_unused]] bool modeSwitch) {
  return true;
}
"""

NEW = """bool enterFullscreenMode(HMONITOR hMonitor, HWND hWindow,
                         DXMTWindowState *pState,
                         [[maybe_unused]] bool modeSwitch) {
  /* madeira-bcd: [wsi-fullscreen] Windows' DXGI covers the output with the window of a swapchain
   * that goes fullscreen; this layer only said yes, so a program that makes a tiny window and
   * leaves the sizing to DXGI (American Truck Simulator: 132x38 around a 1024x768 picture) kept
   * it. A window whose client area is under 160x120 gets the monitor's rectangle; any other
   * window is left as it is. */
  RECT client = {}, mon = {};
  if (hWindow && ::GetClientRect(hWindow, &client) &&
      client.right - client.left < 160 && client.bottom - client.top < 120 &&
      (getDesktopCoordinates(hMonitor, &mon) || getDesktopCoordinates(getDefaultMonitor(), &mon)) &&
      mon.right - mon.left >= 320 && mon.bottom - mon.top >= 200) {
    BOOL ok = ::SetWindowPos(hWindow, nullptr, mon.left, mon.top, mon.right - mon.left,
                             mon.bottom - mon.top,
                             SWP_NOZORDER | SWP_NOACTIVATE | SWP_ASYNCWINDOWPOS);
    static int said = 0;
    if (said++ < 4)
      WARN("[wsi-fullscreen] a fullscreen swapchain's window has a ", (int)(client.right - client.left),
           "x", (int)(client.bottom - client.top), " client area; sized to the monitor ",
           (int)(mon.right - mon.left), "x", (int)(mon.bottom - mon.top), " ok=", (int)ok);
  }
  return true;
}
"""


def main():
    s = PATH.read_text()
    if MARKER in s:
        print("wsi_window_headless.cpp: tiny fullscreen window sizing already present")
        return 0
    if s.count(OLD) != 1:
        print(f"::error::{PATH}: enterFullscreenMode anchor not found once -- dxmt moved, review this patch")
        return 1
    PATH.write_text(s.replace(OLD, NEW))
    print("wsi_window_headless.cpp: enterFullscreenMode sizes a window under 160x120 to the monitor")
    return 0


if __name__ == "__main__":
    sys.exit(main())

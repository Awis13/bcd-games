#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""tools/patch-dxmt-wsi-tiny-fullscreen.py: a fullscreen swapchain's tiny window gets the monitor's size.

American Truck Simulator (Steam 270880, iPad16,10, build 576) makes a 132x38 window and draws a 1024x768
picture into it; Windows' DXGI would cover the output with the window of a swapchain that goes fullscreen,
DXMT's headless WSI layer only says "true". Checks:
  - the new enterFullscreenMode, cut out of the patch and run on the host against stub window calls:
    a client area under 160x120 gets SetWindowPos(monitor rectangle, NOZORDER | NOACTIVATE | ASYNCWINDOWPOS)
    and one log line; a window of 160x120 or more, a failed GetClientRect, a monitor under 320x200 and a
    window without a monitor rectangle are left alone (nothing called); the answer is always true, as before;
    at most 4 log lines;
  - on a copy of dxmt/src/util/wsi_window_headless.cpp (when checked out): the patch applies, a second run
    changes nothing, and only enterFullscreenMode's body differs;
  - the patched file compiles with the arm64ec mingw compiler (when the toolchain is found);
  - CI runs this check and the patch before the DXMT builds.
"""
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile

R = pathlib.Path(__file__).resolve().parents[2]
PATCH = R / "tools/patch-dxmt-wsi-tiny-fullscreen.py"
p = PATCH.read_text()
ok = True


def check(what, cond, detail=None):
    global ok
    print(("ok   " if cond else "FAIL ") + what + ("" if cond or detail is None else f"  [{detail}]"))
    ok = ok and bool(cond)


new_fn = re.search(r'NEW = """(.*?)"""', p, re.S).group(1)
old_fn = re.search(r'OLD = """(.*?)"""', p, re.S).group(1)

cxx = shutil.which("c++") or shutil.which("clang++") or shutil.which("g++")
if not cxx:
    print("skip: no C++ compiler")
else:
    harness = r"""
#include <cstdio>
#include <cstdint>
#include <cstdlib>
#include <string>
typedef int BOOL; typedef long LONG; typedef void *HWND; typedef void *HMONITOR; typedef unsigned UINT;
struct RECT { LONG left, top, right, bottom; };
struct DXMTWindowState {};
#define SWP_NOZORDER 0x4
#define SWP_NOACTIVATE 0x10
#define SWP_ASYNCWINDOWPOS 0x4000
static int g_client_ok, g_cw, g_ch, g_mon_ok, g_mon_default_ok, g_mw, g_mh, g_calls, g_logs;
static int g_x, g_y, g_w, g_h; static UINT g_flags; static void *g_hwnd;
static int g_default_monitor_asked;
BOOL GetClientRect_stub(HWND, RECT *r) { if (!g_client_ok) return 0; *r = {0, 0, g_cw, g_ch}; return 1; }
BOOL SetWindowPos_stub(HWND h, HWND, int x, int y, int w, int hh, UINT f) { g_calls++; g_hwnd = h; g_x = x; g_y = y; g_w = w; g_h = hh; g_flags = f; return 1; }
namespace dxmt { namespace str { template <typename... A> std::string format(A... a) { (void)sizeof...(a); return "x"; } } }
struct Logger { static void warn(const std::string &) { g_logs++; } };
#define WARN(...) Logger::warn(dxmt::str::format(__VA_ARGS__))
#define GetClientRect GetClientRect_stub
#define SetWindowPos SetWindowPos_stub
namespace dxmt { namespace wsi {
static HMONITOR const kMon = reinterpret_cast<HMONITOR>(1);
static HMONITOR const kOther = reinterpret_cast<HMONITOR>(2);
HMONITOR getDefaultMonitor() { g_default_monitor_asked++; return kMon; }
bool getDesktopCoordinates(HMONITOR m, RECT *r) {
  if (m == kMon && g_mon_ok) { *r = {0, 0, g_mw, g_mh}; return true; }
  if (m == kOther && g_mon_default_ok) { *r = {0, 0, g_mw, g_mh}; return true; }
  return false;
}
""" + new_fn.replace("::GetClientRect", "GetClientRect").replace("::SetWindowPos", "SetWindowPos") + r"""
} }
int main(int argc, char **argv) {
  using namespace dxmt::wsi;
  // args: client_ok cw ch mon_ok mw mh [monitor: 0 = known, 1 = unknown but the default one is]
  g_client_ok = atoi(argv[1]); g_cw = atoi(argv[2]); g_ch = atoi(argv[3]); g_mon_ok = atoi(argv[4]);
  g_mw = atoi(argv[5]); g_mh = atoi(argv[6]);
  HMONITOR m = atoi(argv[7]) ? kOther : kMon;
  g_mon_default_ok = 0;   // the stub knows kMon (the default monitor) through g_mon_ok, and kOther never
  DXMTWindowState st;
  bool r = enterFullscreenMode(m, (HWND)0x2006a, &st, false);
  printf("%d %d %d %d %d %d %u %d\n", r, g_calls, g_x, g_y, g_w, g_h, g_flags, g_logs);
  // a second and later calls: the log stops at 4
  for (int i = 0; i < 9; i++) enterFullscreenMode(m, (HWND)0x2006a, &st, false);
  printf("logs %d\n", g_logs);
  return 0;
}
"""
    with tempfile.TemporaryDirectory() as t:
        src = pathlib.Path(t) / "fs.cpp"
        src.write_text(harness)
        exe = pathlib.Path(t) / "fs"
        b = subprocess.run([cxx, "-std=c++17", "-Wall", "-Wno-unused-function", "-Wno-unused-parameter",
                            "-fsanitize=address,undefined", str(src), "-o", str(exe)], capture_output=True, text=True)
        check("the cut-out function compiles on the host", b.returncode == 0)
        if b.returncode:
            print(b.stderr[-3000:])
        else:
            def run(*args):
                r = subprocess.run([str(exe)] + [str(a) for a in args], capture_output=True, text=True)
                assert r.returncode == 0, (args, r.stdout, r.stderr)
                return r.stdout.splitlines()

            FLAGS = 0x4 | 0x10 | 0x4000
            # client_ok cw ch mon_ok mw mh other  ->  "answer calls x y w h flags logs"
            out = run(1, 132, 38, 1, 1024, 768, 0)
            check("American Truck Simulator's 132x38 window is sized to the 1024x768 monitor, asynchronously, once, one line",
                  out[0] == f"1 1 0 0 1024 768 {FLAGS} 1", out)
            check("the log stops at 4 lines", out[1] == "logs 4", out)
            out = run(1, 126, 6, 1, 1408, 648, 0)
            check("Dying Light's 126x6 client area is sized to its 1408x648 monitor", out[0] == f"1 1 0 0 1408 648 {FLAGS} 1", out)
            out = run(1, 159, 119, 1, 1024, 768, 0)
            check("159x119 is still tiny", out[0].split()[1] == "1", out)
            for what, args in (("160x120", (1, 160, 120, 1, 1024, 768, 0)), ("160 wide", (1, 160, 38, 1, 1024, 768, 0)),
                               ("120 high", (1, 132, 120, 1, 1024, 768, 0)),
                               ("a working game's monitor-sized window", (1, 1024, 768, 1, 1024, 768, 0)),
                               ("a failed GetClientRect", (0, 132, 38, 1, 1024, 768, 0)),
                               ("a monitor under 320 wide", (1, 132, 38, 1, 319, 768, 0)),
                               ("a monitor under 200 high", (1, 132, 38, 1, 1024, 199, 0)),
                               ("a monitor without a rectangle", (1, 132, 38, 0, 1024, 768, 0)),
                               ("a monitor handle nobody knows and no default monitor rectangle", (1, 132, 38, 0, 1024, 768, 1))):
                out = run(*args)
                check(f"{what}: the window is left alone, the answer stays true",
                      out[0].split()[0] == "1" and out[0].split()[1] == "0", out)
            out = run(1, 132, 38, 1, 1024, 768, 1)
            check("a monitor handle nobody knows falls back to the default monitor's rectangle",
                  out[0] == f"1 1 0 0 1024 768 {FLAGS} 1", out)

# --- the patch on the pinned source ---------------------------------------------------------------------------
src_dir = pathlib.Path(os.environ.get("DXMT_SRC_DIR", R / "dxmt"))
target = src_dir / "src/util/wsi_window_headless.cpp"
if not target.is_file():
    print(f"skip: {target} is not checked out")
else:
    with tempfile.TemporaryDirectory() as t:
        work = pathlib.Path(t)
        (work / "dxmt/src/util").mkdir(parents=True)
        orig = target.read_text()
        (work / "dxmt/src/util/wsi_window_headless.cpp").write_text(orig)

        def patch():
            return subprocess.run([sys.executable, str(PATCH)], cwd=work, capture_output=True, text=True)

        a = patch()
        patched = (work / "dxmt/src/util/wsi_window_headless.cpp").read_text()
        check("the patch applies to the pinned source", a.returncode == 0 and patched != orig and "[wsi-fullscreen]" in patched)
        b2 = patch()
        check("a second run changes nothing", b2.returncode == 0 and (work / "dxmt/src/util/wsi_window_headless.cpp").read_text() == patched)
        check("only enterFullscreenMode's body differs", patched == orig.replace(old_fn, new_fn) and orig.count(old_fn) == 1)
        (work / "dxmt/src/util/wsi_window_headless.cpp").write_text(orig.replace("enterFullscreenMode", "enterFullscreenModeX"))
        c = patch()
        check("a moved anchor fails by name", c.returncode == 1 and "anchor not found" in c.stdout)
        mingw = os.environ.get("MINGW_CXX")
        if not mingw:
            for pattern in ("toolchains/llvm-mingw-*/bin/arm64ec-w64-mingw32-clang++",):
                found = sorted(R.glob(pattern)) + sorted(pathlib.Path(os.path.expanduser("~/madeira-bcd")).glob(pattern))
                if found:
                    mingw = str(found[0])
                    break
        if mingw:
            (work / "dxmt/src/util/wsi_window_headless.cpp").write_text(patched)
            r = subprocess.run([mingw, "-std=c++20", "-fsyntax-only", "-DDXMT_IOS", "-I", str(src_dir / "src/util"),
                                "-I", str(src_dir / "src"), "-I", str(src_dir / "include"), "-I", str(src_dir),
                                str(work / "dxmt/src/util/wsi_window_headless.cpp")], capture_output=True, text=True)
            check("the patched file compiles with the arm64ec mingw compiler", r.returncode == 0)
            if r.returncode:
                print(r.stderr[-3000:])
        else:
            print("skip: no arm64ec mingw compiler found (set MINGW_CXX)")

wf = (R / ".github/workflows/build-ipa.yml").read_text()
check("CI runs this check and the patch", "python3 tests/host/check-dxmt-wsi-tiny-fullscreen.py" in wf
      and "python3 tools/patch-dxmt-wsi-tiny-fullscreen.py" in wf)
check("the patch comes after the DXMT checkout step and before the DXMT builds",
      wf.index("python3 tools/patch-dxmt-wsi-tiny-fullscreen.py") < wf.index("- name: Build dxmt-ios"))

print("PASS: a fullscreen swapchain's window under 160x120 gets the monitor's size, nothing else changes" if ok else "FAILED")
sys.exit(0 if ok else 1)

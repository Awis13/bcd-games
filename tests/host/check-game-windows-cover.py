#!/usr/bin/env python3
"""MADEIRA_GAME_WINDOWS=2 (opt-in): game mode also draws a GDI window that covers the whole guest desktop.

Source checks only (Winios.m is Objective-C for iOS). Unset or 1 keeps the rule that a game's
desktop-covering window is never drawn; 2 draws it as long as it does not present through Metal
(Warcraft II: Battle.net Edition draws with GDI and ran with sound and no picture); 0 still turns the
overlay off, and win32u treats 2 as on.
"""
from pathlib import Path
import sys

root = Path(__file__).resolve().parents[2]
m = (root / "app/Madeira/Winios/Winios.m").read_text()
drv = (root / "build/win32u-unix/driver_ios.c").read_text()
bad = 0

def check(what, ok):
    global bad
    print(("ok   " if ok else "FAIL ") + what)
    if not ok:
        bad = 1

def body(sig):
    a = m.index(sig)
    return m[a:m.index("\n}\n", a) + 3]

cover = body("static int winios_game_windows_cover(void) {")
shown = body("static BOOL winios_game_window_shown(NSNumber *key) {")
enabled = body("static int winios_game_windows_enabled(void) {")
check("2 is read from MADEIRA_GAME_WINDOWS, nothing else turns it on",
      'getenv("MADEIRA_GAME_WINDOWS")' in cover and "return e && *e == '2';" in cover)
check("a desktop-covering window is drawn only with 2, and never one that presents through Metal",
      "if (!metal && rv && (!winios_covers_desktop(rv.CGRectValue) || winios_game_windows_cover())) return YES;" in shown
      and "BOOL metal = [g_game_metal containsObject:key];" in shown)
check("0 still turns the overlay off; anything else (1, 2, unset) keeps it on",
      "return !(e && *e == '0');" in enabled)
check("win32u hands every top-level window to the app in game mode with 2 as with 1",
      "on = !winios_desktop_mode() && !(env && *env == '0');" in drv)
check("the comment documents the switch", "MADEIRA_GAME_WINDOWS=2 (opt-in, a game's own config line)" in m)
print("FAILED" if bad else "PASS: game windows cover switch")
sys.exit(bad)

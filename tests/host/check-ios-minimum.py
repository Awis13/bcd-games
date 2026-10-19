#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""The app runs from iOS 18 (owner, 2026-10-18: "18 ve yukarısı olarak da verebiliriz").

It required iOS 26 until 2026-10-18 (testers on older iOS could not install it, and LiveContainer,
which checks no minimum, started it into a crash). iOS 17 was tried the same day and dropped: the
engine's Metal 3.2 shaders, residency sets and MTLCopyAllDevices all need iOS 18. Checks:
  - the project-level deployment target is 18.0; MemoryHost sets 17.0 (it runs on 18 too);
    only the JIT helper extension, upstream's too, stays at 26.0;
The compiler enforces the rest: an iOS 26 API outside `#available` fails the build.
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
lines = (ROOT / "app/Madeira.xcodeproj/project.pbxproj").read_text().split("\n")
project = [l.strip() for i, l in enumerate(lines[:-1])
           if "IPHONEOS_DEPLOYMENT_TARGET" in l and "MADEIRA_BUNDLE_IDENTIFIER" in lines[i + 1]]
assert project == ["IPHONEOS_DEPLOYMENT_TARGET = 18.0;"] * 2, project
helper = [l.strip() for i, l in enumerate(lines) if "IPHONEOS_DEPLOYMENT_TARGET" in l
          and any("JITHelper" in x for x in lines[i:i + 15])]
assert set(helper) == {"IPHONEOS_DEPLOYMENT_TARGET = 26.0;"}, helper
all_targets = {l.strip() for l in lines if "IPHONEOS_DEPLOYMENT_TARGET" in l}
assert all_targets <= {"IPHONEOS_DEPLOYMENT_TARGET = 17.0;", "IPHONEOS_DEPLOYMENT_TARGET = 18.0;", "IPHONEOS_DEPLOYMENT_TARGET = 26.0;"}, all_targets
print("PASS: the app's minimum is iOS 18.0; only the JIT helper extension needs iOS 26")

# Wave 3 (2026-10-19): HomeView.swift's Liquid Glass calls (and the file's home screen) are deleted; the compiler
# enforces the rest: an iOS 26 API outside `#available` fails the build against the 18.0 target checked above.

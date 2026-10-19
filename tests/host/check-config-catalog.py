#!/usr/bin/env python3
"""Every option the code reads must be in Settings › All settings.

Regenerates the catalog in memory from the sources (build/tools/gen-config-catalog.py)
and fails when app/Madeira/ConfigCatalog.generated.swift differs, so a new
bcd.cfg key or env switch cannot be added without appearing in Settings.
Also checks the hand-built Memory & sync rows: swap sizes include 3 GB and the
coverage picker defaults to large allocations only (classic); JIT pool and
video memory have pickers of their own. Checks every generated kind against
the real Swift enum, and type-checks the production model and generated data
when swiftc is available (including the macOS CI runner)."""
import os, re, shutil, subprocess, sys, tempfile
R = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
rc = subprocess.run([sys.executable, os.path.join(R, "build/tools/gen-config-catalog.py"), "--check"]).returncode
lib = open(os.path.join(R, "app/Madeira/Library.swift")).read()
live = open(os.path.join(R, "app/Madeira/BCDSettingsLive.swift")).read()   # BCD's Settings (the classic Memory & sync rows are deleted)
gen = open(os.path.join(R, "app/Madeira/ConfigCatalog.generated.swift")).read()
ok = rc == 0
model = open(os.path.join(R, "app/Madeira/ConfigCatalog.swift")).read()
kind = re.search(r'\benum Kind\s*\{([^}]+)\}', model)
assert kind is not None, "ConfigOption.Kind declaration not found"
valid_kinds = {name.strip() for case in re.findall(r'\bcase\s+([^;\n}]+)', kind[1])
               for name in case.split(',')}
generated_kinds = set(re.findall(r'\bkind:\s*\.([A-Za-z_][A-Za-z0-9_]*)', gen))
assert valid_kinds and generated_kinds, "No catalog kinds were checked"
invalid_kinds = generated_kinds - valid_kinds
print(("FAIL " if invalid_kinds else "ok   ") + "catalog kinds match ConfigOption.Kind" +
      (": " + ", ".join(sorted(invalid_kinds)) if invalid_kinds else ""))
ok &= not invalid_kinds
swiftc = shutil.which("swiftc")
if swiftc:
    # These are the actual production models before the SwiftUI view begins,
    # rather than a test copy of the enum that could drift with the generator.
    start = model.index("struct ConfigOption: Identifiable {")
    # Wave 3 (2026-10-19): the classic All settings screen (AllSettingsView) that followed the models is deleted.
    marker = "/// Settings › All settings:"
    end = model.index(marker, start) if marker in model else len(model)
    with tempfile.TemporaryDirectory() as directory:
        models = os.path.join(directory, "ConfigModels.swift")
        with open(models, "w") as output:
            output.write(model[start:end])
        result = subprocess.run([swiftc, "-typecheck", models,
                                 os.path.join(R, "app/Madeira/ConfigCatalog.generated.swift")])
        print(("ok   " if result.returncode == 0 else "FAIL ") + "production Swift catalog type-check")
        ok &= result.returncode == 0
elif sys.platform == "darwin":
    print("FAIL swiftc unavailable on the macOS build runner")
    ok = False
else:
    print("note: swiftc unavailable; Swift type-check will run in macOS CI")
for what, cond in [
    ("swap sizes include 3072", "swapChoices = [0, 1024, 2048, 3072, 4096]" in lib),
    ("coverage picker: large-only default, blocks, wide", '("", "Large allocations (8 MB+)")' in lib and '("blocks"' in lib and '("wide"' in lib),
    ("JIT pool and video memory choices in Memory & performance", 'static let pool = "pool"' in live and 'static let videoMemory = "vram-mb"' in live),
    ("eco mode toggle in Memory & performance, off unless set", 'MadeiraConfig.set(BCDLiveKey.eco, on ? "1" : nil)' in live and 'MadeiraConfig.bool(BCDLiveKey.eco, default: false)' in live),
    ("catalog lists swap-mb with a 3 GB choice", re.search(r'key: "swap-mb".*\("3072", "3 GB"\)', gen) is not None),
    ("catalog lists env.MADEIRA_SWAP_COVERAGE", 'key: "env.MADEIRA_SWAP_COVERAGE"' in gen),
]:
    print(("ok   " if cond else "FAIL ") + what)
    ok &= cond
print("PASS" if ok else "FAILED")
sys.exit(0 if ok else 1)

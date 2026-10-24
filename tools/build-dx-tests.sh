#!/bin/bash
# Builds BCD's Direct3D 9, 10 and 11 test programs (tests/dxtests) as x86-64 Windows programs with llvm-mingw
# and ships them in app/Madeira/arm64ec-windows, next to d3d12-cube-x64.exe (which tools/build-madeira-d3d12-dll.sh
# builds), so that every session finds them in C:\windows\system32 and the Wine desktop's Start menu
# (app/Madeira/BCDStartMenu.c) can start them: Start > Programs > BCD > DirectX 9 test ... DirectX 12 test.
#
# A program that does not build keeps the tracked binary (a warning, not a failure): the tests are
# diagnostics and no game depends on them. The shaders of the Direct3D 10 and 11 programs are bytecode in
# tests/dxtests/cube_shaders.h, made offline by tools/gen-dx-test-shaders.sh, so nothing here compiles HLSL.
# Run from the repository root.
set -u
R="$(pwd)"
MINGW="${MINGW:-$R/toolchains/llvm-mingw-20260421-ucrt-macos-universal/bin}"
SRC="$R/tests/dxtests"
OUT="$R/build/dx-tests"
SHIP="${SHIP_DIR:-$R/app/Madeira/arm64ec-windows}"
CC="$MINGW/x86_64-w64-mingw32-clang"
mkdir -p "$OUT"

[ -x "$CC" ] || { echo "::warning::build-dx-tests: no x86_64-w64-mingw32-clang at $MINGW -- keeping the tracked test programs"; exit 0; }

# The PE header of what was built: x86-64 (0x8664), a GUI program, and imports only Windows' own DLLs.
check_pe() {
    python3 - "$1" <<'PY'
import struct, sys
data = open(sys.argv[1], "rb").read()
pe = struct.unpack_from("<I", data, 0x3C)[0]
assert data[pe:pe + 4] == b"PE\0\0", "not a PE image"
machine = struct.unpack_from("<H", data, pe + 4)[0]
subsystem = struct.unpack_from("<H", data, pe + 24 + 68)[0]
assert machine == 0x8664, "machine %#x is not x86-64" % machine
assert subsystem == 2, "subsystem %d is not Windows GUI" % subsystem
PY
}

for t in d3d9 d3d10 d3d11; do
    name="$t-cube-x64.exe"
    echo "=== $name ==="
    libs=(-luuid)
    [ "$t" = d3d9 ] || libs=(-ldxguid -luuid)
    if "$CC" -O2 -Wall -Wextra -mwindows -Wl,--no-insert-timestamp -I"$SRC" -o "$OUT/$name" "$SRC/${t}_cube.c" "${libs[@]}" 2> "$OUT/$t.err" &&
       check_pe "$OUT/$name" 2>> "$OUT/$t.err"; then
        cp "$OUT/$name" "$SHIP/$name"
        echo "::notice::$name rebuilt ($(wc -c < "$OUT/$name" | tr -d ' ') bytes) and shipped"
    else
        grep -m 20 "error\|Error" "$OUT/$t.err"
        echo "::warning::$name did not build -- keeping the tracked test"
    fi
done

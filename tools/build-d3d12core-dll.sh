#!/bin/bash
# Build d3d12core.dll (arm64ec) from build/d3d12core and ship it next to the
# other arm64ec Windows DLLs: the Agility SDK half of D3D12 (D3D12SDKVersion
# plus forwarders to d3d12.dll). Nothing loads it unless bcd.cfg
# d3d12-core-dll is set (madeira-d3d12's d3d12.dll then loads it at process
# attach). Run from the repository root.
set -eu
R="$(pwd)"
MINGW="${MINGW:-$R/toolchains/llvm-mingw-20260421-ucrt-macos-universal/bin}"
SRC="$R/build/d3d12core"
OUT="$R/build/d3d12core/out"
SHIP="$R/app/Madeira/arm64ec-windows"
mkdir -p "$OUT"

"$MINGW/arm64ec-w64-mingw32-clang" -shared -O2 -Wall \
    -o "$OUT/d3d12core.dll" "$SRC/d3d12core.c" "$SRC/d3d12core.def"

want=$(awk 'f && NF { print $1 } /^EXPORTS/ { f = 1 }' "$SRC/d3d12core.def" | sort)
have=$("$MINGW/llvm-readobj" --coff-exports "$OUT/d3d12core.dll" | awk '$1 == "Name:" { print $2 }' | sort)
if [ "$want" != "$have" ]; then
    echo "::error::d3d12core.dll exports do not match d3d12core.def"
    diff <(echo "$want") <(echo "$have") || true
    exit 1
fi
fwd=$("$MINGW/llvm-readobj" --coff-exports "$OUT/d3d12core.dll" | grep -c "ForwardedTo: d3d12\." || true)
if [ "$fwd" != "$(($(echo "$want" | wc -l) - 1))" ]; then
    echo "::error::d3d12core.dll: $fwd forwarders to d3d12.dll, expected every export but D3D12SDKVersion"
    exit 1
fi

cp "$OUT/d3d12core.dll" "$SHIP/d3d12core.dll"
echo "::notice::d3d12core.dll built ($(wc -c < "$OUT/d3d12core.dll" | tr -d ' ') bytes, D3D12SDKVersion + $fwd forwarders to d3d12.dll) and shipped; bcd.cfg d3d12-core-dll loads it"

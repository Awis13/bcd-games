#!/bin/bash
# Build a Linux (x86_64) host copy of DXMT's shader converter, airconv, from
# this repository's dxmt submodule with the CI patch chain applied, plus a
# DXBC disassembler / HLSL compiler. For reading and converting game shaders
# on a PC: what the device converts, with any MADEIRA_* converter switch set
# in the environment (docs/gow-darkening.md, "Analysing the dumps").
#
#   tools/host-airconv.sh [outdir]          (default: $CACHE/out, outside the repository)
#
# Then:
#   outdir/airconv -S shader.dxbc -o shader.ll        LLVM IR of the Metal shader
#   MADEIRA_TGSM_SYNC=1 outdir/airconv -S ...          the same with a switch
#   outdir/vkd3d-compiler -x dxbc-tpf -b d3d-asm shader.dxbc   DXBC disassembly
#   outdir/vkd3d-compiler -x hlsl -p cs_5_0 -b dxbc-tpf -o t.dxbc t.hlsl
#   outdir/opt -passes=verify -disable-output t.ll    (drop the "target triple" line first)
#
# Downloads into $CACHE (default ~/.cache/madeira-host-airconv), nothing into
# the repository: LLVM 15.0.6 prebuilt for Linux (~800 MB, the LLVM major
# airconv is built against on iOS), misyltoad/mingw-directx-headers (the
# dxmt include/native/directx submodule), and Ubuntu's vkd3d-compiler 2.0 with
# the glibc 2.43 it needs (run through that glibc's loader). DXMT_SRC_DIR
# points at another unpatched dxmt checkout (a directory holding src/,
# include/, libs/) when the submodule is not checked out.
#
# Host-only differences: the converter's embedded Metal helper libraries
# (air_msad, air_samplepos, air_tessellation; built with Apple's metal tool in
# CI) are empty stubs, so shaders using msad / samplepos / tessellation print
# "Failed to parse air bitcode"; and `CreateExtractElement(v, 0ull)` calls are
# rewritten to `(uint64_t)0` because uint64_t is unsigned long on Linux.
set -eu
R="$(cd "$(dirname "$0")/.." && pwd)"
CACHE="${CACHE:-$HOME/.cache/madeira-host-airconv}"
OUT="$(mkdir -p "${1:-$CACHE/out}" && cd "${1:-$CACHE/out}" && pwd)"
D="${DXMT_SRC_DIR:-$R/dxmt}"
mkdir -p "$CACHE"
[ -d "$D/src/airconv" ] || { echo "no dxmt sources at $D (check out the submodule or set DXMT_SRC_DIR)"; exit 1; }

# --- LLVM 15 ------------------------------------------------------------------
L="$CACHE/llvm15"
if [ ! -x "$L/bin/llvm-config" ]; then
  echo "== downloading LLVM 15.0.6 (Linux x86_64 prebuilt)"
  curl -fL -o "$CACHE/llvm.tar.xz" \
    https://github.com/llvm/llvm-project/releases/download/llvmorg-15.0.6/clang+llvm-15.0.6-x86_64-linux-gnu-ubuntu-18.04.tar.xz
  mkdir -p "$CACHE/llvm-x"
  tar -xJf "$CACHE/llvm.tar.xz" -C "$CACHE/llvm-x" --wildcards '*/include/*' '*/lib/libLLVM*.a' \
    '*/bin/llvm-config' '*/bin/llvm-dis' '*/bin/opt'
  rm -rf "$L" && mv "$CACHE"/llvm-x/clang+llvm-15.0.6-* "$L" && rm -rf "$CACHE/llvm-x" "$CACHE/llvm.tar.xz"
fi

# --- DirectX headers (dxmt include/native/directx) ------------------------------
if [ ! -f "$CACHE/dxheaders/d3d11.h" ]; then
  rm -rf "$CACHE/dxheaders"
  git clone -q --depth 1 https://github.com/misyltoad/mingw-directx-headers "$CACHE/dxheaders"
fi

# --- patched source tree ----------------------------------------------------
T="$OUT/tree"
rm -rf "$T" && mkdir -p "$T/dxmt" "$T/build" "$T/research"
cp -r "$D/src" "$D/include" "$D/libs" "$T/dxmt/"
mkdir -p "$T/dxmt/include/native/directx"
cp -r "$CACHE"/dxheaders/* "$T/dxmt/include/native/directx/"
cp "$R/build/madeira_cfg.h" "$T/build/"
cp -r "$R/research/remote-metal" "$T/research/"
python3 - "$R" "$T" <<'EOF'
import pathlib, re, subprocess, sys
R, T = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])
wf = (R / ".github/workflows/build-ipa.yml").read_text()
for s in re.findall(r"python3 (tools/patch-(?:dxmt|airconv|winemetal)-[\w.-]+\.py)", wf):
    p = subprocess.run([sys.executable, str(R / s)], cwd=T, capture_output=True, text=True)
    if p.returncode:
        sys.exit(f"{s} failed:\n{p.stdout}{p.stderr}")
# host only: Linux uint64_t is unsigned long, so CreateExtractElement(v, 0ull) is ambiguous
root = T / "dxmt/src/airconv"
for f in list(root.glob("*.cpp")) + list(root.glob("nt/*.cpp")) + list(root.glob("transforms/*.cpp")):
    s = f.read_text()
    t = re.sub(r"([,(]\s*)(\d+)ull\)", lambda m: f"{m.group(1)}(uint64_t){m.group(2)})", s)
    if t != s:
        f.write_text(t)
print("patch chain applied")
EOF

mkdir -p "$OUT/shader-headers" "$OUT/obj"
for s in air_msad air_samplepos air_tessellation; do
  printf 'unsigned char %s[] = {0};\nunsigned int %s_len = 1;\n' "$s" "$s" > "$OUT/shader-headers/$s.h"
done

# --- compile + link -------------------------------------------------------------
S="$T/dxmt/src"
INC="-I$T/dxmt/include -I$T/dxmt/libs -I$S/winemetal -I$S/airconv -I$T/dxmt/include/native/directx \
 -I$T/dxmt/include/native/windows -I$OUT/shader-headers -I$L/include"
FLAGS="-std=c++20 -O1 -g0 -w -fno-rtti -include optional -include cstdint -include string \
 -D_FILE_OFFSET_BITS=64 -D__STDC_CONSTANT_MACROS -D__STDC_FORMAT_MACROS -D__STDC_LIMIT_MACROS"
pids=()
cc1() { clang++ $FLAGS $INC $3 -c "$1" -o "$OUT/obj/$2.o" 2> "$OUT/obj/$2.err" || { echo "FAILED $2"; head -20 "$OUT/obj/$2.err"; exit 1; }; }
for f in airconv_context air_type air_signature air_operations dxbc_converter dxbc_converter_gs dxbc_converter_ts \
         dxbc_converter_basicblock dxbc_converter_cfg dxbc_instructions dxbc_signature metallib_writer airconv_cli; do
  cc1 "$S/airconv/$f.cpp" "$f" -fno-exceptions & pids+=($!)
done
cc1 "$S/airconv/nt/air_builder.cpp" air_builder -fno-exceptions & pids+=($!)
cc1 "$S/airconv/nt/dxbc_converter_base.cpp" dxbc_converter_base -fno-exceptions & pids+=($!)
cc1 "$S/airconv/transforms/lower_16bit_texread.cpp" lower_16bit_texread -fno-exceptions & pids+=($!)
for f in BlobContainer DXBCUtils ShaderBinary; do
  cc1 "$T/dxmt/libs/DXBCParser/$f.cpp" "dxbc_$f" "" & pids+=($!)
done
for p in "${pids[@]}"; do wait "$p" || { echo "compile failed"; exit 1; }; done
clang++ -o "$OUT/airconv" "$OUT"/obj/*.o -L"$L/lib" $("$L/bin/llvm-config" --libs bitwriter bitreader passes linker irreader) \
  -lpthread -ldl -lz -lm
ln -sf "$L/bin/opt" "$OUT/opt"
ln -sf "$L/bin/llvm-dis" "$OUT/llvm-dis"
echo "== $OUT/airconv built"

# --- vkd3d-compiler 2.0 (HLSL -> DXBC, DXBC disassembly) ------------------------
V="$CACHE/vkd3d"
if [ ! -x "$V/root/usr/bin/vkd3d-compiler" ]; then
  mkdir -p "$V"
  P=http://archive.ubuntu.com/ubuntu/pool
  for deb in universe/v/vkd3d/vkd3d-compiler_2.0+ds-3_amd64.deb universe/v/vkd3d/libvkd3d-shader1_2.0+ds-3_amd64.deb \
             main/g/glibc/libc6_2.43-2ubuntu2.4_amd64.deb; do
    curl -fsSL -o "$V/pkg.deb" "$P/$deb" || { echo "vkd3d: could not fetch $deb (skipped)"; rm -f "$V/pkg.deb"; break; }
    case "$deb" in *libc6*) dpkg-deb -x "$V/pkg.deb" "$V/glibc" ;; *) dpkg-deb -x "$V/pkg.deb" "$V/root" ;; esac
    rm -f "$V/pkg.deb"
  done
fi
if [ -x "$V/root/usr/bin/vkd3d-compiler" ]; then
  cat > "$OUT/vkd3d-compiler" <<EOF
#!/bin/bash
exec "$V/glibc/usr/lib/x86_64-linux-gnu/ld-linux-x86-64.so.2" \\
  --library-path "$V/glibc/usr/lib/x86_64-linux-gnu:$V/root/usr/lib/x86_64-linux-gnu:/lib/x86_64-linux-gnu" \\
  "$V/root/usr/bin/vkd3d-compiler" "\$@"
EOF
  chmod +x "$OUT/vkd3d-compiler"
  echo "== $OUT/vkd3d-compiler ready"
fi

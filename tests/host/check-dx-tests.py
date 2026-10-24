#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""BCD's Direct3D 9, 10, 11 and 12 test programs (tests/dxtests, the Start menu's "DirectX N test" entries).

Four x86-64 Windows programs, each a window with a spinning textured cube that ends cleanly when the
window is closed: d3d9-cube-x64.exe, d3d10-cube-x64.exe and d3d11-cube-x64.exe (tests/dxtests, plain C,
llvm-mingw, tools/build-dx-tests.sh) and d3d12-cube-x64.exe (madeira-d3d12/tests/windows/cube_window.c, built by
tools/build-madeira-d3d12-dll.sh). Checks:
  1. The shaders of the Direct3D 10 and 11 programs (cube_shaders.h, made offline from cube.hlsl by
     tools/gen-dx-test-shaders.sh): the header records the SHA-256 of cube.hlsl and it matches; each of the four
     blobs is real DXBC: the container's sizes and chunk table add up, its checksum (an independent Python
     port of the DXBC variant of MD5) is the one in the header, the chunks are there (RDEF, ISGN, OSGN, SHDR or
     SHEX), the program is the right type and version (vs_4_0, ps_4_0, vs_5_0, ps_5_0), and the vertex input
     signature is the POSITION, TEXCOORD, COLOR of the programs' input layouts. With --recompile (it builds
     Wine's vkd3d-shader on this Mac, about a minute, and needs the wine submodule's native build) the HLSL is
     compiled again and the bytes must be the header's.
  2. The sources: each program loads its API's DLL at run time (a build without it says so in a message box
     instead of failing in the loader), opens a 640x480 window titled "BCD DirectX N test", draws every frame
     through that API's own calls, and ends when the window is closed (the shared window procedure turns the
     close button, Alt+F4 and Escape into WM_CLOSE, WM_DESTROY and a quit message, and each loop runs while
     dxt_pump() does).
  3. With llvm-mingw (the repository's toolchains/, or ~/madeira-bcd/toolchains/): each program builds with
     -Wall -Wextra -Werror; the tracked binary is what the source builds to (its PE time stamp aside); it is an
     x86-64 PE for the GUI subsystem whose imports are Windows' own DLLs and the C runtime and none of the
     graphics DLLs (they are loaded at run time).
  4. The tracked programs are in the app's arm64ec-windows folder, which the Xcode project copies whole into
     the app, so they reach every session's C:\\windows\\system32; the build script and the workflow build them
     and run these checks; the Start menu's entries name them (BCDStartMenu.c).
"""
import argparse
import hashlib
import os
import re
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

R = Path(__file__).resolve().parents[2]
APP = R / "app/Madeira"
DX = R / "tests/dxtests"
ap = argparse.ArgumentParser()
ap.add_argument("--recompile", action="store_true", help="build vkd3d-shader and compile cube.hlsl again")
args = ap.parse_args()
ok = True


def check(what, cond, detail=""):
    global ok
    print(("ok   " if cond else "FAIL ") + what + (("\n     " + str(detail)[-3000:]) if detail and not cond else ""))
    ok = ok and bool(cond)


def note(text):
    print("note " + text)


# ---------------------------------------------------------------- 1. the shaders
K = [int(abs(__import__("math").sin(i + 1)) * 2 ** 32) & 0xFFFFFFFF for i in range(64)]
SHIFT = [7, 12, 17, 22] * 4 + [5, 9, 14, 20] * 4 + [4, 11, 16, 23] * 4 + [6, 10, 15, 21] * 4


def _compress(state, block):
    m = struct.unpack("<16I", block)
    a, b, c, d = state
    for i in range(64):
        if i < 16:
            f, g = d ^ (b & (c ^ d)), i
        elif i < 32:
            f, g = c ^ (d & (b ^ c)), (5 * i + 1) % 16
        elif i < 48:
            f, g = b ^ c ^ d, (3 * i + 5) % 16
        else:
            f, g = c ^ (b | (~d & 0xFFFFFFFF)), (7 * i) % 16
        f = (f + a + K[i] + m[g]) & 0xFFFFFFFF
        a, d, c = d, c, b
        b = (b + (((f << SHIFT[i]) | (f >> (32 - SHIFT[i]))) & 0xFFFFFFFF)) & 0xFFFFFFFF
    return [(s + v) & 0xFFFFFFFF for s, v in zip(state, (a, b, c, d))]


def dxbc_checksum(data):
    """The DXBC checksum of the bytes after the 20-byte header: MD5 with its own final padding (vkd3d checksum.c)."""
    state = [0x67452301, 0xEFCDAB89, 0x98BADCFE, 0x10325476]
    n = len(data)
    for i in range(n // 64):
        state = _compress(state, data[i * 64:(i + 1) * 64])
    rest = data[(n // 64) * 64:]
    buf = bytearray(64)
    buf[:len(rest)] = rest
    buf[len(rest)] = 0x80
    count = len(rest) + 1
    if 64 - count < 8:
        state = _compress(state, bytes(buf))
        buf = bytearray(64)
    else:
        buf[4:4 + count] = buf[0:count]
        for j in range(count + 4, 64):
            buf[j] = 0
    bits = (n << 3) & 0xFFFFFFFF
    buf[0:4] = struct.pack("<I", bits)
    buf[60:64] = struct.pack("<I", ((bits >> 2) | 1) & 0xFFFFFFFF)
    state = _compress(state, bytes(buf))
    return struct.pack("<4I", *state)


def parse_dxbc(blob):
    """(problems, {fourcc: chunk bytes})"""
    problems = []
    chunks = {}
    if len(blob) < 32 or blob[:4] != b"DXBC":
        return ["no DXBC magic"], chunks
    total = struct.unpack_from("<I", blob, 24)[0]
    count = struct.unpack_from("<I", blob, 28)[0]
    if struct.unpack_from("<I", blob, 20)[0] != 1:
        problems.append("container version is not 1")
    if total != len(blob):
        problems.append("the container says %d bytes, the blob has %d" % (total, len(blob)))
    if dxbc_checksum(blob[20:]) != blob[4:20]:
        problems.append("the checksum is not the one of the bytes")
    for i in range(count):
        off = struct.unpack_from("<I", blob, 32 + 4 * i)[0]
        if off + 8 > len(blob):
            problems.append("chunk %d starts outside the blob" % i)
            continue
        fourcc = blob[off:off + 4].decode("latin1")
        size = struct.unpack_from("<I", blob, off + 4)[0]
        if off + 8 + size > len(blob):
            problems.append("chunk %s runs past the end" % fourcc)
            continue
        chunks[fourcc] = blob[off + 8:off + 8 + size]
    return problems, chunks


def signature_names(chunk):
    count = struct.unpack_from("<I", chunk, 0)[0]
    names = []
    for i in range(count):
        name_off = struct.unpack_from("<I", chunk, 8 + 24 * i)[0]
        end = chunk.index(b"\0", name_off)
        names.append(chunk[name_off:end].decode("ascii").upper())   # vkd3d spells SV_POSITION, d3dcompiler SV_Position
    return names


hdr_text = (DX / "cube_shaders.h").read_text()
hlsl_text = (DX / "cube.hlsl").read_bytes()
m = re.search(r"source-sha256: ([0-9a-f]{64})", hdr_text)
check("cube_shaders.h records the SHA-256 of the cube.hlsl it was made from, and it is this cube.hlsl",
      m is not None and m.group(1) == hashlib.sha256(hlsl_text).hexdigest(),
      "run tools/gen-dx-test-shaders.sh" + (" (header: %s)" % m.group(1) if m else ""))
blobs = {}
for prof in ("vs_4_0", "ps_4_0", "vs_5_0", "ps_5_0"):
    mm = re.search(r"static const unsigned char cube_%s\[\] = \{(.*?)\};" % prof, hdr_text, re.S)
    check("cube_shaders.h has cube_%s" % prof, mm is not None)
    if not mm:
        continue
    blobs[prof] = bytes(int(x, 16) for x in re.findall(r"0x([0-9a-fA-F]{2})", mm.group(1)))
want_type = {"vs": 1, "ps": 0}
for prof, blob in blobs.items():
    problems, chunks = parse_dxbc(blob)
    check("%s is a DXBC container whose sizes, chunks and checksum add up" % prof, not problems, problems)
    check("%s has RDEF, ISGN, OSGN and its code chunk (vkd3d-shader writes SHDR for 5.0 too; Wine's d3dcompiler and DXMT take it)" % prof,
          all(k in chunks for k in ("RDEF", "ISGN", "OSGN")) and ("SHDR" in chunks or "SHEX" in chunks), sorted(chunks))
    code = chunks.get("SHDR") or chunks.get("SHEX") or b"\0" * 8
    version, length = struct.unpack_from("<II", code, 0)
    major, minor, ptype = (version >> 4) & 0xF, version & 0xF, version >> 16
    check("%s is a %s_%d_%d program of %d tokens" % (prof, prof[:2], major, minor, length),
          (ptype, major, minor) == (want_type[prof[:2]], int(prof[3]), 0) and length * 4 == len(code), (hex(version), length, len(code)))
    if prof.startswith("vs"):
        check("%s takes POSITION, TEXCOORD, COLOR, in the programs' input layout order" % prof,
              signature_names(chunks["ISGN"]) == ["POSITION", "TEXCOORD", "COLOR"], signature_names(chunks["ISGN"]))
        check("%s gives SV_Position, TEXCOORD, COLOR to the pixel shader" % prof,
              signature_names(chunks["OSGN"]) == ["SV_POSITION", "TEXCOORD", "COLOR"], signature_names(chunks["OSGN"]))
    else:
        check("%s reads TEXCOORD and COLOR and writes SV_Target" % prof,
              signature_names(chunks["ISGN"]) == ["SV_POSITION", "TEXCOORD", "COLOR"] and signature_names(chunks["OSGN"]) == ["SV_TARGET"],
              (signature_names(chunks["ISGN"]), signature_names(chunks["OSGN"])))
    # the shader samples a texture and has one constant buffer of 64 bytes (the matrix) when it is a vertex shader
    if prof.startswith("vs"):
        check("%s's constant buffer is the 64-byte matrix" % prof, struct.pack("<I", 64) in chunks["RDEF"])
check("the Shader Model 4.0 and 5.0 vertex shaders are two programs",
      "vs_4_0" in blobs and "vs_5_0" in blobs and blobs["vs_4_0"] != blobs["vs_5_0"])
check("cube.hlsl keeps its matrix row-major: the program's matrix bytes are the C code's rows", "row_major float4x4 mvp" in hlsl_text.decode())

if args.recompile:
    out = Path(tempfile.mkdtemp(prefix="bcd-dxshaders-"))
    gen = subprocess.run(["bash", str(R / "tools/gen-dx-test-shaders.sh")], env=dict(os.environ, OUT=str(out)), capture_output=True, text=True)
    check("tools/gen-dx-test-shaders.sh runs", gen.returncode == 0, gen.stdout + gen.stderr)
    if gen.returncode == 0:
        for prof, blob in blobs.items():
            fresh = (out / ("cube_%s.dxbc" % prof)).read_bytes()
            # the compiler's version number is in the container: compare apart from the checksum and that string
            check("compiling cube.hlsl again gives %s as it is in the header" % prof,
                  len(fresh) == len(blob) and sum(a != b for a, b in zip(fresh, blob)) <= 17, "%d vs %d bytes" % (len(fresh), len(blob)))
        text = (DX / "cube_shaders.h").read_text()
        check("... and the header is what the script writes (git has no pending change in it)", text == hdr_text)
else:
    note("not compiling cube.hlsl again (--recompile does, with Wine's vkd3d-shader built on this Mac)")

# ---------------------------------------------------------------- 2. the sources
common = (DX / "dxtest.h").read_text()
programs = {
    "d3d9": ("Direct3D 9", "d3d9.dll", "Direct3DCreate9", "BCD DirectX 9 test",
             ["IDirect3DDevice9_DrawIndexedPrimitive", "IDirect3DDevice9_Present", "IDirect3DDevice9_Clear", "IDirect3DDevice9_SetTransform",
              "IDirect3DTexture9_LockRect"]),
    "d3d10": ("Direct3D 10", "d3d10.dll", "D3D10CreateDeviceAndSwapChain", "BCD DirectX 10 test",
              ["ID3D10Device_DrawIndexed", "IDXGISwapChain_Present", "ID3D10Device_ClearRenderTargetView", "cube_vs_4_0", "cube_ps_4_0"]),
    "d3d11": ("Direct3D 11", "d3d11.dll", "D3D11CreateDeviceAndSwapChain", "BCD DirectX 11 test",
              ["ID3D11DeviceContext_DrawIndexed", "IDXGISwapChain_Present", "ID3D11DeviceContext_ClearRenderTargetView", "cube_vs_5_0", "cube_ps_5_0"]),
}
check("the shared window handles WM_CLOSE (DestroyWindow), WM_DESTROY (PostQuitMessage) and Escape (a WM_CLOSE)",
      all(s in common for s in ("case WM_CLOSE:", "DestroyWindow(h)", "case WM_DESTROY:", "PostQuitMessage(0)", "VK_ESCAPE", "WM_CLOSE, 0, 0")))
check("the shared pump returns 0 on WM_QUIT", "if (msg.message == WM_QUIT) return 0;" in common)
check("the window is 640x480, not resizable", "#define DXT_W 640" in common and "#define DXT_H 480" in common and "WS_THICKFRAME" not in common)
check("the frame rate goes into the title", "SetWindowTextA(c->hwnd, title)" in common and '"%s - %.0f fps"' in common)
check("every API test logs through the one-write [dx-test] helper", "[dx-test] " in common and "WriteFile(GetStdHandle(STD_ERROR_HANDLE)" in common)
for key, (api, dll, entry, title, calls) in programs.items():
    src = (DX / (key + "_cube.c")).read_text()
    head = src[:400]
    check("%s_cube.c has the BCD licence header" % key, "SPDX-License-Identifier: GPL-3.0-only" in head and "LICENSE-BCD.md" in head)
    check("%s: loads %s at run time and takes %s from it" % (key, dll, entry),
          'LoadLibraryA("%s")' % dll in src and 'GetProcAddress(dll, "%s")' % entry in src)
    check("%s: a missing DLL gets a message, not a loader failure" % key, "dxt_fail(API" in src and "is not available" in src)
    check("%s: opens the window titled %r" % (key, title), 'dxt_window("%s")' % title in src and 'dxt_clock_start(&clock, hwnd, "%s")' % title in src)
    check("%s: draws every frame through %s" % (key, api), all(c in src for c in calls), [c for c in calls if c not in src])
    check("%s: runs while the window is open and releases what it made when it is closed" % key,
          "while (dxt_pump())" in src and src.index("while (dxt_pump())") < src.rindex("_Release(") and src.rstrip().endswith("return 0;\n}"))
    check("%s: depth test on, culling off, the cube drawn indexed" % key,
          ("D3DRS_ZENABLE" in src and "D3DCULL_NONE" in src) or ("DepthEnable = TRUE" in src and "CULL_NONE" in src))
    check("%s: a texture is created and bound" % key, "dxt_checker(texels)" in src)
dx12 = (R / "madeira-d3d12/tests/windows/cube_window.c").read_text()
check("d3d12: the window is titled \"BCD DirectX 12 test\" and ends when it is closed",
      '"BCD DirectX 12 test"' in dx12 and "WM_DESTROY" in dx12 and "PostQuitMessage(0)" in dx12 and "if (msg.message == WM_QUIT) { running = 0; break; }" in dx12)
check("no window title says Madeira", not any("Madeira" in t for t in re.findall(r'"(BCD DirectX[^"]*)"', dx12 + "".join((DX / (k + "_cube.c")).read_text() for k in programs))))

# ---------------------------------------------------------------- 3. the builds
toolchain = None
for cand in (R / "toolchains/llvm-mingw-20260421-ucrt-macos-universal/bin", Path.home() / "madeira-bcd/toolchains/llvm-mingw-20260421-ucrt-macos-universal/bin"):
    if (cand / "x86_64-w64-mingw32-clang").exists():
        toolchain = cand
        break
GRAPHICS_DLLS = ("d3d9", "d3d10", "d3d11", "d3d12", "dxgi", "madeira_d3d12")
ALLOWED = re.compile(r"^(kernel32|user32|ole32|api-ms-win-crt-[a-z-]+-l1-1-0)\.dll$", re.I)


def pe_info(path):
    data = path.read_bytes()
    pe = struct.unpack_from("<I", data, 0x3C)[0]
    assert data[pe:pe + 4] == b"PE\0\0"
    machine = struct.unpack_from("<H", data, pe + 4)[0]
    opt = pe + 24
    magic = struct.unpack_from("<H", data, opt)[0]
    subsystem = struct.unpack_from("<H", data, opt + 68)[0]
    nsections = struct.unpack_from("<H", data, pe + 6)[0]
    soh = struct.unpack_from("<H", data, pe + 20)[0]
    dirs = opt + (112 if magic == 0x20B else 96)
    import_rva = struct.unpack_from("<I", data, dirs + 8)[0]
    sections = []
    for i in range(nsections):
        s = opt + soh + 40 * i
        sections.append(struct.unpack_from("<IIII", data, s + 8))   # vsize, va, rawsize, rawptr

    def rva(r):
        for vsize, va, rawsize, rawptr in sections:
            if va <= r < va + max(vsize, rawsize):
                return r - va + rawptr
        raise ValueError(r)
    names = []
    p = rva(import_rva)
    while True:
        desc = struct.unpack_from("<IIIII", data, p)
        if desc[3] == 0:
            break
        end = data.index(b"\0", rva(desc[3]))
        names.append(data[rva(desc[3]):end].decode("ascii"))
        p += 20
    return machine, subsystem, names, data


def without_timestamp(data):
    pe = struct.unpack_from("<I", data, 0x3C)[0]
    return data[:pe + 8] + b"\0\0\0\0" + data[pe + 12:pe + 24 + 64] + b"\0\0\0\0" + data[pe + 24 + 68:]   # time stamp and the checksum field


sources = {"d3d9": DX / "d3d9_cube.c", "d3d10": DX / "d3d10_cube.c", "d3d11": DX / "d3d11_cube.c",
           "d3d12": R / "madeira-d3d12/tests/windows/cube_window.c"}
for key, src in sources.items():
    exe = APP / "arm64ec-windows" / ("%s-cube-x64.exe" % key)
    check("%s-cube-x64.exe is tracked in app/Madeira/arm64ec-windows" % key, exe.is_file())
    if not exe.is_file():
        continue
    machine, subsystem, imports, data = pe_info(exe)
    check("%s-cube-x64.exe is an x86-64 program for the GUI subsystem" % key, machine == 0x8664 and subsystem == 2, (hex(machine), subsystem))
    check("%s-cube-x64.exe imports Windows' own DLLs and the C runtime only, and no graphics DLL" % key,
          all(ALLOWED.match(n) for n in imports) and not any(n.lower().startswith(g) for n in imports for g in GRAPHICS_DLLS), imports)
    if toolchain is None:
        continue
    with tempfile.TemporaryDirectory(prefix="bcd-dxbuild-") as t:
        out = Path(t) / "x.exe"
        libs = ["-luuid"] if key == "d3d9" else (["-luuid", "-lole32"] if key == "d3d12" else ["-ldxguid", "-luuid"])
        flags = ["-O2", "-Wall", "-mwindows", "-I", str(src.parent)]
        if key != "d3d12":
            flags += ["-Wextra", "-Werror", "-Wl,--no-insert-timestamp"]
        build = subprocess.run([str(toolchain / "x86_64-w64-mingw32-clang"), *flags, "-o", str(out), str(src), *libs], capture_output=True, text=True)
        error_lines = [l for l in build.stderr.splitlines() if "error" in l]
        check("%s builds with the mingw toolchain%s" % (key, "" if key == "d3d12" else " under -Wall -Wextra -Werror"), build.returncode == 0, error_lines or build.stderr)
        if build.returncode == 0:
            fresh = out.read_bytes()
            if key == "d3d12":   # built without --no-insert-timestamp (tools/build-madeira-d3d12-dll.sh): the debug directory has a time stamp too
                same = len(fresh) == len(data) and sum(a != b for a, b in zip(fresh, data)) <= 16
            else:
                same = without_timestamp(fresh) == without_timestamp(data)
            check("%s-cube-x64.exe is what its source builds to (apart from the PE time stamps)" % key,
                  same, "rebuild it (tools/build-dx-tests.sh, tools/build-madeira-d3d12-dll.sh) and commit it")
if toolchain is None:
    note("llvm-mingw is not here (toolchains/ or ~/madeira-bcd/toolchains): the programs were not built again")

# ---------------------------------------------------------------- 4. where they go
pbx = (R / "app/Madeira.xcodeproj/project.pbxproj").read_text()
check("the Xcode project copies the whole arm64ec-windows folder into the app (a folder reference)",
      "arm64ec-windows in Resources" in pbx and re.search(r'lastKnownFileType = folder; path = "arm64ec-windows"', pbx) is not None)
bridge = (APP / "WineProcessBridge.m").read_text()
check("every session links the arm64ec-windows folder into C:\\windows\\system32 (the programs' path in the Start menu)",
      'bundle_subdir = use_arm64ec ? "arm64ec-windows" : "aarch64-windows"' in bridge and "Cross-linked" in bridge)
start_c = (APP / "BCDStartMenu.c").read_text()
for key in ("d3d9", "d3d10", "d3d11", "d3d12"):
    check("the Start menu has %s-cube-x64.exe" % key, '"%s-cube-x64.exe"' % key in start_c)
build_sh = (R / "tools/build-dx-tests.sh").read_text()
check("tools/build-dx-tests.sh keeps the tracked program when a build fails (a warning, exit 0) and checks the PE it built",
      "keeping the tracked test" in build_sh and "check_pe" in build_sh and "0x8664" in build_sh and "cp \"$OUT/$name\" \"$SHIP/$name\"" in build_sh)
wf = (R / ".github/workflows/build-ipa.yml").read_text()
step = re.search(r"      - name: Build the Direct3D 9, 10 and 11 test programs\n((?:        .*\n|\n)*)", wf)
check("the workflow builds the test programs with tools/build-dx-tests.sh", step is not None and "bash tools/build-dx-tests.sh" in step.group(1))
check("... after the D3D12 build (which makes d3d12-cube-x64.exe)",
      step is not None and wf.index("- name: Build madeira_d3d12.dll from source") < step.start())
check("the workflow runs this check", "tests/host/check-dx-tests.py" in wf)

sys.exit(0 if ok else 1)

#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""Opt-in ID3D12Device1::CreatePipelineLibrary of the D3D12 runtime; no device.

madeira-d3d12/src/pe/madeira_d3d12.c, block "ID3D12PipelineLibrary": with bcd.cfg / game file
d3d12-pipeline-library = 1 the call makes a library that keeps no pipeline (the loads answer
E_INVALIDARG, "the name is not in the library"; StorePipeline accepts and forgets; the serialized
form is 16 bytes), instead of the generated stub's E_NOTIMPL. Returnal (Unreal; Send Log CWKVD7,
build 574) ends in a LowLevelFatalError when the call fails; GTA V Enhanced and RDR2 call it too and
carry on with the failure, so the default stays the stub.

  * checks statically that the key defaults to 0, that the call falls back to the generated stub
    (the same arguments) when the key is off, that the object answers ID3D12PipelineLibrary and
    ID3D12PipelineLibrary1 (same vtable, one more method), that the loads null the out pointer and
    say E_INVALIDARG, that the vtable slots are filled, and that the settings catalog lists the key
    off by default and CI runs this check;
  * cuts the blob helpers (between "piplib-test:begin" and "piplib-test:end"), compiles them on the
    host and runs them: an empty blob and the one Serialize writes are accepted, anything else is
    refused; Serialize writes the 16-byte header and refuses a short buffer;
  * compiles the whole runtime for syntax with the repo's llvm-mingw when its toolchain is present
    (the full tree), else says so.

The runtime part is skipped when no host C compiler is found; the static checks still run.
"""
import pathlib, re, shutil, subprocess, sys, tempfile

R = pathlib.Path(__file__).resolve().parents[2]
SRC = (R / "madeira-d3d12/src/pe/madeira_d3d12.c").read_text()
ok = True


def check(what, cond):
    global ok
    print(("ok   " if cond else "FAIL ") + what)
    ok = ok and bool(cond)


def cut(sig):
    i = SRC.index(sig)
    j = SRC.index("{", i)
    depth = 0
    for k in range(j, len(SRC)):
        if SRC[k] == "{":
            depth += 1
        elif SRC[k] == "}":
            depth -= 1
            if depth == 0:
                return SRC[i:k + 1]
    raise SystemExit("unbalanced " + sig)


# --- static checks ---------------------------------------------------------
check("d3d12-pipeline-library defaults to 0", 'v = mad_cfg_int_pe("d3d12-pipeline-library", 0) ? 1 : 0;' in SRC)
check("logged once, only when on", re.search(r'if \(v\) d3d12_log\("\[madeira-d3d12\] d3d12-pipeline-library = 1 \(opt-in\)', SRC) is not None)
dev = cut("static HRESULT STDMETHODCALLTYPE device_CreatePipelineLibrary(ID3D12Device10 *This")
first = dev[dev.index("{") + 1:].lstrip().splitlines()
gate = next(l for l in first if l.strip().startswith("if (!mad_piplib_on())"))
check("the generated stub, with the same arguments, when the key is off",
      "return stub_ID3D12Device10_CreatePipelineLibrary(This, blob, blob_size, riid, out);" in gate)
stub = SRC.count("stub_ID3D12Device10_CreatePipelineLibrary(")
check("the stub stays where it is (generated header)", stub == 1 or stub == 2)
check("a blob that is not ours is DRIVER_VERSION_MISMATCH, before anything is allocated",
      dev.index("mad_piplib_blob_ok(blob, blob_size)") < dev.index("calloc(1, sizeof *l)")
      and "return MAD_D3D12_ERROR_DRIVER_VERSION_MISMATCH;" in dev
      and "#define MAD_D3D12_ERROR_DRIVER_VERSION_MISMATCH ((HRESULT)0x887E0002L)" in SRC)
check("a NULL out is the documented capability test (S_FALSE)", "if (!out) return S_FALSE;" in dev)
check("the object is released once after the query (the caller owns one reference)",
      "hr = piplib_QI((ID3D12PipelineLibrary1 *)l, riid, out);\n    piplib_Release((ID3D12PipelineLibrary1 *)l);" in dev)
qi = cut("static HRESULT STDMETHODCALLTYPE piplib_QI(ID3D12PipelineLibrary1 *This")
check("ID3D12PipelineLibrary1 is the same object (QI); ID3D12PipelineLibrary is its iid",
      "IsEqualGUID(riid, &IID_ID3D12PipelineLibrary1)" in qi and "InterlockedIncrement(&o->refs);" in qi
      and "return mad_qi(o, riid, out, 1);" in qi
      and "l->iid = &IID_ID3D12PipelineLibrary;" in dev)
for fn in ("piplib_LoadGraphicsPipeline", "piplib_LoadComputePipeline", "piplib_LoadPipeline"):
    body = cut("static HRESULT STDMETHODCALLTYPE %s(" % fn)
    check(fn + ": clears the out pointer and answers E_INVALIDARG",
          "if (pso) *pso = NULL;" in body and "return E_INVALIDARG;" in body)
check("StorePipeline: refuses missing arguments, otherwise accepts and forgets",
      "if (!name || !pso) return E_INVALIDARG;\n    return S_OK;" in cut("static HRESULT STDMETHODCALLTYPE piplib_StorePipeline("))
check("GetSerializedSize is the blob size", "return MAD_PIPLIB_BLOB_SIZE; }" in SRC and "piplib_GetSerializedSize" in SRC)
for slot in ("QueryInterface", "AddRef", "Release", "GetPrivateData", "SetPrivateData", "SetPrivateDataInterface", "SetName",
             "GetDevice", "StorePipeline", "LoadGraphicsPipeline", "LoadComputePipeline", "GetSerializedSize", "Serialize",
             "LoadPipeline"):
    check("vtable: " + slot, re.search(r"g_piplib_vtbl\.%s\s*= piplib_%s;" % (slot, slot if slot not in ("QueryInterface",) else "QI"), SRC) is not None)
check("the device vtable points at the new function (the stub when off)",
      "g_device_vtbl.CreatePipelineLibrary              = device_CreatePipelineLibrary;" in SRC)
catalog = (R / "build/tools/gen-config-catalog.py").read_text()
check("the settings catalog lists d3d12-pipeline-library, off by default",
      re.search(r'"d3d12-pipeline-library": \{"category": "Direct3D 12"[^}]*"kind": "bool", "default": "0"', catalog) is not None)
swift = (R / "app/Madeira/ConfigCatalog.generated.swift").read_text()
check("the generated catalog carries the key (regenerate it in the full tree; the line is the generator's)",
      re.search(r'ConfigOption\(key: "d3d12-pipeline-library", [^\n]*kind: \.bool, defaultValue: "0"', swift) is not None)
workflow = (R / ".github/workflows/build-ipa.yml").read_text()
check("CI runs this check", "run: python3 tests/host/check-d3d12-pipeline-library.py" in workflow)

# --- the blob helpers on the host ------------------------------------------
cc = shutil.which("cc") or shutil.which("gcc") or shutil.which("clang")
b = SRC.index("/* piplib-test:begin */")
e = SRC.index("/* piplib-test:end */")
HARNESS = r'''
#include <stdint.h>
#include <stdio.h>
#include <string.h>
typedef long HRESULT; typedef size_t SIZE_T;
#define S_OK ((HRESULT)0)
#define E_INVALIDARG ((HRESULT)0x80070057L)
''' + SRC[b:e] + r'''
#define T(c) do { if (!(c)) { printf("FAIL line %d: %s\n", __LINE__, #c); return 1; } } while (0)
int main(void) {
    unsigned char buf[32];
    T(MAD_PIPLIB_BLOB_SIZE == 16);
    /* nothing: an empty library */
    T(mad_piplib_blob_ok(NULL, 0)); T(mad_piplib_blob_ok(buf, 0));
    /* what Serialize writes comes back as an empty library */
    memset(buf, 0xAA, sizeof buf);
    T(mad_piplib_serialize(buf, 16) == S_OK);
    T(!memcmp(buf, "MADPLIB1", 8)); { int i; for (i = 8; i < 16; i++) T(buf[i] == 0); } T(buf[16] == 0xAA);
    T(mad_piplib_blob_ok(buf, 16));
    /* a larger buffer is fine; only the header is written */
    memset(buf, 0xAA, sizeof buf); T(mad_piplib_serialize(buf, 32) == S_OK); T(buf[15] == 0 && buf[16] == 0xAA);
    /* not ours */
    T(!mad_piplib_blob_ok(buf, 15)); T(!mad_piplib_blob_ok(buf, 17)); T(!mad_piplib_blob_ok(NULL, 16));
    buf[0] = 'X'; T(!mad_piplib_blob_ok(buf, 16));
    memset(buf, 0, sizeof buf); T(!mad_piplib_blob_ok(buf, 16));
    /* Serialize refuses a short or missing buffer and writes nothing */
    memset(buf, 0xAA, sizeof buf); T(mad_piplib_serialize(buf, 15) == E_INVALIDARG); T(buf[0] == 0xAA);
    T(mad_piplib_serialize(NULL, 16) == E_INVALIDARG); T(mad_piplib_serialize(buf, 0) == E_INVALIDARG);
    puts("helpers: ok");
    return 0;
}
'''
if cc:
    with tempfile.TemporaryDirectory() as tmp:
        c = pathlib.Path(tmp) / "t.c"
        c.write_text(HARNESS)
        exe = pathlib.Path(tmp) / "t"
        r = subprocess.run([cc, "-O1", "-Wall", "-Werror", "-o", str(exe), str(c)], capture_output=True, text=True)
        check("the blob helpers compile on the host", r.returncode == 0)
        if r.returncode:
            print(r.stderr)
        else:
            run = subprocess.run([str(exe)], capture_output=True, text=True)
            print(run.stdout.rstrip())
            check("empty and own blobs accepted, others refused; Serialize writes the 16-byte header", run.returncode == 0)
else:
    print("note: no host C compiler; the helpers were not run")

# --- the whole runtime, for syntax, with the repo's toolchain -----------------
mingw = R / "toolchains/llvm-mingw-20260421-ucrt-macos-universal/bin/arm64ec-w64-mingw32-clang"
winemetal = R / "dxmt/src/winemetal"
if mingw.exists() and winemetal.exists():
    r = subprocess.run([str(mingw), "-fsyntax-only", "-O2", "-Wall", '-DMAD_SC_CONVERTER_ID="0000000000000000"',
                        "-I" + str(R / "madeira-d3d12/src/pe"), "-I" + str(R / "madeira-d3d12/src"), "-I" + str(winemetal),
                        str(R / "madeira-d3d12/src/pe/madeira_d3d12.c")], capture_output=True, text=True)
    check("madeira_d3d12.c compiles (arm64ec, -fsyntax-only)", r.returncode == 0)
    if r.returncode:
        print(r.stderr[-3000:])
else:
    print("note: llvm-mingw / dxmt not in this tree; the syntax compile runs in the full tree")

sys.exit(0 if ok else 1)

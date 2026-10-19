#!/usr/bin/env python3
"""tools/patch-dxmt-d3d11-fault-info.py: env.MADEIRA_DXMT_FAULT_INFO = 1.

GTA V Legacy (build 546) hung the GPU at Story Mode ("Caused GPU Timeout
Error") and DXMT could not say which encoder did it. The patch, off by
default: command buffers with encoder execution status (winemetal unix),
labelled encoders and a [fault-enc] line after "Device error" (DXMT).

Checks, on a copy of dxmt/src (or $DXMT_SRC_DIR/src):
  - the patch applies to the three files, a second run changes nothing, and
    it only adds lines;
  - winemetal: the descriptor with MTLCommandBufferErrorOptionEncoderExecutionStatus
    is used only when MADEIRA_DXMT_FAULT_INFO starts with 1, and the plain
    commandBuffer stays the path otherwise (remote mode returns before it);
  - DXMT: the [fault-enc] line calls madeira_ctl op 9 only with the switch,
    never in the i386 build, and before MarkDeviceError;
  - DXMT: every render, compute and blit encoder label is behind
    madeira_fault_info_on(), which reads the switch once (default off);
  - CI runs the patch before the i386 farm key and this check; the settings
    catalog documents the switch, default 0.
SKIP (exit 0) when no dxmt sources are available.
"""
import difflib
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile

R = pathlib.Path(__file__).resolve().parents[2]
src = pathlib.Path(os.environ.get("DXMT_SRC_DIR", R / "dxmt")) / "src"
FILES = ("winemetal/unix/winemetal_unix.c", "dxmt/dxmt_command_queue.cpp", "dxmt/dxmt_context.cpp")
if not all((src / f).is_file() for f in FILES):
    print(f"SKIP: no dxmt sources at {src} (check out the submodule or set DXMT_SRC_DIR)")
    sys.exit(0)

ok = True


def check(what, cond):
    global ok
    print(("ok   " if cond else "FAIL ") + what)
    ok = ok and bool(cond)


with tempfile.TemporaryDirectory() as tmp:
    t = pathlib.Path(tmp)
    for f in FILES:
        (t / "dxmt/src" / f).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(src / f, t / "dxmt/src" / f)
    before = {f: (t / "dxmt/src" / f).read_text() for f in FILES}
    runs = [subprocess.run([sys.executable, str(R / "tools/patch-dxmt-d3d11-fault-info.py")],
                           cwd=t, capture_output=True, text=True) for _ in range(2)]
    after = {f: (t / "dxmt/src" / f).read_text() for f in FILES}
    check("the patch applies to the three files",
          runs[0].returncode == 0 and all("madeira-bcd: DXMT fault info" in after[f] for f in FILES))
    check("a second run changes nothing", runs[1].returncode == 0 and runs[1].stdout.count("already patched") == 3)
    check("the patch only adds lines",
          not any(l.startswith("- ") for f in FILES
                  for l in difflib.ndiff(before[f].splitlines(), after[f].splitlines())))

    wm = after["winemetal/unix/winemetal_unix.c"]
    cb = wm[wm.index("_MTLCommandQueue_commandBuffer(void *obj) {"):]
    cb = cb[:cb.index("\n}\n")]
    gate = cb.index('const char *fi = getenv("MADEIRA_DXMT_FAULT_INFO");')
    check("winemetal: remote mode returns before the switch is read", cb.index("if (wmtr_enabled()) {") < gate)
    check("winemetal: encoder execution status only when the switch starts with 1",
          "if (fi && fi[0] == '1') {" in cb and
          cb.index("if (fi && fi[0] == '1') {") < cb.index("MTLCommandBufferErrorOptionEncoderExecutionStatus")
          < cb.index("commandBufferWithDescriptor:desc]") < cb.index("[desc release];"))
    check("winemetal: otherwise the plain commandBuffer, as before",
          cb.rstrip().endswith("params->ret = (obj_handle_t)[(id<MTLCommandQueue>)params->handle commandBuffer];\n"
                               "  return STATUS_SUCCESS;"))

    q = after["dxmt/dxmt_command_queue.cpp"]
    blk = q[q.index('ERR("Device error at frame "'):q.index("MarkDeviceError();")]
    check("DXMT: [fault-enc] through op 9, only with the switch, never in the i386 build, before MarkDeviceError",
          "#ifndef __i386__" in blk and blk.index("#ifndef __i386__") < blk.index("madeira_fi =") <
          blk.index("if (madeira_fi) {") < blk.index("fi_args.op = 9;") < blk.index("MadeiraCtl(&fi_args);") <
          blk.index('"[fault-enc] madeira-bcd frame "') < blk.index("#endif"))

    c = after["dxmt/dxmt_context.cpp"]
    check("DXMT: madeira_fault_info_on() reads the switch once and is off unless it starts with 1",
          'static const bool on = dxmt::env::getEnvVar("MADEIRA_DXMT_FAULT_INFO").starts_with("1");' in c)
    labels = [m.start() for m in re.finditer(r"encoder\.setLabel\(WMT::String::string\(label,", c)]
    gates = [c.rfind("if (madeira_fault_info_on()) {", 0, p) for p in labels]
    check("DXMT: render, compute and blit encoders labelled, each behind the switch",
          len(labels) == 3 and all(g >= 0 and p - g < 600 for g, p in zip(gates, labels)) and
          '"rp#%llu %ux%u cmds=%u gs=%d ts=%d gs_tasks=%u ts_tasks=%u"' in c and
          '"cs#%llu cmds=%u"' in c and '"blit#%llu cmds=%u"' in c)

wf = (R / ".github/workflows/build-ipa.yml").read_text()
key = wf.index("key=i386-farm-")
check("CI runs the patch before the i386 farm key, and this check",
      0 <= wf.find("run: python3 tools/patch-dxmt-d3d11-fault-info.py") < key and
      "run: python3 tests/host/check-dxmt-d3d11-fault-info.py" in wf)
cat = (R / "app/Madeira/ConfigCatalog.generated.swift").read_text()
check("the catalog documents env.MADEIRA_DXMT_FAULT_INFO (default 0)",
      'ConfigOption(key: "env.MADEIRA_DXMT_FAULT_INFO", title: "Name the DXMT encoder of a GPU fault or hang", '
      'kind: .bool, defaultValue: "0"' in cat)

print("PASS: DXMT fault info" if ok else "FAILED: DXMT fault info")
sys.exit(0 if ok else 1)

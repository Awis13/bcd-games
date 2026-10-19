#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""env.MADEIRA_PAUSE_IN_BACKGROUND = 1 (opt-in): Metal work waits while the app is not active.

iOS refuses GPU work from the background; what a game submitted in the seconds after Home was
lost (gameplay.md section 2.2 of the iOS 18 audit). tools/patch-winemetal-pause-inactive.py makes
winemetal's commit and present call madeira_app_wait_active (WineProcessBridge.m) first.

Checks:
  - the patch applies to the pinned winemetal_unix.c once (a second run only says so); the call
    comes after the remote-Metal branch of _MTLCommandBuffer_commit and of
    _MTLCommandBuffer_presentDrawable, before anything else they do;
  - the bridge: the flag that makes it wait is set only by the resign-active observer, only with
    the switch; become-active always clears it; no observer is registered without a switch;
  - compiled (macOS): while active the call returns at once; the main thread never waits; a worker
    thread waits until the app is active again, then goes on;
  - the catalog entry, off by default; the workflow runs the patch after the other winemetal
    patches, before the i386 farm key, and this check before it.
"""
from pathlib import Path
import re, shutil, subprocess, sys, tempfile

root = Path(__file__).resolve().parents[2]
wm_pin = root / "dxmt/src/winemetal/unix/winemetal_unix.c"
bridge = (root / "app/Madeira/WineProcessBridge.m").read_text()
catalog = (root / "app/Madeira/ConfigCatalog.generated.swift").read_text()
workflow = (root / ".github/workflows/build-ipa.yml").read_text()
ok = True


def check(what, cond):
    global ok
    print(("ok   " if cond else "FAIL ") + what)
    ok &= bool(cond)


if wm_pin.is_file():
    with tempfile.TemporaryDirectory() as t:
        dst = Path(t) / "dxmt/src/winemetal/unix/winemetal_unix.c"
        dst.parent.mkdir(parents=True)
        shutil.copy(wm_pin, dst)
        patch = root / "tools/patch-winemetal-pause-inactive.py"
        r1 = subprocess.run([sys.executable, str(patch)], cwd=t, capture_output=True, text=True)
        r2 = subprocess.run([sys.executable, str(patch)], cwd=t, capture_output=True, text=True)
        check("the patch applies once; a second run only says it is there",
              r1.returncode == 0 and "pause while inactive patched" in r1.stdout and "already" not in r1.stdout
              and r2.returncode == 0 and "already patched" in r2.stdout)
        wm = dst.read_text()
    commit = wm[wm.index("_MTLCommandBuffer_commit(void *obj) {"):]
    commit = commit[:commit.index("\n}\n")]
    present = wm[wm.index("_MTLCommandBuffer_presentDrawable(void *obj) {"):]
    present = present[:present.index("\n}\n")]
    check("declared once, before the commit function",
          wm.count("extern int madeira_app_wait_active(const char *where);") == 1
          and wm.index("extern int madeira_app_wait_active") < wm.index("_MTLCommandBuffer_commit(void *obj) {"))
    check("commit: the wait comes right after the remote branch, before the buffer is touched",
          commit.count('madeira_app_wait_active("commit");') == 1
          and commit.index("wmtr_call(RM_OP_COMMIT") < commit.index('madeira_app_wait_active("commit");')
          < commit.index("[(id<MTLCommandBuffer>)params->handle commit];"))
    check("present: the wait comes right after the remote branch, before the pacing mode is read",
          present.count('madeira_app_wait_active("present");') == 1
          and present.index("wmtr_call(RM_OP_PRESENT_DRAWABLE") < present.index('madeira_app_wait_active("present");')
          < present.index("int mode = g_madeira_vsync_mode;"))
else:
    print("SKIP: no dxmt sources for the patch (check out the submodule)")

wait = bridge[bridge.index("int madeira_app_wait_active(const char *where)"):]
wait = wait[:wait.index("\n}\n")]
check("the bridge's wait returns at once while active and on the main thread",
      "if (!atomic_load_explicit(&g_madeira_inactive, memory_order_acquire) || pthread_main_np()) return 0;" in wait)
check("only the resign-active observer sets the flag, only with the switch; become-active always clears it",
      bridge.count("madeira_set_inactive(1);") == 1 and "if (madeira_pause_switch()) madeira_set_inactive(1);" in bridge
      and "madeira_set_inactive(0);   /* always: the switch may have changed since */" in bridge)
check("the switch is read from the environment, only \"1\" turns it on",
      'const char *v = getenv("MADEIRA_PAUSE_IN_BACKGROUND");' in bridge)
inst = bridge[bridge.index("static void madeira_engine_observers_install(void)"):]
check("no observer without a switch", inst.index("if (!audio && !pause) return;") < inst.index("addObserverForName:"))
check("the catalog lists env.MADEIRA_PAUSE_IN_BACKGROUND, off by default",
      re.search(r'ConfigOption\(key: "env\.MADEIRA_PAUSE_IN_BACKGROUND", [^\n]*kind: \.bool, defaultValue: "0"', catalog) is not None)
p = workflow.index("run: python3 tools/patch-winemetal-pause-inactive.py")
check("CI runs the patch after the other winemetal patches, before the i386 farm key, and this check first",
      workflow.index("run: python3 tools/patch-winemetal-heap-untracked.py") < p < workflow.index("- name: i386 farm revision")
      and workflow.index("run: python3 tests/host/check-gpu-pause-inactive.py") < p)

code = bridge[bridge.index("static pthread_mutex_t g_madeira_active_lock"):bridge.index("/* Activate the session and ask the driver")]
harness = r"""
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <errno.h>
#include <unistd.h>
#include <pthread.h>
#include <stdatomic.h>
#include <sys/time.h>
""" + code + r"""
static void *worker(void *arg) { *(int *)arg = madeira_app_wait_active("commit"); return NULL; }
static void *waker(void *arg) { (void)arg; usleep(200000); madeira_set_inactive(0); return NULL; }
int main(void)
{
    pthread_t w, k;
    int r = -1;
    struct timeval a, b;
    if (madeira_app_wait_active("commit") != 0) { puts("FAIL active: no wait"); return 1; }
    madeira_set_inactive(1);
    if (madeira_app_wait_active("present") != 0) { puts("FAIL the main thread never waits"); return 1; }
    gettimeofday(&a, NULL);
    pthread_create(&w, NULL, worker, &r); pthread_create(&k, NULL, waker, NULL);
    pthread_join(w, NULL); pthread_join(k, NULL);
    gettimeofday(&b, NULL);
    if (r != 1 || (b.tv_sec - a.tv_sec) + (b.tv_usec - a.tv_usec) / 1e6 < 0.15) { puts("FAIL a worker waits until active"); return 1; }
    r = -1; pthread_create(&w, NULL, worker, &r); pthread_join(w, NULL);
    if (r != 0) { puts("FAIL active again: no wait"); return 1; }
    puts("ok");
    return 0;
}
"""
cc = shutil.which("cc") or shutil.which("clang")
if sys.platform != "darwin" or not cc:
    print("SKIP: the wait uses pthread_main_np (macOS CI runs it)")
else:
    with tempfile.TemporaryDirectory() as t:
        c = Path(t) / "wait.c"
        c.write_text(harness)
        exe = Path(t) / "wait"
        b = subprocess.run([cc, "-std=c11", "-Wall", "-o", str(exe), str(c)], capture_output=True, text=True)
        check("the bridge's wait compiles on its own", b.returncode == 0)
        if b.returncode:
            print(b.stderr[-2000:])
        else:
            r = subprocess.run([str(exe)], capture_output=True, text=True, timeout=60)
            if r.returncode:
                print(r.stdout[-2000:])
            check("active: no wait; main thread: no wait; a worker waits until active, then goes on",
                  r.returncode == 0 and r.stdout.strip().endswith("ok"))

print("PASS: pause while inactive" if ok else "FAILED: pause while inactive")
sys.exit(0 if ok else 1)

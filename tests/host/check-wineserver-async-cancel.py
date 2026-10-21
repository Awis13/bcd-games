#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""tools/patch-wineserver-async-cancel.py: the wineserver's cancel_process_async no longer touches an async
that cancel_async already freed (BeamNG.drive, builds 574 and 576: the whole app died in req_cancel_async+0x158).

Checks:
  1. the patch applies once to the pinned server/async.c (when the Wine sources are there), is idempotent, and an
     anchor that moved is reported by name;
  2. the REAL async.c, original and patched, compiled on the host into a library (the object layer, the fd layer and
     the APC queue are small stand-ins; a freed object is zeroed and kept mapped, as the iOS 26/27 allocator returns
     it) and driven through cancel_process_async, each scenario in a forked child:
       - the APC of the async's thread cannot be queued (iOS: send_thread_signal always fails, nobody else waits): the
         original dies on the NULL link of list_remove(&async->process_entry), exactly the logged fault; the patched
         one finishes, frees the async once, leaves the process list empty, and hands out no cancel handle (there is
         nothing to wait for, so NtCancelIoFileEx returns instead of waiting for a group nobody will complete);
       - the APC is queued (Linux, or a waiting thread): original and patched print the SAME state before and after
         the thread runs the APC (reference count, list membership, cancel group count, handle, signal), so nothing
         changes where cancel already worked;
       - one async queued and one not: the group counts only the pending one and is signalled once, when it ends;
  3. the workflow applies the patch before the wineserver is built, runs this check, and auto-tune names the crash
     as fixed in 577.
Needs a C compiler (cc) and the Wine source tree (BCD_WINE_SRC, or wine/ in the repository, or ~/madeira-bcd/wine).
"""
from pathlib import Path
import ctypes
import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[2]
ok = True


def check(what, cond, extra=""):
    global ok
    print(("ok   " if cond else "FAIL ") + what + ((" :: " + extra) if (extra and not cond) else ""))
    ok &= bool(cond)


spec = importlib.util.spec_from_file_location("patch_async", root / "tools/patch-wineserver-async-cancel.py")
patch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(patch)

HARNESS = r'''
/* Host harness: the real server/async.c, compiled as part of this file, driven through cancel_process_async
 * with the object layer, the fd layer and the APC queue replaced by small stand-ins. Freed objects are zeroed
 * and never returned to malloc, which is what the iOS 26/27 allocator hands back to the next reader (the
 * fault in the BeamNG logs: x9 = x8 = 0 in list_remove). */
#include "async.c"

#include <stdlib.h>
#include <unistd.h>
#include <signal.h>

struct type_descr no_type;
unsigned int global_error;
struct thread *current;

static unsigned int freed_objects;
static unsigned int apc_drops;      /* bit i: the APC for async i cannot be queued (iOS: busy thread, nobody waiting) */
static struct object *pending_apc[8];
static int npending, sync_signals, handles_allocated;

#define DIE(...) do { fprintf( stderr, "HARNESS: " __VA_ARGS__ ); fputc( '\n', stderr ); _exit( 3 ); } while (0)

void *alloc_object( const struct object_ops *ops )
{
    struct object *obj = calloc( 1, ops->size );
    obj->refcount = 1;
    obj->ops = ops;
    list_init( &obj->wait_queue );
    return obj;
}

struct object *grab_object( void *ptr )
{
    struct object *obj = ptr;
    if (!obj->refcount) DIE( "grab of a freed object %p", (void *)obj );
    obj->refcount++;
    return obj;
}

void release_object( void *ptr )
{
    struct object *obj = ptr;
    size_t size;

    if (!obj->refcount) DIE( "release of a freed object %p (the ml574 [dead-release] case)", (void *)obj );
    if (--obj->refcount) return;
    size = obj->ops->size;
    if (obj->ops->destroy) obj->ops->destroy( obj );
    freed_objects++;
    memset( obj, 0, size );         /* zeroed, kept mapped: reads give 0, as on the device */
}

void *memdup( const void *data, size_t len ) { void *p = malloc( len ); memcpy( p, data, len ); return p; }
int add_queue( struct object *obj, struct wait_queue_entry *entry ) { return 1; }
void remove_queue( struct object *obj, struct wait_queue_entry *entry ) {}
int no_add_queue( struct object *obj, struct wait_queue_entry *entry ) { return 0; }
int no_signal( struct object *obj, unsigned int access, int signal ) { return 0; }
struct fd *no_get_fd( struct object *obj ) { return NULL; }
WCHAR *no_get_full_name( struct object *obj, data_size_t max, data_size_t *ret_len ) { return NULL; }
struct object *no_lookup_name( struct object *obj, struct unicode_str *name, unsigned int attr,
                               struct object *root ) { return NULL; }
int no_link_name( struct object *obj, struct object_name *name, struct object *parent ) { return 0; }
struct object *no_open_file( struct object *obj, unsigned int access, unsigned int sharing, unsigned int options )
{ return NULL; }
struct list *no_kernel_obj_list( struct object *obj ) { return NULL; }
int no_close_handle( struct object *obj, struct process *process, obj_handle_t handle ) { return 1; }
struct object *default_get_sync( struct object *obj ) { return grab_object( obj ); }
unsigned int default_map_access( struct object *obj, unsigned int access ) { return access; }
struct security_descriptor *default_get_sd( struct object *obj ) { return NULL; }
int default_set_sd( struct object *obj, const struct security_descriptor *sd, unsigned int set_info ) { return 0; }

struct object *create_internal_sync( int manual, int signaled )
{
    static const struct object_ops sync_ops = { sizeof(struct object), &no_type };
    return alloc_object( &sync_ops );
}
void signal_sync( struct object *sync ) { sync_signals++; }

obj_handle_t alloc_handle( struct process *process, void *obj, unsigned int access, unsigned int attr )
{
    grab_object( obj );             /* the handle table's reference */
    handles_allocated++;
    return 0x1234;
}
unsigned int close_handle( struct process *process, obj_handle_t handle ) { return 0; }
struct object *get_handle_obj( struct process *process, obj_handle_t handle, unsigned int access,
                               const struct object_ops *ops ) { return NULL; }
unsigned int get_obj_handle_count( struct process *process, const struct object *obj ) { return 1; }
struct event *get_event_obj( struct process *process, obj_handle_t handle, unsigned int access ) { return NULL; }
void set_event( struct event *event ) {}
void reset_event( struct event *event ) {}
struct thread *get_thread_from_handle( obj_handle_t handle, unsigned int access ) { return NULL; }
void set_wait_status( struct wait_queue_entry *entry, int status ) {}
void wake_up( struct object *obj, int max ) {}
struct timeout_user *add_timeout_user( timeout_t when, timeout_callback func, void *private ) { return NULL; }
void remove_timeout_user( struct timeout_user *user ) {}
void add_completion( struct completion *completion, apc_param_t ckey, apc_param_t cvalue,
                     unsigned int status, apc_param_t information ) {}
struct completion *fd_get_completion( struct fd *fd, apc_param_t *p_key ) { return NULL; }
void *get_fd_user( struct fd *fd ) { return fd; }          /* the fd stands for its user object */
int is_fd_overlapped( struct fd *fd ) { return 1; }
void set_fd_signaled( struct fd *fd, int signaled ) {}
void fd_reselect_async( struct fd *fd, struct async_queue *queue ) {}

/* default_fd_cancel_async */
void fd_cancel_async( struct fd *fd, struct async *async ) { async_terminate( async, STATUS_CANCELLED ); }

/* thread_queue_apc as thread.c has it: the APC holds the owner; if it cannot be queued it is released at once and
 * thread_apc_destroy completes the async with the APC's own status. The iOS drop is apc_drops = 1. */
int thread_queue_apc( struct process *process, struct thread *thread, struct object *owner,
                      const union apc_call *call_data )
{
    grab_object( owner );           /* create_apc: apc->owner */
    if (!(apc_drops & (1u << (((struct async *)owner)->data.user - 0x1000))))
    {
        pending_apc[npending++] = owner;    /* queued: runs when the thread next waits */
        return 1;
    }
    async_set_result( owner, call_data->async_io.status, 0 );   /* thread_apc_destroy */
    release_object( owner );
    return 0;
}

/* the thread runs the queued APC and reports the client's result */
static void run_pending_apcs( void )
{
    while (npending)
    {
        struct object *owner = pending_apc[--npending];
        async_set_result( owner, STATUS_CANCELLED, 0 );
        release_object( owner );
    }
}

static struct process proc;
static struct thread thr;
static struct object fd_obj[4];
static struct async_queue queue[4];
static struct async *a[4];
static const struct object_ops fixed_ops = { sizeof(struct object), &no_type };

static void setup( int nasyncs )
{
    int i;

    list_init( &proc.asyncs );
    thr.process = &proc;
    thr.obj.refcount = 1000; thr.obj.ops = &fixed_ops;
    proc.obj.refcount = 1000; proc.obj.ops = &fixed_ops;
    current = NULL;
    for (i = 0; i < nasyncs; i++)
    {
        struct async_data data;

        fd_obj[i].refcount = 1000; fd_obj[i].ops = &fixed_ops;
        list_init( &queue[i].queue );
        memset( &data, 0, sizeof(data) );
        data.user = 0x1000 + i;
        a[i] = create_async( (struct fd *)&fd_obj[i], &thr, &data, NULL );
        queue_async( &queue[i], a[i] );      /* queue reference */
        release_object( a[i] );              /* the request's own reference: only the queue's is left */
        a[i]->wait_handle = 0;               /* non-blocking: the wait handle is closed already */
    }
}

static unsigned int refs( struct async *x ) { return x->obj.refcount; }
static int listed( struct async *x )
{
    struct async *it;
    LIST_FOR_EACH_ENTRY( it, &proc.asyncs, struct async, process_entry ) if (it == x) return 1;
    return 0;
}

static void quiet_fault( int sig ) { _exit( 128 + sig ); }

int scenario( const char *what )
{
    obj_handle_t wait_handle = 0;
    int count;
    struct sigaction sa;

    setvbuf( stdout, NULL, _IONBF, 0 );
    /* The original code is meant to fault here. Ending the forked child with _exit (128 + signal) instead of a real
     * crash keeps macOS from writing a crash report and opening a "Python quit unexpectedly" dialog on the Mac. */
    memset( &sa, 0, sizeof(sa) );
    sa.sa_handler = quiet_fault;
    sigaction( SIGSEGV, &sa, NULL );
    sigaction( SIGBUS, &sa, NULL );

    if (!strcmp( what, "drop-single" ) || !strcmp( what, "drop-group" ))
    {
        int group = !strcmp( what, "drop-group" );

        apc_drops = 1;
        setup( 1 );
        count = cancel_process_async( &proc, &fd_obj[0], group ? &thr : NULL, 0, &wait_handle );
        printf( "count=%d wait_handle=%#x freed=%u list_empty=%d sync_signals=%d handles=%d\n", count, wait_handle,
                freed_objects, list_empty( &proc.asyncs ), sync_signals, handles_allocated );
    }
    else if (!strcmp( what, "drop-two" ))
    {
        apc_drops = 3;
        setup( 2 );
        count = cancel_process_async( &proc, NULL, NULL, 0, &wait_handle );
        printf( "count=%d freed=%u list_empty=%d\n", count, freed_objects, list_empty( &proc.asyncs ) );
    }
    else if (!strcmp( what, "queued-group" ) || !strcmp( what, "queued-single" ))
    {
        int group = !strcmp( what, "queued-group" );

        apc_drops = 0;
        setup( 1 );
        count = cancel_process_async( &proc, &fd_obj[0], group ? &thr : NULL, 0, &wait_handle );
        printf( "count=%d wait_handle=%#x refs=%u listed=%d canceled=%d terminated=%d "
                "async_cancel=%d group_count=%u freed=%u handles=%d\n", count, wait_handle, refs( a[0] ),
                listed( a[0] ), a[0]->canceled, a[0]->terminated, a[0]->async_cancel != NULL,
                a[0]->async_cancel ? a[0]->async_cancel->count : 0, freed_objects, handles_allocated );
        run_pending_apcs();
        printf( "after the APC ran: freed=%u sync_signals=%d list_empty=%d\n", freed_objects, sync_signals,
                list_empty( &proc.asyncs ) );
    }
    else if (!strcmp( what, "mixed" ))
    {
        /* the first async's APC is queued, the second one's cannot be */
        apc_drops = 2;
        setup( 2 );
        count = cancel_process_async( &proc, NULL, &thr, 0, &wait_handle );
        printf( "count=%d wait_handle=%#x listed0=%d listed1=%d group_count=%u freed=%u\n", count, wait_handle,
                listed( a[0] ), listed( a[1] ), a[0]->async_cancel ? a[0]->async_cancel->count : 0, freed_objects );
        run_pending_apcs();
        printf( "after the APC ran: freed=%u sync_signals=%d\n", freed_objects, sync_signals );
    }
    else DIE( "unknown scenario %s", what );
    return 0;
}
'''


def wine_tree():
    for c in (os.environ.get("BCD_WINE_SRC"), root / "wine", Path.home() / "madeira-bcd/wine"):
        if c and (Path(c) / "server/async.c").is_file():
            return Path(c)
    return None


def run(lib, name):
    """One scenario in a forked child: (exit code or -signal, output)."""
    r, w = os.pipe()
    pid = os.fork()
    if pid == 0:
        os.close(r)
        os.dup2(w, 1)
        os.dup2(w, 2)
        code = lib.scenario(name.encode())
        os._exit(code & 0xff)
    os.close(w)
    out = b""
    while True:
        chunk = os.read(r, 4096)
        if not chunk:
            break
        out += chunk
    os.close(r)
    _, status = os.waitpid(pid, 0)
    code = -os.WTERMSIG(status) if os.WIFSIGNALED(status) else os.WEXITSTATUS(status)
    return code, out.decode(errors="replace").strip()


wine = wine_tree()
if wine is None or not shutil.which("cc"):
    print("SKIP: no Wine source tree or no C compiler (set BCD_WINE_SRC)")
else:
    original = (wine / "server/async.c").read_text()
    if patch.MARKER in original:    # a build clone the workflow already patched: step back to the pinned text
        for old, new in patch.EDITS:
            original = original.replace(new, old)
        check("a tree that is already patched can be stepped back to the pinned text", patch.MARKER not in original)
    patched = patch.patch(original)
    check("the patch changes the pinned async.c", patched != original and patch.MARKER in patched)
    check("a second run changes nothing", patch.patch(patched) == patched)
    try:
        patch.patch(original.replace("async->is_system     = 0;", "async->is_system     = 0; /* moved */", 1)
                    .replace("    unsigned int         is_system :1;", "    unsigned int         is_systemx :1;", 1))
        check("a moved anchor is reported", False)
    except ValueError as error:
        check("a moved anchor is reported by name", "anchor missing" in str(error))
    check("the walk grabs before cancel_async and releases after the async is back on the list",
          patched.count("grab_object( &async->obj );") == 1 and
          patched.index("grab_object( &async->obj );") < patched.index("if (!async->canceled) cancel_async( async );") and
          "release_object( &async->obj );   /* madeira-bcd async-cancel-keepalive: the keepalive" in patched)
    for line in original.splitlines():
        check_line = line.strip()
        if check_line and check_line not in patched:
            if "if (cancel)" in check_line or "if (!async->canceled) cancel_async( async );" in check_line:
                continue
            check("every original line is kept: " + check_line, False)
    with tempfile.TemporaryDirectory() as t:
        t = Path(t)
        libs = {}
        for tag, text in (("orig", original), ("patched", patched)):
            (t / tag).mkdir()
            (t / tag / "async.c").write_text(text)
            (t / "harness.c").write_text(HARNESS)
            (t / "config.h").write_text("")
            ext = ".dylib" if sys.platform == "darwin" else ".so"
            r = subprocess.run(["cc", "-shared", "-fPIC", "-O0", "-g", "-w", "-D__WINESRC__", "-I" + str(t),
                                "-I" + str(t / tag), "-I" + str(wine / "server"), "-I" + str(wine / "include"),
                                "-I" + str(wine / "include/wine"), "-include", "stdarg.h",
                                str(t / "harness.c"), "-o", str(t / (tag + ext))], capture_output=True, text=True)
            check("the harness compiles against the " + tag + " async.c", r.returncode == 0, r.stderr[-1500:])
            if r.returncode == 0:
                libs[tag] = ctypes.CDLL(str(t / (tag + ext)))
            sys.stdout.flush()
        if len(libs) == 2:
            for lib in libs.values():
                lib.scenario.argtypes = [ctypes.c_char_p]
                lib.scenario.restype = ctypes.c_int
            sys.stdout.flush()
            res = {tag: {n: run(lib, n) for n in ("drop-single", "drop-group", "drop-two", "queued-single",
                                                  "queued-group", "mixed")} for tag, lib in libs.items()}
            for n in ("drop-single", "drop-group", "drop-two", "mixed"):
                code, out = res["orig"][n]
                check("original, " + n + ": dies on the freed async (SIGSEGV/SIGBUS), as the device log does",
                      code in (-11, -10, 128 + 11, 128 + 10), "%s %s" % (code, out))
            expect = {
                "drop-single": "count=1 wait_handle=0 freed=1 list_empty=1 sync_signals=0 handles=0",
                "drop-group": "count=1 wait_handle=0 freed=3 list_empty=1 sync_signals=0 handles=0",
                "drop-two": "count=2 freed=2 list_empty=1",
            }
            for n, text in expect.items():
                code, out = res["patched"][n]
                check("patched, " + n + ": finishes, " + text, code == 0 and out == text, "%s %s" % (code, out))
            code, out = res["patched"]["mixed"]
            check("patched, mixed: the group counts the pending async only and is signalled once it ends",
                  code == 0 and out.splitlines()[0] == "count=2 wait_handle=0x1234 listed0=1 listed1=0 group_count=1 freed=1"
                  and out.splitlines()[1] == "after the APC ran: freed=2 sync_signals=1", "%s %s" % (code, out))
            for n in ("queued-single", "queued-group"):
                check("original and patched print the same state before and after the APC, " + n + " (nothing changes "
                      "where cancel worked)", res["orig"][n] == res["patched"][n] and res["patched"][n][0] == 0,
                      "%s vs %s" % (res["orig"][n], res["patched"][n]))
            print("     queued-group state: " + res["patched"]["queued-group"][1].replace("\n", " | "))

import fnmatch
check("the script's name stays outside the glob tools/patch-wine-*.py, which keys the i386 farm's cache (a server "
      "patch must not make the farm rebuild)", not fnmatch.fnmatch("patch-wineserver-async-cancel.py", "patch-wine-*.py"))
wf = (root / ".github/workflows/build-ipa.yml").read_text()
step = "python3 tools/patch-wineserver-async-cancel.py wine"
check("the workflow patches the Wine server and runs this check", step in wf and
      "python3 tests/host/check-wineserver-async-cancel.py" in wf)
if step in wf:
    check("the patch runs before the base libwineserver.a and the wineserver are built",
          wf.index(step) < wf.index("- name: Build base libwineserver.a from wine/server sources") <
          wf.index("- name: Build wineserver"))
rules = (root / "build/tools/autotune_rules.py").read_text()
rid = rules.find('"id": "wineserver-cancel-async-freed"')
check("auto-tune names the crash as fixed in 577", rid >= 0 and '"fixedIn": 577' in rules[rid:rid + 400] and
      "req_cancel_async" in rules[rid:rid + 2500])
sys.exit(0 if ok else 1)

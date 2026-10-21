#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""QueryDisplayConfig and the virtual monitor's path (env.MADEIRA_VDCFG_PATH, round 579); no Wine runs.

Trails in the Sky 1st Chapter (24VADD, X56ZXZ, YTMNEK): GetDisplayConfigBufferSizes says "1 path, 2 modes" for the
virtual monitor, QueryDisplayConfig skips it (no source) and says "success, 0 paths, 0 modes"; the game's next
SetWindowPos is {32767,32767}-{32768,32768}, then a NULL swapchain (sora_1st.exe+0x5215E0). The default answers do not
change; the one new line, [vdcfg-mismatch], is written only when the two disagree; env.MADEIRA_VDCFG_PATH = 1 lists
the path.

1. The vdcfg-test region compiled on the host: the key's spellings, off by default, the one monitor it may list.
2. Wiring, read from build/win32u-unix/sysparams_ios.c: the sizes call is as it was; the query skips a sourceless
   monitor unless the key lists it (and only the virtual monitor); the path uses the virtual monitor's current mode
   and the shared adapter LUID; the device-info names are behind the key; the mismatch line only on success with no
   path; the LUID choice moved out of d3dkmt_open_adapter_from_gdi_display_name unchanged.
3. The catalog knows the key (off), and the auto-tune rule asks for it only after Trails' crash signature.
4. (when the pinned Wine headers and an iPhone SDK are there) the whole file still compiles.
"""
from pathlib import Path
import os, re, shutil, subprocess, sys, tempfile

root = Path(__file__).resolve().parents[2]
src = (root / "build/win32u-unix/sysparams_ios.c").read_text()
ok = True


def check(what, cond, extra=""):
    global ok
    print(("ok   " if cond else "FAIL ") + what + ("" if cond or not extra else "  -- " + extra))
    ok &= bool(cond)


def func(text, sig):
    """The definition: the last occurrence (prototypes come first)."""
    body = text[text.rindex(sig):]
    return body[:body.index("\n}\n") + 3]


# ------------------------------------------------------------------ 1. the region
region = src[src.index("/* vdcfg-test:begin"):src.index("/* vdcfg-test:end */")]
harness = r"""
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
typedef unsigned int UINT32;
static int said;
static int dprintf_stub( int fd, const char *fmt, ... ) { (void)fd; (void)fmt; said++; return 0; }
#define dprintf dprintf_stub
""" + region + r"""
int main( void )
{
    int on = ios_vdcfg_path_enabled();
    /* the predicate: only the key, only a sourceless monitor, only the virtual one */
    int rows[8][3] = {{0,1,1},{1,0,1},{1,1,0},{1,1,1},{0,0,0},{0,1,0},{1,0,0},{0,0,1}};
    int want[8] = {0,0,0,1,0,0,0,0};
    int i;
    for (i = 0; i < 8; i++)
        if (ios_vdcfg_lists_sourceless( rows[i][0], rows[i][1], rows[i][2] ) != want[i]) return 10 + i;
    if (ios_vdcfg_sizes_paths || ios_vdcfg_sizes_modes) return 30;
    printf( "%d %d\n", on, said );
    return 0;
}
"""
with tempfile.TemporaryDirectory() as t:
    c = Path(t) / "vdcfg.c"
    c.write_text(harness)
    exe = Path(t) / "vdcfg"
    r = subprocess.run(["cc", "-std=gnu11", "-Wall", "-Wno-unused-function", "-fsanitize=address,undefined", str(c), "-o", str(exe)],
                       capture_output=True, text=True)
    check("the vdcfg-test region compiles", r.returncode == 0, r.stderr[-800:])
    if r.returncode == 0:
        def run(value):
            env = {k: v for k, v in os.environ.items() if k != "MADEIRA_VDCFG_PATH"}
            if value is not None:
                env["MADEIRA_VDCFG_PATH"] = value
            p = subprocess.run([str(exe)], capture_output=True, text=True, env=env)
            return p.returncode, p.stdout.strip()
        rc, out = run(None)
        check("the key is off when unset", (rc, out) == (0, "0 0"), "%s %s" % (rc, out))
        for v, want in [("1", "1 1"), ("on", "1 1"), ("ON", "1 1"), ("true", "1 1"), ("Yes", "1 1"),
                        ("0", "0 0"), ("", "0 0"), ("off", "0 0"), ("2", "0 0"), ("11", "0 0"), ("no", "0 0")]:
            rc, out = run(v)
            check("MADEIRA_VDCFG_PATH=%r gives %s" % (v, "on (one log line)" if want == "1 1" else "off (no log line)"),
                  (rc, out) == (0, want), "%s %s" % (rc, out))

# ------------------------------------------------------------------ 2. wiring
sizes = func(src, "LONG WINAPI NtUserGetDisplayConfigBufferSizes(")
check("the sizes call still counts every active monitor (sourceless ones too), 2 modes per path",
      "if (!is_monitor_active( monitor )) continue;\n            count++;" in sizes
      and "*num_mode_info = count * 2;" in sizes and "monitor->source" not in sizes)
check("the sizes call only remembers its answer for the mismatch line",
      "ios_vdcfg_sizes_paths = *num_path_info;" in sizes and "ios_vdcfg_sizes_modes = *num_mode_info;" in sizes
      and sizes.index("ios_vdcfg_sizes_paths") > sizes.index("TRACE( \"returning"))

q = func(src, "LONG WINAPI NtUserQueryDisplayConfig(")
check("a sourceless monitor is skipped unless the key lists the virtual monitor",
      "if (!ios_vdcfg_lists_sourceless( ios_vdcfg_path_enabled(), 1, monitor == &virtual_monitor ))\n"
      "            {\n                skipped++;\n                continue;\n            }" in q)
check("a monitor with a source takes the unchanged branch (source id, its GPU's LUID, its current settings)",
      "source_index = monitor->source->id;\n            gpu_luid = &monitor->source->gpu->luid;\n"
      "            output_id = monitor->output_id;" in q
      and "if (!source_get_current_settings( monitor->source, &devmode ))" in q)
check("the virtual path: source 0, the virtual monitor's output, the shared adapter LUID, its current mode",
      "source_index = 0;" in q and "ios_virtual_luid_locked( &virtual_luid );" in q and "gpu_luid = &virtual_luid;" in q
      and "ios_virtual_enum_display_settings( ENUM_CURRENT_SETTINGS, &devmode, 0 );" in q)
check("the same buffer checks, path and mode building as before (one body for every path)",
      q.count("set_mode_source_info(") == 1 and q.count("set_path_source_info(") == 1
      and "ret = ERROR_INSUFFICIENT_BUFFER;" in q and "*paths_count = path_index;" in q)
check("the mismatch line: success, no path, a sizes answer with paths; twice per process",
      "if (ret == ERROR_SUCCESS && !path_index && ios_vdcfg_sizes_paths)" in q and "if (mismatch++ < 2)" in q
      and "[vdcfg-mismatch]" in q and "env.MADEIRA_VDCFG_PATH = 1" in q)
check("the input room is taken before the counts are overwritten",
      q.index("offered_paths = *paths_count;") < q.index("*paths_count = path_index;"))
check("the existing [vdcfg] QueryDisplayConfig line is as it was",
      '"[vdcfg] QueryDisplayConfig flags=%#x -> %ld, %u paths %u modes\\n"' in q)

# The LUID choice is the one d3dkmt_open_adapter_from_gdi_display_name always made, now in one helper.
helper = func(src, "static void ios_virtual_luid_locked( LUID *out )")
check("the shared LUID helper keeps the three choices and their log lines (KMT adapter, registered GPU, software)",
      "madeira_kmt_adapter_enabled() && madeira_kmt_adapter_luid( &ios_virtual_adapter_luid )" in helper
      and "ios_virtual_adapter_luid = first->luid;" in helper
      and "NtAllocateLocallyUniqueId( &ios_virtual_adapter_luid );" in helper
      and helper.count("[vmode] ml1006") == 3 and "ios_virtual_adapter_luid_ready = TRUE;" in helper)
kmt = func(src, "static NTSTATUS d3dkmt_open_adapter_from_gdi_display_name(")
check("the d3dkmt path takes its LUID from the helper under the display lock, as before",
      "pthread_mutex_lock( &display_lock );\n        ios_virtual_luid_locked( &luid_desc.AdapterLuid );" in kmt
      and "pthread_mutex_unlock( &display_lock );" in kmt and "static LUID virtual_luid;" not in kmt)

info = func(src, "static NTSTATUS display_config_get_device_info(")
check("DisplayConfigGetDeviceInfo: the source name of the virtual monitor only with the key, id 0 and the shared LUID",
      "if (ret != STATUS_SUCCESS && ios_vdcfg_path_enabled() && !source_name->header.id)" in info
      and "asciiz_to_unicode( source_name->viewGdiDeviceName, \"\\\\\\\\.\\\\DISPLAY1\" );" in info)
check("DisplayConfigGetDeviceInfo: the target name of the virtual monitor only with the key, through the same predicate",
      "if (!monitor->source && ios_vdcfg_lists_sourceless( ios_vdcfg_path_enabled(), 1, monitor == &virtual_monitor ))" in info
      and "ios_vmon_device_id( EDD_GET_DEVICE_INTERFACE_NAME, path, sizeof(path) );" in info)
check("DisplayConfigGetDeviceInfo: the adapter name of the virtual adapter only with the key and the shared LUID (the registry GPU's interface)",
      "if (ret != STATUS_SUCCESS && ios_vdcfg_path_enabled())" in info and "madeira_kmt_identity( &kmt_id );" in info
      and info.index("ios_vdcfg_path_enabled()", info.index("DISPLAYCONFIG_DEVICE_INFO_GET_ADAPTER_NAME")) > info.index("DISPLAYCONFIG_DEVICE_INFO_GET_ADAPTER_NAME"))
check("the other device-info types keep their sourceless skip",
      info.count("if (!monitor->source) continue;") >= 3)
check("nothing reads the key outside the region and these four places (the query and the three names)",
      src.count("ios_vdcfg_path_enabled()") == 4, str(src.count("ios_vdcfg_path_enabled()")))

# ------------------------------------------------------------------ 2b. the real functions, run on the host
# The production QueryDisplayConfig / GetDisplayConfigBufferSizes / vdcfg region / LUID helper / virtual-monitor mode code
# are cut out of the file and compiled against Wine's own headers with small stubs for the display lists. The BEFORE text is
# the function as it was in build 578 (QueryDisplayConfig skipped a monitor without a source): with the key off the two
# must give byte-identical answers for every scenario.
QDC_BEFORE = r"""
LONG WINAPI NtUserQueryDisplayConfig( UINT32 flags, UINT32 *paths_count, DISPLAYCONFIG_PATH_INFO *paths,
                                      UINT32 *modes_count, DISPLAYCONFIG_MODE_INFO *modes,
                                      DISPLAYCONFIG_TOPOLOGY_ID *topology_id )
{
    ULONG source_index;
    LONG ret;
    UINT32 output_id, source_mode_index, path_index = 0, mode_index = 0;
    const LUID *gpu_luid;
    DEVMODEW devmode;
    struct monitor *monitor;
    DWORD retrieve_flags = flags & qdc_retrieve_flags_mask;

    TRACE( "flags %#x, paths_count %p, paths %p, modes_count %p, modes %p, topology_id %p.\n",
           flags, paths_count, paths, modes_count, modes, topology_id );

    if (!paths_count || !modes_count)
        return ERROR_INVALID_PARAMETER;

    if (!*paths_count || !*modes_count)
        return ERROR_INVALID_PARAMETER;

    if (retrieve_flags != QDC_ALL_PATHS &&
        retrieve_flags != QDC_ONLY_ACTIVE_PATHS &&
        retrieve_flags != QDC_DATABASE_CURRENT)
        return ERROR_INVALID_PARAMETER;

    if (((retrieve_flags == QDC_DATABASE_CURRENT) && !topology_id) ||
        ((retrieve_flags != QDC_DATABASE_CURRENT) && topology_id))
        return ERROR_INVALID_PARAMETER;

    if ((flags & ~(qdc_retrieve_flags_mask | QDC_VIRTUAL_MODE_AWARE)))
    {
        FIXME( "unsupported flags %#x.\n", flags );
        return ERROR_INVALID_PARAMETER;
    }

    if (retrieve_flags != QDC_ONLY_ACTIVE_PATHS)
        FIXME( "only returning active paths\n" );

    if (topology_id)
    {
        FIXME( "setting toplogyid to DISPLAYCONFIG_TOPOLOGY_INTERNAL\n" );
        *topology_id = DISPLAYCONFIG_TOPOLOGY_INTERNAL;
    }

    if (!lock_display_devices( FALSE ))
        return ERROR_GEN_FAILURE;

    ret = ERROR_GEN_FAILURE;

    LIST_FOR_EACH_ENTRY( monitor, &monitors, struct monitor, entry )
    {
        if (!is_monitor_active( monitor )) continue;
        if (!monitor->source) continue;

        source_index = monitor->source->id;
        gpu_luid = &monitor->source->gpu->luid;
        output_id = monitor->output_id;

        memset( &devmode, 0, sizeof(devmode) );
        devmode.dmSize = sizeof(devmode);
        if (!source_get_current_settings( monitor->source, &devmode ))
        {
            goto done;
        }

        if (path_index == *paths_count || mode_index == *modes_count)
        {
            ret = ERROR_INSUFFICIENT_BUFFER;
            goto done;
        }

        if (!source_mode_exists( modes, mode_index, source_index, &source_mode_index ))
        {
            set_mode_source_info( &modes[mode_index], gpu_luid, source_index, &devmode );
            source_mode_index = mode_index;
            if (++mode_index == *modes_count)
            {
                ret = ERROR_INSUFFICIENT_BUFFER;
                goto done;
            }
        }

        paths[path_index].flags = DISPLAYCONFIG_PATH_ACTIVE;
        set_mode_target_info( &modes[mode_index], gpu_luid, output_id, flags, &devmode );
        if (flags & QDC_VIRTUAL_MODE_AWARE)
        {
            paths[path_index].flags |= DISPLAYCONFIG_PATH_SUPPORT_VIRTUAL_MODE;
            if (++mode_index == *modes_count)
            {
                ret = ERROR_INSUFFICIENT_BUFFER;
                goto done;
            }
            set_mode_desktop_info( &modes[mode_index], gpu_luid, output_id, &modes[source_mode_index].sourceMode );
            set_path_target_info( &paths[path_index].targetInfo, gpu_luid, output_id, mode_index - 1, mode_index,
                                  flags, &devmode );
        }
        else set_path_target_info( &paths[path_index].targetInfo, gpu_luid, output_id, mode_index, ~0u, flags, &devmode );
        ++mode_index;

        set_path_source_info( &paths[path_index].sourceInfo, gpu_luid, source_index, source_mode_index, flags );
        path_index++;
    }

    *paths_count = path_index;
    *modes_count = mode_index;
    ret = ERROR_SUCCESS;

done:
    unlock_display_devices();
    return ret;
}
"""


def cut(text, start, end_marker, include_end=False):
    a = text.index(start)
    b = text.index(end_marker, a)
    return text[a:b + (len(end_marker) if include_end else 0)]


def host_harness(query_text):
    structs = cut(src, "struct gpu\n{", "static struct list gpus")
    qdc_mask = "static const UINT32 qdc_retrieve_flags_mask = QDC_ALL_PATHS | QDC_ONLY_ACTIVE_PATHS | QDC_DATABASE_CURRENT;\n"
    virtual_monitor = cut(src, "#define VIRTUAL_HMONITOR", "};\n", True)
    screen = cut(src, "static int ios_screen_cur_w, ios_screen_cur_h;", "\n}\n", True)
    screen = screen[:screen.index("static void ios_screen_size")] + func(src, "static void ios_screen_size( int *w, int *h )")
    modes = cut(src, "static const struct { short w, h; } ios_standard_modes[] =", "/* iOS-Madeira: does this device name refer")
    vregion = src[src.index("#ifdef WINE_IOS\n/* madeira-bcd (round 579, Trails"):src.index("static BOOL ios_virtual_enum_display_settings( DWORD index, DEVMODEW *devmode, DWORD flags );   /* below */")] + "\n#endif\n"
    dc_helpers = cut(src, "static DISPLAYCONFIG_ROTATION get_dc_rotation(", "/***********************************************************************\n *              NtUserQueryDisplayConfig")
    sizes_fn = func(src, "LONG WINAPI NtUserGetDisplayConfigBufferSizes(")
    return r"""
#include <stdio.h>
#include <stdarg.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
#include <unistd.h>
#include <pthread.h>
#include "windef.h"
#include "winbase.h"
#include "wingdi.h"
#include "winuser.h"
#include "winerror.h"
#include "d3dkmdt.h"
#include "wine/list.h"
#define WINE_IOS 1
#define ARRAY_SIZE(a) (sizeof(a) / sizeof((a)[0]))
#define WINE_ENUM_PHYSICAL_SETTINGS ((DWORD)-3)
#define TRACE(...) do {} while (0)
#define FIXME(...) do {} while (0)
static char logbuf[8192];
static int dprintf_cap( int fd, const char *fmt, ... )
{
    va_list ap; size_t n = strlen( logbuf );
    (void)fd;
    va_start( ap, fmt ); vsnprintf( logbuf + n, sizeof(logbuf) - n, fmt, ap ); va_end( ap );
    return 0;
}
#define dprintf dprintf_cap
static void RtlSetLastWin32Error( DWORD e ) { (void)e; }
static LUID kmt_luid = { 0x4444, 0x7777 };
static int kmt_on, kmt_ok = 1, lock_ok = 1;
static int madeira_kmt_adapter_enabled(void) { return kmt_on; }
static int madeira_kmt_adapter_luid( LUID *l ) { *l = kmt_luid; return kmt_ok; }
static void NtAllocateLocallyUniqueId( LUID *l ) { l->LowPart = 0x1234; l->HighPart = 0; }
""" + qdc_mask + structs + r"""
static struct list gpus = LIST_INIT(gpus);
static struct list sources = LIST_INIT(sources);
static struct list monitors = LIST_INIT(monitors);
""" + virtual_monitor + "\n" + screen + "\n" + r"""
static BOOL lock_display_devices( BOOL force ) { (void)force; return lock_ok; }
static void unlock_display_devices(void) {}
static BOOL is_monitor_active( struct monitor *monitor )
{
    if (!monitor->source) return TRUE;
    return (monitor->source->state_flags & DISPLAY_DEVICE_ATTACHED_TO_DESKTOP) != 0;
}
static BOOL source_get_current_settings( struct source *source, DEVMODEW *mode ) { *mode = source->current; return TRUE; }
""" + vregion + modes + dc_helpers + sizes_fn + "\n" + query_text + r"""
/* ---- scenarios -------------------------------------------------------------------------------------------- */
static struct gpu gpu1; static struct source src1; static struct monitor mon_src, mon_other;
static void reset( int regime )
{
    list_init( &monitors ); list_init( &gpus ); list_init( &sources );
    ios_vdcfg_sizes_paths = ios_vdcfg_sizes_modes = 0;
    logbuf[0] = 0; lock_ok = 1;
    memset( &gpu1, 0, sizeof(gpu1) ); gpu1.luid.LowPart = 0xabcd; gpu1.luid.HighPart = 9;
    memset( &src1, 0, sizeof(src1) ); src1.gpu = &gpu1; src1.id = 2; src1.state_flags = DISPLAY_DEVICE_ATTACHED_TO_DESKTOP;
    src1.current.dmSize = sizeof(DEVMODEW); src1.current.dmPelsWidth = 1920; src1.current.dmPelsHeight = 1080;
    src1.current.dmBitsPerPel = 32; src1.current.dmDisplayFrequency = 144;
    src1.current.dmFields = DM_POSITION | DM_PELSWIDTH | DM_PELSHEIGHT | DM_BITSPERPEL | DM_DISPLAYFREQUENCY;
    src1.current.dmPosition.x = 100; src1.current.dmPosition.y = 50;
    memset( &mon_src, 0, sizeof(mon_src) ); mon_src.source = &src1; mon_src.output_id = 5;
    memset( &mon_other, 0, sizeof(mon_other) );          /* a sourceless monitor that is NOT the virtual one */
    if (regime == 0) list_add_tail( &monitors, &virtual_monitor.entry );
    if (regime == 1) { list_add_tail( &monitors, &mon_src.entry ); }
    if (regime == 2) { list_add_tail( &monitors, &mon_other.entry ); }
    if (regime == 3) { list_add_tail( &monitors, &mon_src.entry ); }
}
static void dump( const char *tag, LONG rc, UINT32 np, UINT32 nm, const unsigned char *paths, const unsigned char *modes, size_t pn, size_t mn )
{
    size_t i;
    printf( "%s rc=%ld np=%u nm=%u paths=", tag, (long)rc, np, nm );
    for (i = 0; i < pn; i++) printf( "%02x", paths[i] );
    printf( " modes=" );
    for (i = 0; i < mn; i++) printf( "%02x", modes[i] );
    printf( "\n" );
}
int main( int argc, char **argv )
{
    static DISPLAYCONFIG_PATH_INFO paths[4]; static DISPLAYCONFIG_MODE_INFO modes[8];
    UINT32 np, nm; LONG rc; DISPLAYCONFIG_TOPOLOGY_ID topo;
    int regime, sc;
    (void)argc; (void)argv;
    for (regime = 0; regime < 4; regime++)
    for (sc = 0; sc < 6; sc++)
    {
        UINT32 flags = QDC_ONLY_ACTIVE_PATHS, sp, sm, room_p = 4, room_m = 8;
        DISPLAYCONFIG_TOPOLOGY_ID *tp = NULL;
        char tag[64];
        reset( regime );
        if (sc == 1) flags |= QDC_VIRTUAL_MODE_AWARE;
        if (sc == 2) { room_p = 1; room_m = 1; }
        if (sc == 3) { room_p = 1; room_m = 2; }
        if (sc == 4) { flags = QDC_DATABASE_CURRENT; tp = &topo; }
        if (sc == 5) lock_ok = 0;
        memset( paths, 0xaa, sizeof(paths) ); memset( modes, 0xaa, sizeof(modes) );
        rc = NtUserGetDisplayConfigBufferSizes( flags & ~0u, &sp, &sm );
        np = room_p; nm = room_m;
        rc = NtUserQueryDisplayConfig( flags, &np, paths, &nm, modes, tp );
        snprintf( tag, sizeof(tag), "r%d s%d sizes=%u/%u", regime, sc, sp, sm );
        dump( tag, rc, np, nm, (unsigned char *)paths, (unsigned char *)modes, sizeof(DISPLAYCONFIG_PATH_INFO) * 2, sizeof(DISPLAYCONFIG_MODE_INFO) * 3 );
        { char *q; for (q = logbuf; *q; q++) if (*q == '\n') *q = '|'; }
        printf( "LOG %s\n", logbuf );
    }
    /* the shape of the virtual path (key on only) */
    reset( 0 );
    {
        DISPLAYCONFIG_SOURCE_MODE *sm0; DISPLAYCONFIG_TARGET_MODE *tm0;
        memset( paths, 0xaa, sizeof(paths) ); memset( modes, 0xaa, sizeof(modes) );
        np = 4; nm = 8;
        rc = NtUserQueryDisplayConfig( QDC_ONLY_ACTIVE_PATHS, &np, paths, &nm, modes, NULL );
        sm0 = &modes[0].sourceMode; tm0 = &modes[1].targetMode;
        printf( "SHAPE rc=%ld np=%u nm=%u pflags=%x src=%u srcmode=%u tgt=%u tgtmode=%u adapter=%x:%x modeinfo=%d/%d size=%ux%u pos=%d,%d fmt=%d tgtsize=%ux%u rate=%u/%u act=%x\n",
                (long)rc, np, nm, (unsigned)paths[0].flags, (unsigned)paths[0].sourceInfo.id, (unsigned)paths[0].sourceInfo.modeInfoIdx,
                (unsigned)paths[0].targetInfo.id, (unsigned)paths[0].targetInfo.modeInfoIdx,
                (unsigned)paths[0].sourceInfo.adapterId.HighPart, (unsigned)paths[0].sourceInfo.adapterId.LowPart,
                (int)modes[0].infoType, (int)modes[1].infoType, (unsigned)sm0->width, (unsigned)sm0->height,
                (int)sm0->position.x, (int)sm0->position.y, (int)sm0->pixelFormat,
                (unsigned)tm0->targetVideoSignalInfo.activeSize.cx, (unsigned)tm0->targetVideoSignalInfo.activeSize.cy,
                (unsigned)paths[0].targetInfo.refreshRate.Numerator, (unsigned)paths[0].targetInfo.refreshRate.Denominator,
                (unsigned)paths[0].flags );
        printf( "LUID %x:%x\n", (unsigned)paths[0].sourceInfo.adapterId.HighPart, (unsigned)paths[0].sourceInfo.adapterId.LowPart );
        /* a second call keeps the same LUID; a mode change moves the size */
        np = 4; nm = 8; NtUserQueryDisplayConfig( QDC_ONLY_ACTIVE_PATHS, &np, paths, &nm, modes, NULL );
        printf( "LUID2 %x:%x\n", (unsigned)paths[0].sourceInfo.adapterId.HighPart, (unsigned)paths[0].sourceInfo.adapterId.LowPart );
        ios_screen_cur_w = 800; ios_screen_cur_h = 600;
        np = 4; nm = 8; NtUserQueryDisplayConfig( QDC_ONLY_ACTIVE_PATHS, &np, paths, &nm, modes, NULL );
        printf( "MODECHANGE %ux%u\n", (unsigned)modes[0].sourceMode.width, (unsigned)modes[0].sourceMode.height );
    }
    return 0;
}
"""


wine_inc = None
for cand in (os.environ.get("WINE_SRC"), str(root / "wine"), str(Path.home() / "madeira-bcd/wine")):
    if cand and (Path(cand) / "include/windef.h").exists():
        wine_inc = Path(cand)
        break
if wine_inc:
    incs = ["-I" + str(wine_inc / "include")] + (["-I" + str(wine_inc / "build-macos/include")] if (wine_inc / "build-macos/include").exists() else [])
    results = {}
    with tempfile.TemporaryDirectory() as t:
        for name, text in (("after", func(src, "LONG WINAPI NtUserQueryDisplayConfig(")), ("before", QDC_BEFORE)):
            c = Path(t) / (name + ".c")
            c.write_text(host_harness(text))
            exe = Path(t) / name
            r = subprocess.run(["cc", "-std=gnu11", "-w", "-fsanitize=address,undefined", *incs, str(c), "-o", str(exe)],
                               capture_output=True, text=True)
            check("the production display-config code compiles on the host (%s)" % name, r.returncode == 0, r.stderr[-1500:])
            if r.returncode:
                break
            for key in (None, "1"):
                env = {k: v for k, v in os.environ.items() if k not in ("MADEIRA_VDCFG_PATH", "MADEIRA_SCREEN_W", "MADEIRA_SCREEN_H")}
                env.update({"MADEIRA_SCREEN_W": "1408", "MADEIRA_SCREEN_H": "648", "ASAN_OPTIONS": "symbolize=0"})
                if key:
                    env["MADEIRA_VDCFG_PATH"] = key
                p = subprocess.run([str(exe)], capture_output=True, text=True, env=env)
                check("%s, key %s: runs clean under ASan/UBSan" % (name, key or "off"), p.returncode == 0, (p.stdout + p.stderr)[-800:])
                results[(name, key)] = p.stdout
    if ("after", None) in results and ("before", None) in results:
        a = [l for l in results[("after", None)].splitlines() if not l.startswith(("LOG", "SHAPE", "LUID", "MODECHANGE"))]
        b = [l for l in results[("before", None)].splitlines() if not l.startswith(("LOG", "SHAPE", "LUID", "MODECHANGE"))]
        check("key off: every answer of the new QueryDisplayConfig is byte-identical to build 578's (%d scenarios)" % len(a), a == b and len(a) == 24,
              "\n".join(x for x, y in zip(a, b) if x != y)[:600])
        virt0 = next(l for l in a if l.startswith("r0 s0 "))
        check("key off, virtual monitor: sizes say 1 path 2 modes, the query answers success with 0 paths 0 modes and writes nothing",
              "sizes=1/2 rc=0 np=0 nm=0 paths=" + "aa" * 144 in virt0 and "modes=" + "aa" * 192 in virt0, virt0[:200])
        logs = [l for l in results[("after", None)].splitlines() if l.startswith("LOG")]
        check("key off: the mismatch line names both answers and is written twice per process at most",
              "[vdcfg-mismatch]" in logs[0] and "1 monitor(s) without a source skipped" in logs[0]
              and "said 1 paths 2 modes" in logs[0] and "room for 4 paths 8 modes offered" in logs[0]
              and sum(l.count("[vdcfg-mismatch]") for l in logs) == 2, logs[0][:300])
        check("key off: a monitor with a source (a real path) writes no mismatch line",
              all("[vdcfg-mismatch]" not in l for l in logs[6:12]))
    if ("after", "1") in results and ("before", None) in results:
        on = results[("after", "1")].splitlines()
        shape = next(l for l in on if l.startswith("SHAPE"))
        check("key on, virtual monitor: success, 1 path, 2 modes (as the sizes call says), source mode then target mode",
              " rc=0 np=1 nm=2 " in shape and "modeinfo=1/2" in shape, shape)
        check("key on: path active, source id 0, source mode 0, target id 0 (the monitor's output), target mode 1, 60 Hz",
              "pflags=1 src=0 srcmode=0 tgt=0 tgtmode=1" in shape and "rate=60/1" in shape, shape)
        check("key on: the source mode is the desktop's current size at 0,0, 32 bpp (the session's MADEIRA_SCREEN_W/H)",
              "size=1408x648 pos=0,0 fmt=4 tgtsize=1408x648" in shape, shape)
        luids = [l for l in on if l.startswith("LUID")]
        check("key on: the adapter LUID is the helper's, stable across calls (software LUID here)",
              luids == ["LUID 0:1234", "LUID2 0:1234"], str(luids))
        check("key on: a program's later mode change moves the source mode", any(l == "MODECHANGE 800x600" for l in on))
        b_on = [l for l in on if l.startswith("r") and " sizes=" in l]
        # regimes with a real source or a non-virtual sourceless monitor answer as before with the key on
        b_off = [l for l in results[("after", None)].splitlines() if l.startswith("r") and " sizes=" in l]
        same = [x for x, y in zip(b_on, b_off) if x.split()[0] in ("r1", "r2", "r3") and x == y]
        check("key on: a monitor with a source and a non-virtual sourceless monitor are answered as with the key off",
              len(same) == 18, str(len(same)))
        r0 = [l for l in b_on if l.startswith("r0 s0 ")][0]
        check("key on, the one scenario that matters: sizes 1/2 and the query now gives 1 path 2 modes", "sizes=1/2 rc=0 np=1 nm=2" in r0, r0[:120])
        logs_on = [l for l in on if l.startswith("LOG")]
        check("key on: the virtual monitor's path logs no mismatch line", all("[vdcfg-mismatch]" not in l for l in logs_on[:6]))
else:
    print("skip the host run of the production code (no Wine headers at $WINE_SRC / ./wine / ~/madeira-bcd/wine)")

# ------------------------------------------------------------------ 3. catalog and rule
catalog = (root / "build/tools/gen-config-catalog.py").read_text()
check("the config catalog lists env.MADEIRA_VDCFG_PATH, off", '"env.MADEIRA_VDCFG_PATH"' in catalog
      and re.search(r'"env\.MADEIRA_VDCFG_PATH":\s*\{[^}]*"default": "0"', catalog, re.S) is not None)
gen = (root / "app/Madeira/ConfigCatalog.generated.swift").read_text()
check("the generated catalog has the key (one line, added by hand; regenerate in the full tree)",
      'key: "env.MADEIRA_VDCFG_PATH"' in gen)
sys.path.insert(0, str(root / "build/tools"))
import importlib.util
spec = importlib.util.spec_from_file_location("autotune_rules", root / "build/tools/autotune_rules.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
rule = next((r for r in mod.RULES if r["id"] == "vdcfg-virtual-path"), None)
check("the auto-tune rule exists and adds only that line", rule is not None and rule["lines"] == ["env.MADEIRA_VDCFG_PATH = 1"])
if rule:
    text = repr(rule["all"])
    check("the rule needs Trails' fault (NULL at 0x10, registers in sora_deploy_steam.exe) and the 0-path answer, in a Dock session after a crash",
          "NULL-class fault addr=0x10" in text and "sora_deploy_steam.exe+" in text and "-> 0, 0 paths" in text
          and rule.get("session") == "dock" and rule.get("outcomes") == ["crashed"])
    check("the rule is not a fixedIn rule (it is an attempt)", "fixedIn" not in rule)

# ------------------------------------------------------------------ 4. the whole file still compiles (optional)
wine = None
for cand in (os.environ.get("WINE_SRC"), str(root / "wine"), str(Path.home() / "madeira-bcd/wine")):
    if cand and (Path(cand) / "build-macos/include/windef.h").exists() or cand and (Path(cand) / "include/windef.h").exists():
        wine = Path(cand)
        break
sdk = subprocess.run(["xcrun", "--sdk", "iphoneos", "--show-sdk-path"], capture_output=True, text=True)
if wine and sdk.returncode == 0:
    wb = wine / "build-macos" if (wine / "build-macos/include").exists() else wine
    u = root / "build/win32u-unix"
    sh = root / "build/ntdll-unix/shims"
    cmd = ["xcrun", "-sdk", "iphoneos", "clang", "-arch", "arm64", "-isysroot", sdk.stdout.strip(), "-miphoneos-version-min=17.0",
           "-O2", "-fno-strict-aliasing", "-Wall", "-Wno-int-conversion", "-Werror=implicit-function-declaration",
           "-include", str(u / "config_ios.h"), "-include", str(sh / "wine_ios_exit.h"), "-I" + str(u), "-I" + str(wb / "include"),
           "-I" + str(sh), "-I" + str(wb / "dlls/win32u"), "-I" + str(wine / "dlls/win32u"), "-I" + str(wine / "include"),
           "-D__WINESRC__", "-D_WIN32U_", "-D_ACRTIMP=", "-DWINBASEAPI=", '-DSYSTEMDLLPATH=""', "-DWINE_UNIX_LIB", "-DWINE_IOS=1",
           "-D__wine_unix_lib_init=win32u_unix_lib_init", "-USONAME_LIBFREETYPE", "-USONAME_LIBFONTCONFIG", "-USONAME_LIBEGL",
           "-USONAME_LIBVULKAN", "-USONAME_LIBGNUTLS", "-UHAVE_FT2BUILD_H", "-fsyntax-only", str(u / "sysparams_ios.c")]
    r = subprocess.run(cmd, capture_output=True, text=True)
    mine = [l for l in r.stderr.splitlines() if re.search(r"vdcfg|virtual_luid|ios_virtual_adapter|error:", l)]
    check("sysparams_ios.c compiles for the iPhone SDK with no error and no warning about the new code (no implicit declarations)",
          r.returncode == 0 and not mine, "\n".join(mine[:6]))
else:
    print("skip the whole-file compile (no Wine headers at $WINE_SRC / ./wine / ~/madeira-bcd/wine, or no iPhone SDK)")

print("check-vdcfg-path: " + ("PASS" if ok else "FAIL"))
sys.exit(0 if ok else 1)

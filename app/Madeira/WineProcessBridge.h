#pragma once
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

// Start Wine process initialization on a background thread.
// Must be called AFTER wineserver is running.
// prefix_path: path to the Wine prefix directory
// Returns 0 on success, -1 on error.
int wine_process_start(const char *prefix_path);

// Check if Wine process is running
int wine_process_is_running(void);

// Session exit report (the library front end). ntdll calls
// wine_launched_process_did_exit() when the program the app launched (the
// session's initial process) exits; other processes are not reported.
// Returns 1 and fills *status when that program ended with an NTSTATUS error
// (0xC...) since the last reset.
void wine_launched_process_did_exit(int status);
int wine_crash_exit_status(uint32_t *status);
// Forget the recorded status; called when a session begins.
void wine_exit_status_reset(void);

// Session end (madeira-bcd, round 577; WineProcessBridge.m "Session end"). The session ends when __wine_main
// returns on the boot thread. A program that ends on another thread never gets it back there.
// wine_launched_exit_stuck: 1 when the program (the session's initial process) ended on a thread other than
// the boot thread and __wine_main has not returned; *code is its exit status, *seconds how long ago.
// wine_session_abandon: the end-of-session work the boot thread would have done (g_wine_running = 0,
// the wineserver stopped). Blocks until the wineserver thread is joined: call it off the main queue.
int wine_launched_exit_stuck(int32_t *code, double *seconds);
void wine_session_abandon(void);
// Processes the program started that still run, not counting Wine's own (explorer, services, winedevice,
// conhost, plugplay, rpcss, svchost, the Dock host) or crash reporters and helpers (process_ios.c). Their
// file names go to names. A child that could not be tracked counts as running.
int madeira_live_user_processes(char *names, int len);

// Steam S0 net-test VPN gate: write C:\madeira-continue.flag into the
// prefix's drive_c so the paused winhttp-test.exe resumes to the Steam
// stage. Called by the "Continue Net Test" UI button after the user has
// detached the JIT debugger and switched VPNs. Returns 0 on success.
int madeira_write_continue_flag(void);

// Extract the bundled prefix template if the prefix is new (idempotent).
// Madeira Dock's component setup calls it before writing Steam's registry keys.
void madeira_seed_prefix_if_needed(const char *prefix_path);

#ifdef __cplusplus
}
#endif

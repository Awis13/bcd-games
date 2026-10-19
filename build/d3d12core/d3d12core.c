/*
 * d3d12core.dll for Madeira (arm64ec).
 *
 * Since the Agility SDK, Windows' d3d12.dll is a thin loader and D3D12Core.dll
 * holds the runtime and exports D3D12SDKVersion. vkd3d-proton split itself the
 * same way in 2.9 because games started assuming that layout, and some check
 * D3D12Core's D3D12SDKVersion (vkd3d-proton issues #2240). In a working Proton
 * run of GTA V Enhanced the game loads system32\D3D12.DLL and then
 * system32\d3d12core.dll before it creates its device (owner's PROTONLOG,
 * 2026-10-02); here it stops with ERR_GFX_D3D_NOD3D12 after destroying a
 * device it created fine.
 *
 * Nothing but the version lives here: every function forwards to d3d12.dll
 * (d3d12core.def). madeira-d3d12's d3d12.dll loads this DLL at process attach
 * only with bcd.cfg d3d12-core-dll set, and a value above 1 replaces the
 * version (d3d12-core-dll = 614 -> D3D12SDKVersion 614).
 *
 * Built by tools/build-d3d12core-dll.sh.
 */
#include <windows.h>

/* Agility SDK 1.618 (vkd3d-proton's current value); writable on purpose. */
UINT D3D12SDKVersion = 618;

BOOL WINAPI DllMain(HINSTANCE inst, DWORD reason, LPVOID reserved)
{
    (void)reserved;
    if (reason == DLL_PROCESS_ATTACH) DisableThreadLibraryCalls(inst);
    return TRUE;
}

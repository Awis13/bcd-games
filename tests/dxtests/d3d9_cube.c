// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
//
// BCD's Direct3D 9 test: a spinning textured cube, drawn with the fixed-function pipeline (a texture
// modulated by the vertex colour, one world, view and projection matrix, a depth buffer), so it needs no shader
// compiler and no shader bytecode. x86-64 Windows; see dxtest.h. (tests/x86/d3d9-cube-x86.c is the 32-bit test of
// the WoW64 path; this is the 64-bit program a game's Direct3D 9 path looks like.)

#define COBJMACROS
#include "dxtest.h"
#include <d3d9.h>

#define API "Direct3D 9"

typedef IDirect3D9 *(WINAPI *create_fn)(UINT);

struct vertex9 { float x, y, z; DWORD diffuse; float u, v; };
#define FVF9 (D3DFVF_XYZ | D3DFVF_DIFFUSE | D3DFVF_TEX1)

#define CHECK(call, what) do { HRESULT hr_ = (call); if (FAILED(hr_)) { \
        char m_[200]; snprintf(m_, sizeof m_, "%s failed (%#lx)", what, (unsigned long)hr_); dxt_fail(API, m_); return 1; } } while (0)

int WINAPI WinMain(HINSTANCE inst, HINSTANCE prev, LPSTR cmd, int show)
{
    HMODULE dll;
    create_fn create;
    HWND hwnd;
    IDirect3D9 *d3d;
    IDirect3DDevice9 *dev = NULL;
    IDirect3DVertexBuffer9 *vb = NULL;
    IDirect3DIndexBuffer9 *ib = NULL;
    IDirect3DTexture9 *tex = NULL;
    D3DPRESENT_PARAMETERS pp;
    D3DADAPTER_IDENTIFIER9 id;
    dxt_vertex cube[24];
    struct vertex9 verts[24];
    unsigned short idx[36];
    dxt_clock clock;
    unsigned failures = 0;
    HRESULT hr;
    void *p;
    int i;

    (void)inst; (void)prev; (void)cmd; (void)show;
    dxt_log(API ": start, %dx%d", DXT_W, DXT_H);
    dll = LoadLibraryA("d3d9.dll");
    create = dll ? (create_fn)GetProcAddress(dll, "Direct3DCreate9") : NULL;
    if (!create) { dxt_fail(API, "d3d9.dll (Direct3DCreate9) is not available"); return 1; }
    hwnd = dxt_window("BCD DirectX 9 test");
    if (!hwnd) { dxt_fail(API, "the window could not be created"); return 1; }
    d3d = create(D3D_SDK_VERSION);
    if (!d3d) { dxt_fail(API, "Direct3DCreate9 returned no interface"); return 1; }
    memset(&id, 0, sizeof id);
    if (SUCCEEDED(IDirect3D9_GetAdapterIdentifier(d3d, D3DADAPTER_DEFAULT, 0, &id)))
        dxt_log(API ": adapter \"%s\", driver \"%s\"", id.Description, id.Driver);

    memset(&pp, 0, sizeof pp);
    pp.BackBufferWidth = DXT_W;
    pp.BackBufferHeight = DXT_H;
    pp.BackBufferFormat = D3DFMT_X8R8G8B8;
    pp.BackBufferCount = 1;
    pp.SwapEffect = D3DSWAPEFFECT_DISCARD;
    pp.hDeviceWindow = hwnd;
    pp.Windowed = TRUE;
    pp.EnableAutoDepthStencil = TRUE;
    pp.AutoDepthStencilFormat = D3DFMT_D24S8;
    pp.PresentationInterval = D3DPRESENT_INTERVAL_ONE;
    hr = IDirect3D9_CreateDevice(d3d, D3DADAPTER_DEFAULT, D3DDEVTYPE_HAL, hwnd, D3DCREATE_HARDWARE_VERTEXPROCESSING, &pp, &dev);
    if (FAILED(hr)) {
        dxt_log(API ": CreateDevice with hardware vertex processing returned %#lx; trying software", (unsigned long)hr);
        hr = IDirect3D9_CreateDevice(d3d, D3DADAPTER_DEFAULT, D3DDEVTYPE_HAL, hwnd, D3DCREATE_SOFTWARE_VERTEXPROCESSING, &pp, &dev);
    }
    CHECK(hr, "CreateDevice");

    /* the cube, as fixed-function vertices (position, diffuse colour, one texture coordinate pair) */
    dxt_cube(cube, idx);
    for (i = 0; i < 24; i++) {
        verts[i].x = cube[i].x; verts[i].y = cube[i].y; verts[i].z = cube[i].z;
        verts[i].diffuse = D3DCOLOR_ARGB(cube[i].a, cube[i].r, cube[i].g, cube[i].b);
        verts[i].u = cube[i].u; verts[i].v = cube[i].v;
    }
    CHECK(IDirect3DDevice9_CreateVertexBuffer(dev, sizeof verts, D3DUSAGE_WRITEONLY, FVF9, D3DPOOL_MANAGED, &vb, NULL), "CreateVertexBuffer");
    CHECK(IDirect3DVertexBuffer9_Lock(vb, 0, 0, &p, 0), "Lock (vertices)");
    memcpy(p, verts, sizeof verts);
    IDirect3DVertexBuffer9_Unlock(vb);
    CHECK(IDirect3DDevice9_CreateIndexBuffer(dev, sizeof idx, D3DUSAGE_WRITEONLY, D3DFMT_INDEX16, D3DPOOL_MANAGED, &ib, NULL), "CreateIndexBuffer");
    CHECK(IDirect3DIndexBuffer9_Lock(ib, 0, 0, &p, 0), "Lock (indices)");
    memcpy(p, idx, sizeof idx);
    IDirect3DIndexBuffer9_Unlock(ib);

    /* the texture, filled through LockRect */
    CHECK(IDirect3DDevice9_CreateTexture(dev, DXT_TEX, DXT_TEX, 1, 0, D3DFMT_A8R8G8B8, D3DPOOL_MANAGED, &tex, NULL), "CreateTexture");
    {
        D3DLOCKED_RECT lr;
        unsigned texels[DXT_TEX * DXT_TEX];
        int y;
        dxt_checker(texels);
        CHECK(IDirect3DTexture9_LockRect(tex, 0, &lr, NULL, 0), "LockRect");
        for (y = 0; y < DXT_TEX; y++) memcpy((BYTE *)lr.pBits + y * lr.Pitch, texels + y * DXT_TEX, DXT_TEX * 4);
        IDirect3DTexture9_UnlockRect(tex, 0);
    }

    IDirect3DDevice9_SetRenderState(dev, D3DRS_LIGHTING, FALSE);
    IDirect3DDevice9_SetRenderState(dev, D3DRS_ZENABLE, D3DZB_TRUE);
    IDirect3DDevice9_SetRenderState(dev, D3DRS_CULLMODE, D3DCULL_NONE);
    IDirect3DDevice9_SetTexture(dev, 0, (IDirect3DBaseTexture9 *)tex);
    IDirect3DDevice9_SetTextureStageState(dev, 0, D3DTSS_COLOROP, D3DTOP_MODULATE);
    IDirect3DDevice9_SetTextureStageState(dev, 0, D3DTSS_COLORARG1, D3DTA_TEXTURE);
    IDirect3DDevice9_SetTextureStageState(dev, 0, D3DTSS_COLORARG2, D3DTA_DIFFUSE);
    IDirect3DDevice9_SetSamplerState(dev, 0, D3DSAMP_MINFILTER, D3DTEXF_LINEAR);
    IDirect3DDevice9_SetSamplerState(dev, 0, D3DSAMP_MAGFILTER, D3DTEXF_LINEAR);
    IDirect3DDevice9_SetFVF(dev, FVF9);
    IDirect3DDevice9_SetStreamSource(dev, 0, vb, 0, sizeof(struct vertex9));
    IDirect3DDevice9_SetIndices(dev, ib);
    {
        dxt_mat view = dxt_view(), proj = dxt_proj();
        IDirect3DDevice9_SetTransform(dev, D3DTS_VIEW, (const D3DMATRIX *)&view);
        IDirect3DDevice9_SetTransform(dev, D3DTS_PROJECTION, (const D3DMATRIX *)&proj);
    }

    dxt_clock_start(&clock, hwnd, "BCD DirectX 9 test");
    dxt_log(API ": drawing");
    while (dxt_pump()) {
        dxt_mat world = dxt_world(dxt_seconds(&clock));
        IDirect3DDevice9_Clear(dev, 0, NULL, D3DCLEAR_TARGET | D3DCLEAR_ZBUFFER, D3DCOLOR_XRGB(15, 18, 26), 1.0f, 0);
        if (SUCCEEDED(IDirect3DDevice9_BeginScene(dev))) {
            IDirect3DDevice9_SetTransform(dev, D3DTS_WORLD, (const D3DMATRIX *)&world);
            IDirect3DDevice9_DrawIndexedPrimitive(dev, D3DPT_TRIANGLELIST, 0, 0, 24, 0, 12);
            IDirect3DDevice9_EndScene(dev);
        }
        hr = IDirect3DDevice9_Present(dev, NULL, NULL, NULL, NULL);
        if (FAILED(hr)) {
            if (failures++ < 3) dxt_log(API ": Present returned %#lx", (unsigned long)hr);
            Sleep(16);   /* a lost device (minimised, say) is not a reason to spin */
        }
        dxt_clock_frame(&clock);
    }
    dxt_log(API ": the window was closed after %u frames", clock.total);

    IDirect3DTexture9_Release(tex);
    IDirect3DIndexBuffer9_Release(ib);
    IDirect3DVertexBuffer9_Release(vb);
    IDirect3DDevice9_Release(dev);
    IDirect3D9_Release(d3d);
    return 0;
}

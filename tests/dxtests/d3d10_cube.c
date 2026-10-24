// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
//
// BCD's Direct3D 10 test: a spinning textured cube, drawn with a vertex and a pixel shader of Shader Model 4.0
// (cube_shaders.h, from cube.hlsl) through D3D10CreateDeviceAndSwapChain, as a game would. x86-64 Windows; see dxtest.h.

#define COBJMACROS
#include "dxtest.h"
#include <d3d10.h>
#include "cube_shaders.h"

#define API "Direct3D 10"

typedef HRESULT (WINAPI *create_fn)(IDXGIAdapter *, D3D10_DRIVER_TYPE, HMODULE, UINT, UINT, DXGI_SWAP_CHAIN_DESC *,
                                    IDXGISwapChain **, ID3D10Device **);

#define CHECK(call, what) do { HRESULT hr_ = (call); if (FAILED(hr_)) { \
        char m_[200]; snprintf(m_, sizeof m_, "%s failed (%#lx)", what, (unsigned long)hr_); dxt_fail(API, m_); return 1; } } while (0)

int WINAPI WinMain(HINSTANCE inst, HINSTANCE prev, LPSTR cmd, int show)
{
    static const D3D10_INPUT_ELEMENT_DESC layout[] = {
        { "POSITION", 0, DXGI_FORMAT_R32G32B32_FLOAT, 0, 0, D3D10_INPUT_PER_VERTEX_DATA, 0 },
        { "TEXCOORD", 0, DXGI_FORMAT_R32G32_FLOAT, 0, 12, D3D10_INPUT_PER_VERTEX_DATA, 0 },
        { "COLOR", 0, DXGI_FORMAT_R8G8B8A8_UNORM, 0, 20, D3D10_INPUT_PER_VERTEX_DATA, 0 },
    };
    HMODULE dll;
    create_fn create;
    HWND hwnd;
    DXGI_SWAP_CHAIN_DESC sd;
    IDXGISwapChain *swap = NULL;
    ID3D10Device *dev = NULL;
    ID3D10Texture2D *back = NULL, *depth = NULL, *tex = NULL;
    ID3D10RenderTargetView *rtv = NULL;
    ID3D10DepthStencilView *dsv = NULL;
    ID3D10ShaderResourceView *srv = NULL;
    ID3D10SamplerState *sampler = NULL;
    ID3D10RasterizerState *raster = NULL;
    ID3D10DepthStencilState *depthstate = NULL;
    ID3D10VertexShader *vs = NULL;
    ID3D10PixelShader *ps = NULL;
    ID3D10InputLayout *il = NULL;
    ID3D10Buffer *vb = NULL, *ib = NULL, *cb = NULL;
    dxt_vertex verts[24];
    unsigned short idx[36];
    unsigned texels[DXT_TEX * DXT_TEX];
    dxt_clock clock;
    D3D10_BUFFER_DESC bd;
    D3D10_SUBRESOURCE_DATA init;
    D3D10_TEXTURE2D_DESC td;
    D3D10_SAMPLER_DESC smd;
    D3D10_RASTERIZER_DESC rd;
    D3D10_DEPTH_STENCIL_DESC dd;
    D3D10_VIEWPORT vp;
    UINT stride = sizeof(dxt_vertex), offset = 0;
    DXGI_ADAPTER_DESC ad;
    IDXGIDevice *dxgidev = NULL;
    IDXGIAdapter *adapter = NULL;

    (void)inst; (void)prev; (void)cmd; (void)show;
    dxt_log(API ": start, %dx%d", DXT_W, DXT_H);
    dll = LoadLibraryA("d3d10.dll");
    create = dll ? (create_fn)GetProcAddress(dll, "D3D10CreateDeviceAndSwapChain") : NULL;
    if (!create) { dxt_fail(API, "d3d10.dll (D3D10CreateDeviceAndSwapChain) is not available in this build"); return 1; }
    hwnd = dxt_window("BCD DirectX 10 test");
    if (!hwnd) { dxt_fail(API, "the window could not be created"); return 1; }

    memset(&sd, 0, sizeof sd);
    sd.BufferDesc.Width = DXT_W;
    sd.BufferDesc.Height = DXT_H;
    sd.BufferDesc.Format = DXGI_FORMAT_B8G8R8A8_UNORM;
    sd.SampleDesc.Count = 1;
    sd.BufferUsage = DXGI_USAGE_RENDER_TARGET_OUTPUT;
    sd.BufferCount = 1;
    sd.OutputWindow = hwnd;
    sd.Windowed = TRUE;
    sd.SwapEffect = DXGI_SWAP_EFFECT_DISCARD;
    CHECK(create(NULL, D3D10_DRIVER_TYPE_HARDWARE, NULL, 0, D3D10_SDK_VERSION, &sd, &swap, &dev), "D3D10CreateDeviceAndSwapChain");
    if (SUCCEEDED(ID3D10Device_QueryInterface(dev, &IID_IDXGIDevice, (void **)&dxgidev)) &&
        SUCCEEDED(IDXGIDevice_GetAdapter(dxgidev, &adapter)) && SUCCEEDED(IDXGIAdapter_GetDesc(adapter, &ad))) {
        char name[128];
        int i;
        for (i = 0; i < 127 && ad.Description[i]; i++) name[i] = ad.Description[i] < 0x80 ? (char)ad.Description[i] : '?';
        name[i] = 0;
        dxt_log(API ": adapter \"%s\"", name);
    }
    if (adapter) IDXGIAdapter_Release(adapter);
    if (dxgidev) IDXGIDevice_Release(dxgidev);

    /* the back buffer's view and a depth buffer */
    CHECK(IDXGISwapChain_GetBuffer(swap, 0, &IID_ID3D10Texture2D, (void **)&back), "GetBuffer");
    CHECK(ID3D10Device_CreateRenderTargetView(dev, (ID3D10Resource *)back, NULL, &rtv), "CreateRenderTargetView");
    memset(&td, 0, sizeof td);
    td.Width = DXT_W; td.Height = DXT_H; td.MipLevels = 1; td.ArraySize = 1;
    td.Format = DXGI_FORMAT_D24_UNORM_S8_UINT;
    td.SampleDesc.Count = 1;
    td.Usage = D3D10_USAGE_DEFAULT;
    td.BindFlags = D3D10_BIND_DEPTH_STENCIL;
    CHECK(ID3D10Device_CreateTexture2D(dev, &td, NULL, &depth), "CreateTexture2D (depth)");
    CHECK(ID3D10Device_CreateDepthStencilView(dev, (ID3D10Resource *)depth, NULL, &dsv), "CreateDepthStencilView");

    /* shaders and their input layout */
    CHECK(ID3D10Device_CreateVertexShader(dev, cube_vs_4_0, sizeof cube_vs_4_0, &vs), "CreateVertexShader");
    CHECK(ID3D10Device_CreatePixelShader(dev, cube_ps_4_0, sizeof cube_ps_4_0, &ps), "CreatePixelShader");
    CHECK(ID3D10Device_CreateInputLayout(dev, layout, 3, cube_vs_4_0, sizeof cube_vs_4_0, &il), "CreateInputLayout");

    /* the cube's buffers */
    dxt_cube(verts, idx);
    memset(&bd, 0, sizeof bd);
    memset(&init, 0, sizeof init);
    bd.ByteWidth = sizeof verts; bd.Usage = D3D10_USAGE_IMMUTABLE; bd.BindFlags = D3D10_BIND_VERTEX_BUFFER;
    init.pSysMem = verts;
    CHECK(ID3D10Device_CreateBuffer(dev, &bd, &init, &vb), "CreateBuffer (vertices)");
    bd.ByteWidth = sizeof idx; bd.BindFlags = D3D10_BIND_INDEX_BUFFER;
    init.pSysMem = idx;
    CHECK(ID3D10Device_CreateBuffer(dev, &bd, &init, &ib), "CreateBuffer (indices)");
    bd.ByteWidth = sizeof(dxt_mat); bd.Usage = D3D10_USAGE_DYNAMIC; bd.BindFlags = D3D10_BIND_CONSTANT_BUFFER;
    bd.CPUAccessFlags = D3D10_CPU_ACCESS_WRITE;
    CHECK(ID3D10Device_CreateBuffer(dev, &bd, NULL, &cb), "CreateBuffer (constants)");

    /* the texture, its view and a sampler */
    dxt_checker(texels);
    memset(&td, 0, sizeof td);
    td.Width = DXT_TEX; td.Height = DXT_TEX; td.MipLevels = 1; td.ArraySize = 1;
    td.Format = DXGI_FORMAT_R8G8B8A8_UNORM;
    td.SampleDesc.Count = 1;
    td.Usage = D3D10_USAGE_IMMUTABLE;
    td.BindFlags = D3D10_BIND_SHADER_RESOURCE;
    memset(&init, 0, sizeof init);
    init.pSysMem = texels;
    init.SysMemPitch = DXT_TEX * 4;
    CHECK(ID3D10Device_CreateTexture2D(dev, &td, &init, &tex), "CreateTexture2D (checker)");
    CHECK(ID3D10Device_CreateShaderResourceView(dev, (ID3D10Resource *)tex, NULL, &srv), "CreateShaderResourceView");
    memset(&smd, 0, sizeof smd);
    smd.Filter = D3D10_FILTER_MIN_MAG_MIP_LINEAR;
    smd.AddressU = smd.AddressV = smd.AddressW = D3D10_TEXTURE_ADDRESS_WRAP;
    smd.MaxAnisotropy = 1;
    smd.ComparisonFunc = D3D10_COMPARISON_NEVER;
    smd.MaxLOD = D3D10_FLOAT32_MAX;
    CHECK(ID3D10Device_CreateSamplerState(dev, &smd, &sampler), "CreateSamplerState");

    /* no culling, depth test on */
    memset(&rd, 0, sizeof rd);
    rd.FillMode = D3D10_FILL_SOLID; rd.CullMode = D3D10_CULL_NONE; rd.DepthClipEnable = TRUE;
    CHECK(ID3D10Device_CreateRasterizerState(dev, &rd, &raster), "CreateRasterizerState");
    memset(&dd, 0, sizeof dd);
    dd.DepthEnable = TRUE; dd.DepthWriteMask = D3D10_DEPTH_WRITE_MASK_ALL; dd.DepthFunc = D3D10_COMPARISON_LESS;
    dd.StencilReadMask = dd.StencilWriteMask = 0xFF;   /* stencil off, with the ordinary operations spelled out */
    dd.FrontFace.StencilFailOp = dd.FrontFace.StencilDepthFailOp = dd.FrontFace.StencilPassOp = D3D10_STENCIL_OP_KEEP;
    dd.FrontFace.StencilFunc = D3D10_COMPARISON_ALWAYS;
    dd.BackFace = dd.FrontFace;
    CHECK(ID3D10Device_CreateDepthStencilState(dev, &dd, &depthstate), "CreateDepthStencilState");

    memset(&vp, 0, sizeof vp);
    vp.Width = DXT_W; vp.Height = DXT_H; vp.MaxDepth = 1.0f;

    dxt_clock_start(&clock, hwnd, "BCD DirectX 10 test");
    dxt_log(API ": drawing");
    while (dxt_pump()) {
        static const float clear[4] = { 0.06f, 0.07f, 0.10f, 1.0f };
        void *mapped = NULL;
        dxt_mat mvp = dxt_mul(dxt_mul(dxt_world(dxt_seconds(&clock)), dxt_view()), dxt_proj());
        HRESULT hr;

        if (SUCCEEDED(ID3D10Buffer_Map(cb, D3D10_MAP_WRITE_DISCARD, 0, &mapped))) {
            memcpy(mapped, &mvp, sizeof mvp);
            ID3D10Buffer_Unmap(cb);
        }
        ID3D10Device_OMSetRenderTargets(dev, 1, &rtv, dsv);
        ID3D10Device_OMSetDepthStencilState(dev, depthstate, 0);
        ID3D10Device_RSSetViewports(dev, 1, &vp);
        ID3D10Device_RSSetState(dev, raster);
        ID3D10Device_ClearRenderTargetView(dev, rtv, clear);
        ID3D10Device_ClearDepthStencilView(dev, dsv, D3D10_CLEAR_DEPTH | D3D10_CLEAR_STENCIL, 1.0f, 0);
        ID3D10Device_IASetInputLayout(dev, il);
        ID3D10Device_IASetPrimitiveTopology(dev, D3D10_PRIMITIVE_TOPOLOGY_TRIANGLELIST);
        ID3D10Device_IASetVertexBuffers(dev, 0, 1, &vb, &stride, &offset);
        ID3D10Device_IASetIndexBuffer(dev, ib, DXGI_FORMAT_R16_UINT, 0);
        ID3D10Device_VSSetShader(dev, vs);
        ID3D10Device_VSSetConstantBuffers(dev, 0, 1, &cb);
        ID3D10Device_PSSetShader(dev, ps);
        ID3D10Device_PSSetShaderResources(dev, 0, 1, &srv);
        ID3D10Device_PSSetSamplers(dev, 0, 1, &sampler);
        ID3D10Device_DrawIndexed(dev, 36, 0, 0);
        hr = IDXGISwapChain_Present(swap, 1, 0);
        if (FAILED(hr) && clock.total < 5) dxt_log(API ": Present returned %#lx", (unsigned long)hr);
        dxt_clock_frame(&clock);
    }
    dxt_log(API ": the window was closed after %u frames", clock.total);

    ID3D10Device_ClearState(dev);
    ID3D10DepthStencilState_Release(depthstate);
    ID3D10RasterizerState_Release(raster);
    ID3D10SamplerState_Release(sampler);
    ID3D10ShaderResourceView_Release(srv);
    ID3D10Texture2D_Release(tex);
    ID3D10Buffer_Release(cb);
    ID3D10Buffer_Release(ib);
    ID3D10Buffer_Release(vb);
    ID3D10InputLayout_Release(il);
    ID3D10PixelShader_Release(ps);
    ID3D10VertexShader_Release(vs);
    ID3D10DepthStencilView_Release(dsv);
    ID3D10Texture2D_Release(depth);
    ID3D10RenderTargetView_Release(rtv);
    ID3D10Texture2D_Release(back);
    IDXGISwapChain_Release(swap);
    ID3D10Device_Release(dev);
    return 0;
}

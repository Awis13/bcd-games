// BCD's Direct3D 10 and 11 test cube (d3d10_cube.c, d3d11_cube.c). Compiled offline, to Shader Model
// 4.0 for Direct3D 10 and 5.0 for Direct3D 11, by tools/gen-dx-test-shaders.sh into cube_shaders.h.

cbuffer Frame : register(b0)
{
    row_major float4x4 mvp;
};

Texture2D tex : register(t0);
SamplerState smp : register(s0);

struct VSIn
{
    float3 pos : POSITION;
    float2 uv : TEXCOORD0;
    float4 col : COLOR0;
};

struct VSOut
{
    float4 pos : SV_POSITION;
    float2 uv : TEXCOORD0;
    float4 col : COLOR0;
};

VSOut vs_main(VSIn i)
{
    VSOut o;
    o.pos = mul(float4(i.pos, 1.0f), mvp);
    o.uv = i.uv;
    o.col = i.col;
    return o;
}

float4 ps_main(VSOut i) : SV_Target
{
    return tex.Sample(smp, i.uv) * i.col;
}

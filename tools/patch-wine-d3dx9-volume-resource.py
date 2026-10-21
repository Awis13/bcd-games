#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""D3DXCreateVolumeTextureFromResourceExA/W in Wine's d3dx9 (madeira-bcd).

Wine lists both as stubs in every d3dx9_NN.spec: the first call aborts the program with "unimplemented
function d3dx9_43.dll.D3DXCreateVolumeTextureFromResourceExW". GTA IV ended there (tester's log 2934SV,
build 574). They are implemented the way Wine's D3DXCreateTextureFromResourceExW is: find the resource
(RT_RCDATA), load it, and hand it to D3DXCreateVolumeTextureFromFileInMemoryEx. A program that never calls
them sees no change; one that did was aborted before.

Usage: patch-wine-d3dx9-volume-resource.py <wine source dir>. Idempotent; fails by name if an anchor moves.
"""
import pathlib
import re
import sys

wine = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "wine")
TEXTURE = wine / "dlls/d3dx9_36/texture.c"
MARKER = "madeira-bcd: D3DXCreateVolumeTextureFromResourceEx"

CODE = r'''
/* madeira-bcd: D3DXCreateVolumeTextureFromResourceEx (tools/patch-wine-d3dx9-volume-resource.py):
 * the resource is loaded and handed to D3DXCreateVolumeTextureFromFileInMemoryEx, as
 * D3DXCreateTextureFromResourceExW does for 2D textures (GTA IV called it; Wine had a stub). */
HRESULT WINAPI D3DXCreateVolumeTextureFromResourceExW(struct IDirect3DDevice9 *device, HMODULE srcmodule,
        const WCHAR *resource, UINT width, UINT height, UINT depth, UINT miplevels, DWORD usage,
        D3DFORMAT format, D3DPOOL pool, DWORD filter, DWORD mipfilter, D3DCOLOR colorkey,
        D3DXIMAGE_INFO *srcinfo, PALETTEENTRY *palette, struct IDirect3DVolumeTexture9 **volume_texture)
{
    HRSRC resinfo;
    void *buffer;
    DWORD size;

    TRACE("device %p, srcmodule %p, resource %s, volume_texture %p.\n", device, srcmodule,
            debugstr_w(resource), volume_texture);

    if (!device || !volume_texture)
        return D3DERR_INVALIDCALL;
    if (!(resinfo = FindResourceW(srcmodule, resource, (const WCHAR *)RT_RCDATA)))
        return D3DXERR_INVALIDDATA;
    if (FAILED(load_resource_into_memory(srcmodule, resinfo, &buffer, &size)))
        return D3DXERR_INVALIDDATA;
    return D3DXCreateVolumeTextureFromFileInMemoryEx(device, buffer, size, width, height, depth, miplevels,
            usage, format, pool, filter, mipfilter, colorkey, srcinfo, palette, volume_texture);
}

HRESULT WINAPI D3DXCreateVolumeTextureFromResourceExA(struct IDirect3DDevice9 *device, HMODULE srcmodule,
        const char *resource, UINT width, UINT height, UINT depth, UINT miplevels, DWORD usage,
        D3DFORMAT format, D3DPOOL pool, DWORD filter, DWORD mipfilter, D3DCOLOR colorkey,
        D3DXIMAGE_INFO *srcinfo, PALETTEENTRY *palette, struct IDirect3DVolumeTexture9 **volume_texture)
{
    HRSRC resinfo;
    void *buffer;
    DWORD size;

    TRACE("device %p, srcmodule %p, resource %s, volume_texture %p.\n", device, srcmodule,
            debugstr_a(resource), volume_texture);

    if (!device || !volume_texture)
        return D3DERR_INVALIDCALL;
    if (!(resinfo = FindResourceA(srcmodule, resource, (const char *)RT_RCDATA)))
        return D3DXERR_INVALIDDATA;
    if (FAILED(load_resource_into_memory(srcmodule, resinfo, &buffer, &size)))
        return D3DXERR_INVALIDDATA;
    return D3DXCreateVolumeTextureFromFileInMemoryEx(device, buffer, size, width, height, depth, miplevels,
            usage, format, pool, filter, mipfilter, colorkey, srcinfo, palette, volume_texture);
}
'''

SPEC_ARGS = "(ptr ptr {s} long long long long long long long long long long ptr ptr ptr)"


def main():
    text = TEXTURE.read_text()
    if MARKER in text:
        print("d3dx9 volume texture from resource: already applied")
        return
    if "HRESULT WINAPI D3DXCreateTextureFromResourceExW(" not in text or "load_resource_into_memory" not in text:
        sys.exit("patch-wine-d3dx9-volume-resource: anchor not found in " + str(TEXTURE))
    if "D3DXCreateVolumeTextureFromResourceExW(struct" in text:
        sys.exit("patch-wine-d3dx9-volume-resource: Wine already implements it; drop this patch")
    TEXTURE.write_text(text.rstrip("\n") + "\n" + CODE)
    specs = 0
    for spec in sorted((wine / "dlls").glob("d3dx9_*/d3dx9_*.spec")):
        s = spec.read_text()
        n = 0
        for suffix, kind in (("A", "str"), ("W", "wstr")):
            s, k = re.subn(r"^@ stub D3DXCreateVolumeTextureFromResourceEx%s\([^)]*\)$" % suffix,
                           "@ stdcall D3DXCreateVolumeTextureFromResourceEx%s%s" % (suffix, SPEC_ARGS.format(s=kind)),
                           s, flags=re.M)
            n += k
        if n:
            spec.write_text(s)
            specs += 1
    if not specs:
        sys.exit("patch-wine-d3dx9-volume-resource: no d3dx9 .spec lists the stubs")
    print(f"d3dx9 volume texture from resource: applied (texture.c, {specs} spec files)")


if __name__ == "__main__":
    main()

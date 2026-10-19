#!/usr/bin/env python3
"""Native MIN/MAX reduction samplers (Metal, iOS 26, Apple10 GPUs).

D3D12 MINIMUM / MAXIMUM filters (0x100 / 0x180 reduction bits) return the
smallest / largest texel of the filter footprint -- the classic Hi-Z depth
pyramid for GPU occlusion culling. Ghost of Tsushima creates one (filter 0x114,
owner's log 2026-10-02 10:47:07) and its culling compute dispatches swing
frame to frame ([ind-count] DIP/SPIKE), which is the grass flickering in
front of the main menu katana. WMTSamplerInfo has no field for the mode, so
madeira-d3d12 (madeira.cfg sampler-reduction = 3) puts it in the struct's
padding byte after support_argument_buffers: 0xA1 minimum, 0xA2 maximum.
Here, with the same key set (DXMT's own D3D11 samplers leave that byte
uninitialised, so it is never trusted otherwise), the sampler descriptor gets
MTLSamplerDescriptor.reductionMode (iOS 26; MTLSamplerReductionModeMinimum 1,
Maximum 2) when the device supports MTLGPUFamilyApple10 -- the same gate
MoltenVK 1.4.2 uses for VK_EXT_sampler_filter_minmax. The selector is sent by
name so an older SDK still compiles. Logs the first few [sampler-reduction]
lines. Native only. Idempotent; fails by name if the anchor moves.
Run from the repository root.
"""
import pathlib
import sys

PATH = pathlib.Path("dxmt/src/winemetal/unix/winemetal_unix.c")
MARKER = "madeira-bcd: native sampler reduction"

OLD = """  sampler_desc.supportArgumentBuffers = info->support_argument_buffers;

  id<MTLSamplerState> sampler = [device newSamplerStateWithDescriptor:sampler_desc];"""
NEW = """  sampler_desc.supportArgumentBuffers = info->support_argument_buffers;
  { /* madeira-bcd: native sampler reduction (tools/patch-winemetal-sampler-reduction.py) */
    static int on = -1, said;
    uint8_t tag = ((const uint8_t *)info)[__builtin_offsetof(struct WMTSamplerInfo, support_argument_buffers) + 1];
    if (on < 0) on = madeira_cfg_int("sampler-reduction", 0) == 3;
    if (on && (tag == 0xA1 || tag == 0xA2)) {
      SEL sel = sel_registerName("setReductionMode:");
      int ok = [device supportsFamily:(MTLGPUFamily)1010] && [sampler_desc respondsToSelector:sel];
      if (ok) ((void (*)(id, SEL, NSUInteger))objc_msgSend)(sampler_desc, sel, (NSUInteger)(tag & 3));
      if (said++ < 6)
        fprintf(stderr, "[sampler-reduction] madeira-bcd %s sampler: %s (min %lu mag %lu mip %lu)\\n",
                tag == 0xA1 ? "MINIMUM" : "MAXIMUM",
                ok ? "Metal reductionMode set" : "this device has no Metal reduction (needs an Apple10 GPU and iOS 26) -- averaging",
                (unsigned long)info->min_filter, (unsigned long)info->mag_filter, (unsigned long)info->mip_filter);
    }
  }

  id<MTLSamplerState> sampler = [device newSamplerStateWithDescriptor:sampler_desc];"""

src = PATH.read_text()
if MARKER in src:
    print("patch-winemetal-sampler-reduction: already applied")
    sys.exit(0)
if src.count(OLD) != 1:
    sys.exit("patch-winemetal-sampler-reduction: _MTLDevice_newSamplerState anchor not found")
PATH.write_text(src.replace(OLD, NEW))
print("patch-winemetal-sampler-reduction: MIN/MAX samplers use Metal's reductionMode with madeira.cfg sampler-reduction = 3")

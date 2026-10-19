#!/usr/bin/env python3
"""Hand Metal 16-bit index ranges that start on a 4-byte boundary.

32-bit Crysis's D3D10 renderer still draws trees and branches as long streaks
after the short-constant-buffer fix (log 2026-09-30 15:46, build 249: 184
[cb-short] copies, and 659456 DrawIndexed calls whose 16-bit index offset is
2 mod 4, [idx-align]); rocks, water and sky are fine. D3D allows any 2-byte
aligned index offset; DXMT passes it to Metal as is. If the Apple GPU fetches
indices from a 4-byte aligned address, such a draw reads every index one slot
off and stitches unrelated vertices together -- long streaks through the
scene, only on meshes that sit at odd positions in a shared index buffer.

For a 16-bit indexed draw whose offset is not a multiple of 4, this copies the
draw's index range into the encoder's argument buffer (4-byte aligned) and
draws from there. MADEIRA_IDX_REALIGN=0 turns it off. Needs
tools/patch-dxmt-cb-short.py first (its census and argbuf GPU address).

Idempotent; fails by name if an anchor moves. Run from the repository root.
"""
import pathlib
import sys

ROOT = pathlib.Path("dxmt/src")
MARKER = "madeira-bcd: realigned 16-bit indices"


def edit(rel, pairs):
    path = ROOT / rel
    s = path.read_text()
    if MARKER in s:
        print(f"{rel}: already patched")
        return
    for old, new, count in pairs:
        if s.count(old) != count:
            sys.exit(f"patch-dxmt-idx-align: anchor found {s.count(old)} times (want {count}) in {rel}:\n{old}")
        s = s.replace(old, new)
    if MARKER not in s:
        sys.exit(f"patch-dxmt-idx-align: marker missing after editing {rel}")
    path.write_text(s)
    print(f"{rel}: patched")


edit("dxmt/dxmt_context.hpp", [
    ('#include "dxmt_buffer.hpp"\n', '#include "dxmt_buffer.hpp"\n#include <cstring>\n', 1),
    (
    """  std::pair<WMT::Buffer, uint64_t>
  currentIndexBuffer() {""",
    """  /* madeira-bcd: realigned 16-bit indices -- copy [off, off+bytes) of the bound
   * index buffer to the argument buffer at pad_off and point buf/off there.
   * false (buf/off untouched) when the CPU copy is not the GPU's. */
  bool
  realignIndexRange(WMT::Buffer &buf, uint64_t &off, uint64_t pad_off, uint64_t bytes) {
    auto [alloc, suboff] = access<true>(ibuf_, 0, ibuf_->length(), DXMT_ENCODER_RESOURCE_ACESS_READ);
    (void)suboff;
    /* The CPU mapping is the buffer's storage on iOS (unified memory; a
     * GpuManaged buffer is CpuPlaced and Managed does not exist there). */
    static unsigned copied, nomap, oob;
    auto src = (const char *)alloc->mappedMemory(0);
    if (!src || off + bytes > alloc->length()) {
      unsigned n = !src ? ++nomap : ++oob;
      if (n <= 4 || (n & 0xffff) == 0)
        WARN("[idx-align] madeira-bcd encode: realign skipped (", !src ? "no CPU mapping" : "range past the buffer",
             ", #", n, ")");
      return false;
    }
    if (++copied <= 4 || (copied & 0xfffff) == 0)
      WARN("[idx-align] madeira-bcd encode: realigned (#", copied, ", ", bytes, " bytes)");
    memcpy(getMappedArgumentBuffer<char>(pad_off), src + off, bytes);
    buf = getFinalArgumentBuffer();
    off = getFinalArgumentBufferOffset(pad_off);
    return true;
  }

  std::pair<WMT::Buffer, uint64_t>
  currentIndexBuffer() {""", 1),
])

RESERVE = """  /* madeira-bcd: realigned 16-bit indices -- reserve argument-buffer room for a
   * 16-bit draw whose index offset is not 4-byte aligned; 0 = not needed. */
  uint64_t
  madeira_idx_realign_reserve(WMTIndexType type, uint64_t offset, uint32_t count) {
    static int enabled = -1;
    if (enabled < 0) {
      const char *e = getenv("MADEIRA_IDX_REALIGN");
      enabled = !(e && e[0] == '0');
      WARN("[idx-align] madeira-bcd realign ", enabled ? "on" : "off (MADEIRA_IDX_REALIGN=0)");
    }
    if (!enabled || type != WMTIndexTypeUInt16 || !(offset & 3) || !count)
      return 0;
    return PreAllocateArgumentBuffer(uint64_t(count) * 2, 16) + 1;
  }

  void
  STDMETHODCALLTYPE
  DrawIndexed(UINT IndexCount, UINT StartIndexLocation, INT BaseVertexLocation) override {"""

edit("d3d11/d3d11_context_impl.cpp", [
    ("""  void
  STDMETHODCALLTYPE
  DrawIndexed(UINT IndexCount, UINT StartIndexLocation, INT BaseVertexLocation) override {""", RESERVE, 1),
    ("""    madeira_idx_align_census(IndexType, IndexBufferOffset);
    EmitOP([IndexType, IndexBufferOffset, Primitive, IndexCount, BaseVertexLocation](ArgumentEncodingContext &enc) {""",
     """    madeira_idx_align_census(IndexType, IndexBufferOffset);
    uint64_t RealignOffset = madeira_idx_realign_reserve(IndexType, IndexBufferOffset, IndexCount);
    EmitOP([IndexType, IndexBufferOffset, Primitive, IndexCount, BaseVertexLocation,
            RealignOffset](ArgumentEncodingContext &enc) {""", 1),
    ("""      cmd.index_count = IndexCount;
      cmd.index_type = IndexType;
      cmd.index_buffer = index_buffer;
      cmd.index_buffer_offset = IndexBufferOffset + index_sub_offset;
      cmd.base_vertex = BaseVertexLocation;
      cmd.base_instance = 0;
      cmd.instance_count = 1;""",
     """      cmd.index_count = IndexCount;
      cmd.index_type = IndexType;
      cmd.index_buffer = index_buffer;
      cmd.index_buffer_offset = IndexBufferOffset + index_sub_offset;
      if (RealignOffset) {
        WMT::Buffer b = index_buffer;
        uint64_t o = IndexBufferOffset + index_sub_offset;
        if (enc.realignIndexRange(b, o, RealignOffset - 1, uint64_t(IndexCount) * 2)) {
          cmd.index_buffer = b;
          cmd.index_buffer_offset = o;
        }
      }
      cmd.base_vertex = BaseVertexLocation;
      cmd.base_instance = 0;
      cmd.instance_count = 1;""", 1),
    ("""    madeira_idx_align_census(IndexType, IndexBufferOffset);
    EmitOP([IndexType, IndexBufferOffset, Primitive, InstanceCount, BaseVertexLocation, StartInstanceLocation,
          IndexCountPerInstance](ArgumentEncodingContext &enc) {""",
     """    madeira_idx_align_census(IndexType, IndexBufferOffset);
    uint64_t RealignOffset = madeira_idx_realign_reserve(IndexType, IndexBufferOffset, IndexCountPerInstance);
    EmitOP([IndexType, IndexBufferOffset, Primitive, InstanceCount, BaseVertexLocation, StartInstanceLocation,
          IndexCountPerInstance, RealignOffset](ArgumentEncodingContext &enc) {""", 1),
    ("""      cmd.index_count = IndexCountPerInstance;
      cmd.index_type = IndexType;
      cmd.index_buffer = index_buffer;
      cmd.index_buffer_offset = IndexBufferOffset + index_sub_offset;
      cmd.base_vertex = BaseVertexLocation;
      cmd.base_instance = StartInstanceLocation;
      cmd.instance_count = InstanceCount;
    });""",
     """      cmd.index_count = IndexCountPerInstance;
      cmd.index_type = IndexType;
      cmd.index_buffer = index_buffer;
      cmd.index_buffer_offset = IndexBufferOffset + index_sub_offset;
      if (RealignOffset) {
        WMT::Buffer b = index_buffer;
        uint64_t o = IndexBufferOffset + index_sub_offset;
        if (enc.realignIndexRange(b, o, RealignOffset - 1, uint64_t(IndexCountPerInstance) * 2)) {
          cmd.index_buffer = b;
          cmd.index_buffer_offset = o;
        }
      }
      cmd.base_vertex = BaseVertexLocation;
      cmd.base_instance = StartInstanceLocation;
      cmd.instance_count = InstanceCount;
    });""", 1),
])

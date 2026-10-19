#!/usr/bin/env python3
"""Read a constant buffer bound shorter than the shader declares as zeros.

D3D10/11 lets a draw bind a constant buffer smaller than the shader's
dcl_constantbuffer cbN[size]; the constants past the end of the buffer read as
0. airconv turns cbN[i] into a plain load through the buffer's GPU address, and
Metal has no robust buffer access, so here those constants were whatever memory
follows the buffer. 32-bit Crysis (CryRenderD3D10) on build 247 draws nearby
meshes with their vertices thrown far out in world space (logs 2026-09-30
13:19 and 13:55; -dx9 renders the same scene correctly), which is what garbage
transform or bending constants look like.

This patch
  * parses each shader's dcl_constantbuffer sizes from its SHDR/SHEX chunk
    (Shader::cb_declared_vec4(), no airconv ABI change);
  * in UploadShaderStageResourceBinding, reserves room in the encoder's
    argument buffer for every bound buffer that is shorter than declared, and
    keeps those slots dirty so the copy is refreshed on every draw;
  * in encodeConstantBuffers, copies the bound bytes there, zero-fills the
    rest and binds that copy instead ([cb-short] in the log counts them);
  * counts 16-bit index buffer offsets that are not a multiple of 4
    ([idx-align], log only).

Idempotent; fails by name if an anchor moves. Run from the repository root.
"""
import pathlib
import re
import sys

ROOT = pathlib.Path("dxmt/src")
MARKER = "madeira-bcd: short constant buffers"


def edit(rel, pairs):
    path = ROOT / rel
    s = path.read_text()
    if MARKER in s:
        print(f"{rel}: already patched")
        return
    for old, new in pairs:
        if callable(old):
            s2 = old(s)
            if s2 == s:
                sys.exit(f"patch-dxmt-cb-short: regex edit did nothing in {rel}")
            s = s2
            continue
        if s.count(old) != 1:
            sys.exit(f"patch-dxmt-cb-short: anchor not found once in {rel}:\n{old}")
        s = s.replace(old, new)
    if MARKER not in s:
        sys.exit(f"patch-dxmt-cb-short: marker missing after editing {rel}")
    path.write_text(s)
    print(f"{rel}: patched")


# --- Shader interface --------------------------------------------------------
edit("d3d11/d3d11_shader.hpp", [(
    "  virtual MTL_SM50_SHADER_ARGUMENT *arguments_info() = 0;\n",
    "  virtual MTL_SM50_SHADER_ARGUMENT *arguments_info() = 0;\n"
    "  /* madeira-bcd: short constant buffers -- dcl_constantbuffer size (vec4)\n"
    "   * per slot cb0..cb13, 0 when not declared or not known. */\n"
    "  virtual const uint16_t *cb_declared_vec4() = 0;\n",
)])

# --- declared sizes from the bytecode ----------------------------------------
PARSER = r'''
/* madeira-bcd: short constant buffers -- read dcl_constantbuffer cbN[size]
 * from the SHDR/SHEX chunk. SM4/5 only (2D cb operand, immediate indices). */
static void
madeira_parse_cb_sizes(const void *pBytecode, uint32_t BytecodeLength, uint16_t out[14]) {
  for (unsigned i = 0; i < 14; i++)
    out[i] = 0;
  const uint8_t *b = (const uint8_t *)pBytecode;
  if (!b || BytecodeLength < 32 || memcmp(b, "DXBC", 4))
    return;
  uint32_t count;
  memcpy(&count, b + 28, 4);
  if (count > 64 || 32 + 4ull * count > BytecodeLength)
    return;
  for (uint32_t c = 0; c < count; c++) {
    uint32_t off;
    memcpy(&off, b + 32 + 4 * c, 4);
    if (off + 8ull > BytecodeLength)
      continue;
    if (memcmp(b + off, "SHDR", 4) && memcmp(b + off, "SHEX", 4))
      continue;
    uint32_t size;
    memcpy(&size, b + off + 4, 4);
    if (off + 8ull + size > BytecodeLength || size < 8)
      return;
    const uint32_t *t = (const uint32_t *)(b + off + 8);
    uint32_t n = size / 4, len = t[1] < n ? t[1] : n;
    for (uint32_t i = 2; i < len;) {
      uint32_t tok = t[i], op = tok & 0x7ff;
      uint32_t ilen = op == 0x35 /* customdata */ ? (i + 1 < len ? t[i + 1] : 0) : (tok >> 24) & 0x7f;
      if (!ilen || i + ilen > len)
        return;
      if (op == 0x59 /* dcl_constantbuffer */ && ilen >= 4) {
        uint32_t opnd = t[i + 1], k = i + 2;
        if (opnd & 0x80000000u)
          k++; /* extended operand */
        uint32_t dim = (opnd >> 20) & 3, r0 = (opnd >> 22) & 7, r1 = (opnd >> 25) & 7;
        if (dim == 2 && r0 == 0 && r1 == 0 && k + 1 < i + ilen) {
          uint32_t slot = t[k], vec4 = t[k + 1];
          if (slot < 14)
            out[slot] = vec4 > 4096 ? 4096 : (uint16_t)vec4;
        }
      }
      i += ilen;
    }
    return;
  }
}
'''

edit("d3d11/d3d11_pipeline_cache.cpp", [
    ('#include "../d3d10/d3d10_shader.hpp"\n',
     '#include "../d3d10/d3d10_shader.hpp"\n#include <cstring>\n' + PARSER),
    ("    MTL_SM50_SHADER_ARGUMENT *arguments_info_buffer;\n",
     "    MTL_SM50_SHADER_ARGUMENT *arguments_info_buffer;\n"
     "    uint16_t cb_vec4_[14] = {}; /* madeira-bcd: short constant buffers */\n"),
    ("    virtual MTL_SM50_SHADER_ARGUMENT *arguments_info() {\n",
     "    virtual const uint16_t *cb_declared_vec4() { return cb_vec4_; }\n"
     "    void set_cb_declared(const void *code, uint32_t len) { madeira_parse_cb_sizes(code, len, cb_vec4_); }\n"
     "    virtual MTL_SM50_SHADER_ARGUMENT *arguments_info() {\n"),
    ("    auto shader = std::make_unique<CachedSM50Shader>(this, sm50, sha1, reflection);\n",
     "    auto shader = std::make_unique<CachedSM50Shader>(this, sm50, sha1, reflection);\n"
     "    shader->set_cb_declared(pBytecode, BytecodeLength);\n"),
])

# --- the argument buffer's GPU address ---------------------------------------
edit("dxmt/dxmt_command_queue.hpp", [(
    "  std::tuple<void *, WMT::Buffer, uint64_t>\n  AllocateArgumentBuffer(uint64_t seq, size_t size) {\n",
    "  /* madeira-bcd: short constant buffers -- the same, plus the block's GPU address */\n"
    "  std::tuple<void *, WMT::Buffer, uint64_t, uint64_t>\n"
    "  AllocateArgumentBufferGpu(uint64_t seq, size_t size) {\n"
    "    auto [block, offset] = argbuf_allocator.allocate(seq, cpu_coherent.signaledValue(), size, 64);\n"
    "    return {ptr_add(block.mapped_address, offset), block.buffer, offset, block.gpu_address};\n"
    "  }\n\n"
    "  std::tuple<void *, WMT::Buffer, uint64_t>\n  AllocateArgumentBuffer(uint64_t seq, size_t size) {\n",
)])

HDR_DECL_OLD = """  void encodeConstantBuffers(
      const MTL_SHADER_REFLECTION *reflection, const MTL_SM50_SHADER_ARGUMENT *constant_buffers,
      uint64_t argument_buffer_offset
  );"""
HDR_DECL_NEW = """  void encodeConstantBuffers(
      const MTL_SHADER_REFLECTION *reflection, const MTL_SM50_SHADER_ARGUMENT *constant_buffers,
      uint64_t argument_buffer_offset, const uint32_t *short_cb = nullptr
  ); /* madeira-bcd: short constant buffers -- short_cb[2*slot] = argbuf offset + 1, [2*slot+1] = bytes */"""

edit("dxmt/dxmt_context.hpp", [
    ("  WMT::Buffer allocated_argbuf;\n  uint64_t allocated_argbuf_offset;\n  void *allocated_argbuf_mapping;\n"
     "  uint8_t dsv_planar_flags;\n",
     "  WMT::Buffer allocated_argbuf;\n  uint64_t allocated_argbuf_offset;\n  void *allocated_argbuf_mapping;\n"
     "  uint64_t allocated_argbuf_gpu_address; /* madeira-bcd: short constant buffers */\n"
     "  uint8_t dsv_planar_flags;\n"),
    ("struct ComputeEncoderData : EncoderData {\n  wmtcmd_compute_nop cmd_head;\n  wmtcmd_base *cmd_tail;\n"
     "  WMT::Buffer allocated_argbuf;\n  uint64_t allocated_argbuf_offset;\n  void *allocated_argbuf_mapping;\n",
     "struct ComputeEncoderData : EncoderData {\n  wmtcmd_compute_nop cmd_head;\n  wmtcmd_base *cmd_tail;\n"
     "  WMT::Buffer allocated_argbuf;\n  uint64_t allocated_argbuf_offset;\n  void *allocated_argbuf_mapping;\n"
     "  uint64_t allocated_argbuf_gpu_address;\n"),
    (HDR_DECL_OLD, HDR_DECL_NEW),
    ("  template <bool ComputeCommandEncoder = false>\n  uint64_t\n  getFinalArgumentBufferOffset(size_t offset) {\n",
     "  template <bool ComputeCommandEncoder = false>\n  uint64_t\n  getArgumentBufferGpuAddress(size_t offset) {\n"
     "    if constexpr (ComputeCommandEncoder)\n"
     "      return reinterpret_cast<ComputeEncoderData *>(encoder_current)->allocated_argbuf_gpu_address + offset;\n"
     "    return reinterpret_cast<RenderEncoderData *>(encoder_current)->allocated_argbuf_gpu_address + offset;\n"
     "  }\n\n"
     "  template <bool ComputeCommandEncoder = false>\n  uint64_t\n  getFinalArgumentBufferOffset(size_t offset) {\n"),
])

ENC_OLD = """      auto argbuf = cbuf.buffer;
      auto valid_length = argbuf->length() > cbuf.offset ? argbuf->length() - cbuf.offset : 0;
      auto [argbuf_alloc, argbuf_offset] = access<PreRasterStage>(argbuf, cbuf.offset, valid_length, DXMT_ENCODER_RESOURCE_ACESS_READ);
      encoded_buffer[arg.StructurePtrOffset] = argbuf_alloc->gpuAddress() + argbuf_offset + cbuf.offset;
      makeResident<stage, kind>(argbuf.ptr());
      break;"""
ENC_NEW = """      auto argbuf = cbuf.buffer;
      auto valid_length = argbuf->length() > cbuf.offset ? argbuf->length() - cbuf.offset : 0;
      auto [argbuf_alloc, argbuf_offset] = access<PreRasterStage>(argbuf, cbuf.offset, valid_length, DXMT_ENCODER_RESOURCE_ACESS_READ);
      encoded_buffer[arg.StructurePtrOffset] = argbuf_alloc->gpuAddress() + argbuf_offset + cbuf.offset;
      makeResident<stage, kind>(argbuf.ptr());
      /* madeira-bcd: short constant buffers -- bind a zero-padded copy. D3D reads
       * past the end of a constant buffer as 0; Metal would read the next bytes. */
      if (short_cb && arg.SM50BindingSlot < 14 && short_cb[2 * arg.SM50BindingSlot]) {
        uint32_t pad_off = short_cb[2 * arg.SM50BindingSlot] - 1, pad_bytes = short_cb[2 * arg.SM50BindingSlot + 1];
        /* The CPU mapping is the buffer's storage on iOS (unified memory; a
         * GpuManaged buffer is CpuPlaced and Managed does not exist there). */
        auto src = (const char *)argbuf_alloc->mappedMemory(0);
        static unsigned madeira_cb_copied, madeira_cb_nomap;
        if (!src) {
          if (++madeira_cb_nomap <= 4 || (madeira_cb_nomap & 0xffff) == 0)
            WARN("[cb-short] madeira-bcd encode: no CPU mapping, copy skipped (#", madeira_cb_nomap, ")");
        } else if (++madeira_cb_copied <= 4 || (madeira_cb_copied & 0xfffff) == 0) {
          WARN("[cb-short] madeira-bcd encode: zero-padded copy bound (#", madeira_cb_copied, ", ", pad_bytes, " bytes)");
        }
        if (src) {
          char *dst = getMappedArgumentBuffer<char, stage == PipelineStage::Compute>(pad_off);
          size_t have = valid_length < pad_bytes ? valid_length : pad_bytes;
          memcpy(dst, src + argbuf_offset + cbuf.offset, have);
          memset(dst + have, 0, pad_bytes - have);
          encoded_buffer[arg.StructurePtrOffset] = getArgumentBufferGpuAddress<stage == PipelineStage::Compute>(
              getFinalArgumentBufferOffset<stage == PipelineStage::Compute>(pad_off));
        }
      }
      break;"""


def instantiations(s):
    return re.sub(
        r"(template void ArgumentEncodingContext::encodeConstantBuffers<[^>]*>\(\n"
        r"    const MTL_SHADER_REFLECTION \*reflection, const MTL_SM50_SHADER_ARGUMENT \*constant_buffers,\n"
        r"    uint64_t argument_buffer_offset)\n\);",
        r"\1, const uint32_t *short_cb\n);", s)


edit("dxmt/dxmt_context.cpp", [
    ('#include "dxmt_context.hpp"\n', '#include "dxmt_context.hpp"\n#include <cstring>\n'),
    ("ArgumentEncodingContext::encodeConstantBuffers(const MTL_SHADER_REFLECTION *reflection, "
     "const MTL_SM50_SHADER_ARGUMENT * constant_buffers, uint64_t offset) {",
     "ArgumentEncodingContext::encodeConstantBuffers(const MTL_SHADER_REFLECTION *reflection, "
     "const MTL_SM50_SHADER_ARGUMENT * constant_buffers, uint64_t offset, const uint32_t *short_cb) {"),
    (instantiations, None),
    (ENC_OLD, ENC_NEW),
    ("  encoder_info->render_target_count = render_target_count;\n"
     "  auto [gpu_buffer_contents, gpu_buffer_, offset] = queue_.AllocateArgumentBuffer(seq_id_, encoder_argbuf_size);\n"
     "  encoder_info->allocated_argbuf = gpu_buffer_;\n",
     "  encoder_info->render_target_count = render_target_count;\n"
     "  auto [gpu_buffer_contents, gpu_buffer_, offset, gpu_address_] = queue_.AllocateArgumentBufferGpu(seq_id_, encoder_argbuf_size);\n"
     "  encoder_info->allocated_argbuf_gpu_address = gpu_address_;\n"
     "  encoder_info->allocated_argbuf = gpu_buffer_;\n"),
    ("  encoder_info->cmd_tail = (wmtcmd_base *)&encoder_info->cmd_head;\n"
     "  auto [gpu_buffer_contents, gpu_buffer_, offset] = queue_.AllocateArgumentBuffer(seq_id_, encoder_argbuf_size);\n"
     "  encoder_info->allocated_argbuf = gpu_buffer_;\n"
     "  encoder_info->allocated_argbuf_offset = offset;\n"
     "  encoder_info->allocated_argbuf_mapping = gpu_buffer_contents;\n"
     "  encoder_current = encoder_info;\n\n"
     "  currentFrameStatistics().compute_pass_count++;\n",
     "  encoder_info->cmd_tail = (wmtcmd_base *)&encoder_info->cmd_head;\n"
     "  auto [gpu_buffer_contents, gpu_buffer_, offset, gpu_address_] = queue_.AllocateArgumentBufferGpu(seq_id_, encoder_argbuf_size);\n"
     "  encoder_info->allocated_argbuf_gpu_address = gpu_address_;\n"
     "  encoder_info->allocated_argbuf = gpu_buffer_;\n"
     "  encoder_info->allocated_argbuf_offset = offset;\n"
     "  encoder_info->allocated_argbuf_mapping = gpu_buffer_contents;\n"
     "  encoder_current = encoder_info;\n\n"
     "  currentFrameStatistics().compute_pass_count++;\n"),
])

UP_OLD = """    if (reflection->NumConstantBuffers && dirty_cbuffer) {
      auto ConstantBufferCount = reflection->NumConstantBuffers;
      auto offset = PreAllocateArgumentBuffer(ConstantBufferCount << 3, 32);
      EmitST([=, cb = managed_shader->constant_buffers_info()](ArgumentEncodingContext &enc) {
        enc.encodeConstantBuffers<stage, kind>(reflection, cb, offset);
      });
      ShaderStage.ConstantBuffers.clear_dirty();
    }"""
UP_NEW = """    if (reflection->NumConstantBuffers && dirty_cbuffer) {
      auto ConstantBufferCount = reflection->NumConstantBuffers;
      auto offset = PreAllocateArgumentBuffer(ConstantBufferCount << 3, 32);
      /* madeira-bcd: short constant buffers -- a bound buffer shorter than the
       * shader's dcl_constantbuffer gets a zero-padded copy in the argument
       * buffer (see tools/patch-dxmt-cb-short.py). */
      std::array<uint32_t, 28> short_cb{};
      uint32_t short_mask = 0;
      {
        auto cbinfo = managed_shader->constant_buffers_info();
        const uint16_t *decl = managed_shader->cb_declared_vec4();
        for (unsigned i = 0; i < ConstantBufferCount; i++) {
          unsigned slot = cbinfo[i].SM50BindingSlot;
          if (slot >= 14 || !decl[slot] || !ShaderStage.ConstantBuffers.test_bound(slot))
            continue;
          auto &entry = ShaderStage.ConstantBuffers.at(slot);
          if (entry.NumConstants >= decl[slot])
            continue;
          uint32_t bytes = uint32_t(decl[slot]) << 4;
          short_cb[2 * slot] = uint32_t(PreAllocateArgumentBuffer(bytes, 256)) + 1;
          short_cb[2 * slot + 1] = bytes;
          short_mask |= 1u << slot;
          static unsigned madeira_cb_short_n;
          if (++madeira_cb_short_n <= 24 || (madeira_cb_short_n & 0xffff) == 0)
            WARN("[cb-short] madeira-bcd #", madeira_cb_short_n, " stage=", unsigned(stage), " cb", slot,
                 " declared=", decl[slot], " vec4 bound=", entry.NumConstants, " vec4 shader=",
                 managed_shader->sha1().string().substr(0, 16), " -> zero-padded copy");
        }
      }
      EmitST([=, cb = managed_shader->constant_buffers_info()](ArgumentEncodingContext &enc) {
        enc.encodeConstantBuffers<stage, kind>(reflection, cb, offset, short_mask ? short_cb.data() : nullptr);
      });
      ShaderStage.ConstantBuffers.clear_dirty();
      /* the copy is a snapshot: take a fresh one for every draw */
      for (unsigned slot = 0; slot < 14; slot++)
        if (short_mask & (1u << slot))
          ShaderStage.ConstantBuffers.set_dirty(slot);
    }"""

IDX_CENSUS = """
  /* madeira-bcd: short constant buffers -- [idx-align] census: 16-bit index
   * buffer offsets that are not a multiple of 4 (log only). */
  static void
  madeira_idx_align_census(WMTIndexType type, uint64_t offset) {
    if (type != WMTIndexTypeUInt16 || !(offset & 3))
      return;
    static unsigned n;
    if (++n <= 8 || (n & 0xfff) == 0)
      WARN("[idx-align] madeira-bcd #", n, " 16-bit index buffer offset ", offset, " is not a multiple of 4");
  }

  void
  STDMETHODCALLTYPE
  DrawIndexed(UINT IndexCount, UINT StartIndexLocation, INT BaseVertexLocation) override {"""


def idx_calls(s):
    old = ("        StartIndexLocation * (state_.InputAssembler.IndexBufferFormat == DXGI_FORMAT_R32_UINT ? 4 : 2);\n"
           "    EmitOP([IndexType, IndexBufferOffset, Primitive,")
    new = ("        StartIndexLocation * (state_.InputAssembler.IndexBufferFormat == DXGI_FORMAT_R32_UINT ? 4 : 2);\n"
           "    madeira_idx_align_census(IndexType, IndexBufferOffset);\n"
           "    EmitOP([IndexType, IndexBufferOffset, Primitive,")
    if s.count(old) != 2:
        return s
    return s.replace(old, new)


edit("d3d11/d3d11_context_impl.cpp", [
    (UP_OLD, UP_NEW),
    ("  void\n  STDMETHODCALLTYPE\n  DrawIndexed(UINT IndexCount, UINT StartIndexLocation, INT BaseVertexLocation) override {",
     IDX_CENSUS),
    (idx_calls, None),
])

#!/usr/bin/env python3
"""Switchable float experiments for God of War's darkening, plus a 32-bit float
texture census for the black scene on an M1 iPad. Everything is OFF by default.

1. The owner's iPhone (build 275, log 2026-10-01 13:07:31 + screen recording):
   outdoors only, the whole picture -- characters included -- fades to almost
   black over ~4 s (mean brightness 28 -> 11 in the recording, 33-38 s), stays
   there ~35 s, and fades back over ~14 s. Indoors it never happens. That is
   the game's auto-exposure being fed a scene luminance that is far too high
   (or not a number) while the sky/sun is in view. Two places where Metal
   differs from D3D could produce that, and each is now a switch:

   MADEIRA_PS_CLAMP=1  pixel-shader float colour outputs above 65504 are
       written as +-65504 (largest half float) instead of overflowing to Inf
       in a 16-bit float target, the way D3D converts out-of-range finite
       values; Inf and NaN pass as before.
   MADEIRA_PS_CLAMP=2  as 1, and Inf -> +-65504, NaN -> 0 (sanitise).
   MADEIRA_PRECISE_MINMAX=1  min/max use air.fmin/air.fmax instead of the
       fast variants: D3D's min/max return the other operand when one is NaN
       (games use max(x, 0) to kill NaNs); the fast variants do not promise
       that.

   Set in a game's config as env.NAME = value. The native shader cache keeps
   a separate table per combination (cache version + salt), so switching
   does not reuse shaders converted under another setting.

2. An M1 iPad draws God of War's menu UI over a black scene (build 273/275;
   memory was fine on 275). M1 on iPadOS is Apple GPU family 7, and DXMT's
   own format table only gives R32Float/RG32Float/RGBA32Float the Filter
   capability from family 9; a linear sampler on such a texture is not
   supported there. `[f32-tex]` logs the device's answer once
   (supports32BitFloatFiltering, families 7/8/9) and the first sampled 32-bit
   float textures the game creates, with a running count, so the log says
   whether that theory can hold.

Native only (airconv + winemetal); not in the i386 farm key. Idempotent; fails
by name if an anchor moves. Run from the repository root.
"""
import pathlib
import sys

ROOT = pathlib.Path("dxmt/src")
MARKER = "madeira-bcd: float experiments"


def edit(rel, pairs):
    path = ROOT / rel
    s = path.read_text()
    if MARKER in s:
        print(f"{rel}: already patched")
        return
    for old, new in pairs:
        if s.count(old) != 1:
            sys.exit(f"patch-airconv-float-experiments: anchor found {s.count(old)} times (want 1) in {rel}:\n{old}")
        s = s.replace(old, new)
    if MARKER not in s:
        sys.exit(f"patch-airconv-float-experiments: marker missing after editing {rel}")
    path.write_text(s)
    print(f"{rel}: patched")


# --- 1a. pixel-shader output clamp ------------------------------------------
edit("airconv/dxbc_converter_basicblock.cpp", [
    ("""std::function<IRValue(pvalue)> pop_output_reg_fill(uint32_t from_reg, uint32_t mask, uint32_t to_element) {
""",
     """/* madeira-bcd: float experiments (tools/patch-airconv-float-experiments.py) --
 * MADEIRA_PS_CLAMP: 0 off, 1 finite overflow -> +-65504, 2 also Inf -> +-65504
 * and NaN -> 0, on float render-target outputs. */
static int madeira_ps_clamp_mode() {
  static int mode = -1;
  if (mode < 0) {
    const char *e = getenv("MADEIRA_PS_CLAMP");
    mode = e ? atoi(e) : 0;
    if (mode < 0 || mode > 2) mode = 0;
    fprintf(stderr, "[ps-clamp] madeira-bcd pixel output clamp mode %d (MADEIRA_PS_CLAMP; 0 off, 1 finite, 2 +Inf/NaN)\\n", mode);
  }
  return mode;
}

std::function<IRValue(pvalue)> pop_output_reg_fill(uint32_t from_reg, uint32_t mask, uint32_t to_element) {
"""),
    ("""        value = ctx.builder.CreateInsertElement(value, c, (uint64_t)i);
      }
      co_return ctx.builder.CreateInsertValue(ret, value, {to_element});
""",
     """        value = ctx.builder.CreateInsertElement(value, c, (uint64_t)i);
      }
      if (elem->isFloatTy() && madeira_ps_clamp_mode()) { /* madeira-bcd: float experiments */
        auto &b = ctx.builder;
        auto vty = value->getType();
        auto hi = llvm::ConstantFP::get(vty, 65504.0), lo = llvm::ConstantFP::get(vty, -65504.0);
        llvm::Value *over = b.CreateFCmpOGT(value, hi), *under = b.CreateFCmpOLT(value, lo);
        if (madeira_ps_clamp_mode() == 1) {
          over = b.CreateAnd(over, b.CreateFCmpONE(value, llvm::ConstantFP::getInfinity(vty, false)));
          under = b.CreateAnd(under, b.CreateFCmpONE(value, llvm::ConstantFP::getInfinity(vty, true)));
        }
        value = b.CreateSelect(over, hi, value);
        value = b.CreateSelect(under, lo, value);
        if (madeira_ps_clamp_mode() == 2)
          value = b.CreateSelect(b.CreateFCmpUNO(value, value), llvm::ConstantFP::get(vty, 0.0), value);
      }
      co_return ctx.builder.CreateInsertValue(ret, value, {to_element});
"""),
])

# --- 1b. precise min/max -------------------------------------------------------
edit("airconv/nt/dxbc_converter_base.cpp", [
    ("#include \"dxbc_converter_base.hpp\"\n",
     "#include \"dxbc_converter_base.hpp\"\n#include <cstdio>\n#include <cstdlib>\n"),
    ("""  case FloatBinaryOp::Min: {
    Result = air.CreateFPBinOp(AIRBuilder::fmin, LHS, RHS);
    break;
  }
  case FloatBinaryOp::Max: {
    Result = air.CreateFPBinOp(AIRBuilder::fmax, LHS, RHS);
    break;
  }""",
     """  case FloatBinaryOp::Min: {
    Result = air.CreateFPBinOp(AIRBuilder::fmin, LHS, RHS, !madeira_precise_minmax()); /* madeira-bcd: float experiments */
    break;
  }
  case FloatBinaryOp::Max: {
    Result = air.CreateFPBinOp(AIRBuilder::fmax, LHS, RHS, !madeira_precise_minmax());
    break;
  }"""),
    ("""void
Converter::operator()(const InstFloatBinaryOp &bin) {""",
     """/* madeira-bcd: float experiments -- MADEIRA_PRECISE_MINMAX=1 emits air.fmin/fmax
 * (NaN-ignoring, as D3D's min/max) instead of air.fast_fmin/fast_fmax. */
static bool madeira_precise_minmax() {
  static int mode = -1;
  if (mode < 0) {
    const char *e = getenv("MADEIRA_PRECISE_MINMAX");
    mode = e && e[0] == '1';
    fprintf(stderr, "[precise-minmax] madeira-bcd float min/max %s (MADEIRA_PRECISE_MINMAX)\\n",
            mode ? "precise (air.fmin/fmax)" : "fast (default)");
  }
  return mode;
}

void
Converter::operator()(const InstFloatBinaryOp &bin) {"""),
])

# --- 1c. separate shader-cache table per experiment setting --------------------
edit("winemetal/unix/cache.c", [
    ("""int
_CacheReader_alloc_init(void *obj) {
  struct unixcall_cache_alloc_init *params = obj;
  NSString *path = [[NSString alloc] initWithCString:params->path.ptr encoding:NSUTF8StringEncoding];
  params->ret_cache = (obj_handle_t)[[CacheReader alloc] initWithPath:path version:params->version];""",
     """/* madeira-bcd: float experiments -- one cache table per MADEIRA_PS_CLAMP /
 * MADEIRA_PRECISE_MINMAX combination; 0 (both off) keeps the old table. */
static uint64_t madeira_cache_salt(void) {
  static int salt = -1;
  if (salt < 0) {
    const char *a = getenv("MADEIRA_PS_CLAMP"), *b = getenv("MADEIRA_PRECISE_MINMAX");
    int clamp = a ? atoi(a) : 0;
    if (clamp < 0 || clamp > 2) clamp = 0;
    salt = clamp * 10 + (b && b[0] == '1');
  }
  return (uint64_t)salt * 1000000ull;
}

int
_CacheReader_alloc_init(void *obj) {
  struct unixcall_cache_alloc_init *params = obj;
  NSString *path = [[NSString alloc] initWithCString:params->path.ptr encoding:NSUTF8StringEncoding];
  params->ret_cache = (obj_handle_t)[[CacheReader alloc] initWithPath:path version:params->version + madeira_cache_salt()];"""),
    ("""  params->ret_cache = (obj_handle_t)[[CacheWriter alloc] initWithPath:path version:params->version];""",
     """  params->ret_cache = (obj_handle_t)[[CacheWriter alloc] initWithPath:path version:params->version + madeira_cache_salt()];"""),
])

# --- 2. 32-bit float texture census -----------------------------------------
edit("winemetal/unix/winemetal_unix.c", [
    ("""  id<MTLTexture> ret = [device newTextureWithDescriptor:desc];
  params->ret = (obj_handle_t)ret;
  info->gpu_resource_id = [ret gpuResourceID]._impl;
  info->mach_port = 0;
""",
     """  id<MTLTexture> ret = [device newTextureWithDescriptor:desc];
  params->ret = (obj_handle_t)ret;
  info->gpu_resource_id = [ret gpuResourceID]._impl;
  info->mach_port = 0;
  { /* madeira-bcd: float experiments -- 32-bit float textures a shader may sample */
    static int caps_logged;
    static unsigned f32_count;
    MTLPixelFormat pf = desc.pixelFormat;
    if (!caps_logged) {
      caps_logged = 1;
      BOOL filt = [device respondsToSelector:@selector(supports32BitFloatFiltering)]
                      ? [device supports32BitFloatFiltering] : NO;
      fprintf(stderr, "[f32-tex] madeira-bcd device %s: supports32BitFloatFiltering=%d apple7=%d apple8=%d apple9=%d\\n",
              [[device name] UTF8String], (int)filt, (int)[device supportsFamily:MTLGPUFamilyApple7],
              (int)[device supportsFamily:MTLGPUFamilyApple8], (int)[device supportsFamily:MTLGPUFamilyApple9]);
    }
    if ((pf == MTLPixelFormatR32Float || pf == MTLPixelFormatRG32Float || pf == MTLPixelFormatRGBA32Float) &&
        (desc.usage & MTLTextureUsageShaderRead)) {
      f32_count++;
      if (f32_count <= 12 || (f32_count & 255) == 0)
        fprintf(stderr, "[f32-tex] madeira-bcd sampled 32-bit float texture #%u: format %lu %lux%lu mips %lu usage 0x%lx\\n",
                f32_count, (unsigned long)pf, (unsigned long)desc.width, (unsigned long)desc.height,
                (unsigned long)desc.mipmapLevelCount, (unsigned long)desc.usage);
    }
  }
"""),
])

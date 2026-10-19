#!/usr/bin/env python3
"""Probe: what God of War reads back from its luminance pyramid, and how its
32-bit float mip chains are generated.

The outdoor darkening (owner's iPhone, A19) survived every float switch of
build 277 (MADEIRA_PS_CLAMP=1/2, MADEIRA_PRECISE_MINMAX=1) and removing
d3d11.mipClampBC (log 14:23:35). Known: the game builds R32Float pyramids
(128x64 with 8 mips, 64x32 with 7 mips) and copies a texture to a staging
resource ~27 times a second -- most likely the 1x1 exposure/luminance value
the CPU feeds into its eye adaptation. Nothing in the log so far shows the
VALUE, so this native probe reads it:

  [lum-readback]  every small (<= 4x4 texels) texture-to-buffer copy from a
                  32-bit float texture is remembered; on the next one the
                  previous copy's destination (CPU-visible, the GPU has
                  finished it by then) is read and, throttled to ~2 lines/s,
                  logged with format, level and the first four floats.
  [f32-mipgen]    GenerateMips on 32-bit float textures: format, size, levels,
                  running count (first 8, then every 512th).

Compare the [lum-readback] values with the moment the picture darkens: huge,
Inf or NaN values mean the GPU side computes a wrong luminance; sane values
with a dark picture point at the CPU side / readback timing. MADEIRA_LUM_PROBE=0
turns the probe off. Native only. Idempotent; fails by name if an anchor moves.
Run from the repository root.
"""
import pathlib
import sys

PATH = pathlib.Path("dxmt/src/winemetal/unix/winemetal_unix.c")
MARKER = "madeira-bcd: luminance probe"

PAIRS = [
    ("""static NTSTATUS
_MTLBlitCommandEncoder_encodeCommands(void *obj) {
""",
     """/* madeira-bcd: luminance probe (tools/patch-winemetal-lum-probe.py) */
#include <pthread.h>
#include <time.h>
static int madeira_lum_probe_on(void) {
  static int on = -1;
  if (on < 0) {
    const char *e = getenv("MADEIRA_LUM_PROBE");
    on = !(e && e[0] == '0');
    fprintf(stderr, "[lum-readback] madeira-bcd luminance probe %s (MADEIRA_LUM_PROBE)\\n", on ? "on" : "off");
  }
  return on;
}
static int madeira_is_f32(MTLPixelFormat pf) {
  return pf == MTLPixelFormatR32Float || pf == MTLPixelFormatRG32Float || pf == MTLPixelFormatRGBA32Float;
}
static void madeira_lum_note_copy(id<MTLTexture> src, id<MTLBuffer> dst, uint64_t offset,
                                  unsigned level, unsigned w, unsigned h) {
  static id<MTLBuffer> prev_buf;
  static uint64_t prev_off;
  static unsigned prev_level, prev_w, prev_h, n;
  static MTLPixelFormat prev_pf;
  static struct timespec last;
  static pthread_mutex_t lock = PTHREAD_MUTEX_INITIALIZER;
  pthread_mutex_lock(&lock);
  if (prev_buf) {
    const uint8_t *base = (const uint8_t *)[prev_buf contents];
    struct timespec now;
    clock_gettime(CLOCK_MONOTONIC, &now);
    double dt = (now.tv_sec - last.tv_sec) + (now.tv_nsec - last.tv_nsec) / 1e9;
    n++;
    if (base && prev_off + 16 <= [prev_buf length] && (dt >= 0.5 || n <= 4)) {
      float v[4];
      memcpy(v, base + prev_off, sizeof v);
      unsigned comps = prev_pf == MTLPixelFormatR32Float ? 1 : prev_pf == MTLPixelFormatRG32Float ? 2 : 4;
      fprintf(stderr, "[lum-readback] madeira-bcd #%u fmt %lu level %u %ux%u -> %g %g %g %g (%u comps)\\n",
              n, (unsigned long)prev_pf, prev_level, prev_w, prev_h, v[0], v[1], v[2], v[3], comps);
      last = now;
    }
    [prev_buf release];
    prev_buf = nil;
  }
  prev_buf = [dst retain];
  prev_off = offset;
  prev_level = level; prev_w = w; prev_h = h;
  prev_pf = src.pixelFormat;
  pthread_mutex_unlock(&lock);
}

static NTSTATUS
_MTLBlitCommandEncoder_encodeCommands(void *obj) {
"""),
    ("""      if (!body->options && !texture_upload_pitch_ok(src, body->size.width, body->bytes_per_row))
        break;
      [encoder copyFromTexture:src""",
     """      if (!body->options && !texture_upload_pitch_ok(src, body->size.width, body->bytes_per_row))
        break;
      if (madeira_lum_probe_on() && madeira_is_f32(src.pixelFormat) && body->size.width <= 4 &&
          body->size.height <= 4 && body->size.depth <= 1)   /* madeira-bcd: luminance probe */
        madeira_lum_note_copy(src, (id<MTLBuffer>)body->dst, body->offset, (unsigned)body->level,
                              (unsigned)body->size.width, (unsigned)body->size.height);
      [encoder copyFromTexture:src"""),
    ("""      struct wmtcmd_blit_generate_mipmaps *body = (struct wmtcmd_blit_generate_mipmaps *)next;
      [encoder generateMipmapsForTexture:(id<MTLTexture>)body->texture];""",
     """      struct wmtcmd_blit_generate_mipmaps *body = (struct wmtcmd_blit_generate_mipmaps *)next;
      if (madeira_lum_probe_on()) {   /* madeira-bcd: luminance probe */
        id<MTLTexture> t = (id<MTLTexture>)body->texture;
        static unsigned f32_mips;
        if (madeira_is_f32(t.pixelFormat) && (++f32_mips <= 8 || (f32_mips & 511) == 0))
          fprintf(stderr, "[f32-mipgen] madeira-bcd GenerateMips #%u fmt %lu %lux%lu levels %lu\\n", f32_mips,
                  (unsigned long)t.pixelFormat, (unsigned long)t.width, (unsigned long)t.height,
                  (unsigned long)t.mipmapLevelCount);
      }
      [encoder generateMipmapsForTexture:(id<MTLTexture>)body->texture];"""),
]


def main():
    s = PATH.read_text()
    if MARKER in s:
        print("winemetal_unix.c: already patched")
        return 0
    for old, new in PAIRS:
        if s.count(old) != 1:
            sys.exit(f"patch-winemetal-lum-probe: anchor found {s.count(old)} times (want 1) in {PATH}:\n{old}")
        s = s.replace(old, new)
    PATH.write_text(s)
    print("winemetal_unix.c: patched")
    return 0


if __name__ == "__main__":
    sys.exit(main())

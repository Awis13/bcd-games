#!/usr/bin/env python3
"""Census of what a game reads back from the GPU, with the values of small
readbacks. Builds on tools/patch-winemetal-lum-probe.py (run it first).

God of War's outdoor darkening (owner, build 279, log 14:51:20): the game makes
~27 texture-to-staging copies a second (stg<-tex 0 -> 3310 in two minutes), yet
none was a small 32-bit float texture and it never asked for 32-bit float
GenerateMips -- so the exposure value it reads is in another format or comes
from a buffer. The darkness does not depend on the camera (owner). This probe
records every readback so the next log names it:

  [rb-tex]  texture -> buffer copies: each new (format, size, level) the first
            time it appears, and every 1024th copy the six most frequent.
            For copies of <= 64 texels, the previous such copy's destination
            (CPU-visible, finished by then) is read and logged, throttled to
            ~2 lines/s: first 16 bytes as 4 floats and as 4 hex words.
  [rb-buf]  buffer -> buffer copies of <= 256 bytes into a CPU-visible buffer:
            same treatment (length census, values of the previous one: first
            8 floats).

MADEIRA_LUM_PROBE=0 turns this off too. Native only. Idempotent; fails by name
if an anchor moves. Run from the repository root.
"""
import pathlib
import sys

PATH = pathlib.Path("dxmt/src/winemetal/unix/winemetal_unix.c")
MARKER = "madeira-bcd: readback census"

HELPERS = r'''
/* madeira-bcd: readback census (tools/patch-winemetal-readback-census.py) */
struct madeira_rb_key { unsigned long fmt; unsigned w, h, level; unsigned long count; };
static pthread_mutex_t madeira_rb_lock = PTHREAD_MUTEX_INITIALIZER;
static int madeira_rb_due(struct timespec *last) {
  struct timespec now;
  clock_gettime(CLOCK_MONOTONIC, &now);
  if ((now.tv_sec - last->tv_sec) + (now.tv_nsec - last->tv_nsec) / 1e9 < 0.5) return 0;
  *last = now;
  return 1;
}
static void madeira_rb_tex(id<MTLTexture> src, unsigned level, unsigned w, unsigned h,
                           id<MTLBuffer> dst, uint64_t offset) {
  static struct madeira_rb_key keys[64];
  static unsigned nkeys;
  static unsigned long total;
  static id<MTLBuffer> prev; static uint64_t prev_off; static struct madeira_rb_key prev_key;
  static struct timespec last;
  unsigned long fmt = (unsigned long)src.pixelFormat;
  pthread_mutex_lock(&madeira_rb_lock);
  total++;
  unsigned i;
  for (i = 0; i < nkeys; i++)
    if (keys[i].fmt == fmt && keys[i].w == w && keys[i].h == h && keys[i].level == level) break;
  if (i == nkeys && nkeys < 64) {
    keys[nkeys] = (struct madeira_rb_key){fmt, w, h, level, 0};
    fprintf(stderr, "[rb-tex] madeira-bcd new readback #%lu: fmt %lu %ux%u level %u (texture %lux%lu, %lu mips)\n",
            total, fmt, w, h, level, (unsigned long)src.width, (unsigned long)src.height,
            (unsigned long)src.mipmapLevelCount);
    nkeys++;
  }
  if (i < nkeys) keys[i].count++;
  if ((total & 1023) == 0) {
    unsigned char done[64] = {0};
    fprintf(stderr, "[rb-tex] madeira-bcd %lu readbacks, most frequent:", total);
    for (unsigned k = 0; k < 6; k++) {
      unsigned best = nkeys;
      for (unsigned j = 0; j < nkeys; j++)
        if (!done[j] && keys[j].count && (best == nkeys || keys[j].count > keys[best].count)) best = j;
      if (best == nkeys) break;
      done[best] = 1;
      fprintf(stderr, " [fmt %lu %ux%u L%u x%lu]", keys[best].fmt, keys[best].w, keys[best].h,
              keys[best].level, keys[best].count);
    }
    fprintf(stderr, "\n");
  }
  if (w * h <= 64) {
    if (prev) {
      const uint8_t *base = (const uint8_t *)[prev contents];
      if (base && prev_off + 16 <= [prev length] && madeira_rb_due(&last)) {
        float f[4]; uint32_t u[4];
        memcpy(f, base + prev_off, 16); memcpy(u, base + prev_off, 16);
        fprintf(stderr, "[rb-tex] madeira-bcd value fmt %lu %ux%u L%u -> %g %g %g %g | %08x %08x %08x %08x\n",
                prev_key.fmt, prev_key.w, prev_key.h, prev_key.level, f[0], f[1], f[2], f[3], u[0], u[1], u[2], u[3]);
      }
      [prev release];
    }
    prev = [dst retain]; prev_off = offset;
    prev_key = (struct madeira_rb_key){fmt, w, h, level, 0};
  }
  pthread_mutex_unlock(&madeira_rb_lock);
}
static void madeira_rb_buf(id<MTLBuffer> dst, uint64_t offset, uint64_t length) {
  static id<MTLBuffer> prev; static uint64_t prev_off, prev_len;
  static unsigned long lens[16], counts[16];
  static unsigned nlens;
  static struct timespec last;
  if (length > 256 || dst.storageMode != MTLStorageModeShared) return;
  pthread_mutex_lock(&madeira_rb_lock);
  unsigned i;
  for (i = 0; i < nlens && lens[i] != length; i++) {}
  if (i == nlens && nlens < 16) {
    lens[nlens] = length; counts[nlens] = 0; nlens++;
    fprintf(stderr, "[rb-buf] madeira-bcd new small readback: %llu bytes\n", (unsigned long long)length);
  }
  if (i < nlens) counts[i]++;
  if (prev) {
    const uint8_t *base = (const uint8_t *)[prev contents];
    if (base && prev_off + 32 <= [prev length] && madeira_rb_due(&last)) {
      float f[8];
      memcpy(f, base + prev_off, 32);
      fprintf(stderr, "[rb-buf] madeira-bcd value %llu bytes -> %g %g %g %g %g %g %g %g\n",
              (unsigned long long)prev_len, f[0], f[1], f[2], f[3], f[4], f[5], f[6], f[7]);
    }
    [prev release];
  }
  prev = [dst retain]; prev_off = offset; prev_len = length;
  pthread_mutex_unlock(&madeira_rb_lock);
}

static NTSTATUS
_MTLBlitCommandEncoder_encodeCommands(void *obj) {
'''

PAIRS = [
    ("""
static NTSTATUS
_MTLBlitCommandEncoder_encodeCommands(void *obj) {
""", HELPERS),
    ("""        madeira_lum_note_copy(src, (id<MTLBuffer>)body->dst, body->offset, (unsigned)body->level,
                              (unsigned)body->size.width, (unsigned)body->size.height);
""",
     """        madeira_lum_note_copy(src, (id<MTLBuffer>)body->dst, body->offset, (unsigned)body->level,
                              (unsigned)body->size.width, (unsigned)body->size.height);
      if (madeira_lum_probe_on())   /* madeira-bcd: readback census */
        madeira_rb_tex(src, (unsigned)body->level, (unsigned)body->size.width, (unsigned)body->size.height,
                       (id<MTLBuffer>)body->dst, body->offset);
"""),
    ("""      wmt_stale_check(body->src, "blit copy src"); wmt_stale_check(body->dst, "blit copy dst");
      [encoder copyFromBuffer:(id<MTLBuffer>)body->src
                 sourceOffset:body->src_offset
                     toBuffer:(id<MTLBuffer>)body->dst""",
     """      wmt_stale_check(body->src, "blit copy src"); wmt_stale_check(body->dst, "blit copy dst");
      if (madeira_lum_probe_on())   /* madeira-bcd: readback census */
        madeira_rb_buf((id<MTLBuffer>)body->dst, body->dst_offset, body->copy_length);
      [encoder copyFromBuffer:(id<MTLBuffer>)body->src
                 sourceOffset:body->src_offset
                     toBuffer:(id<MTLBuffer>)body->dst"""),
]


def main():
    s = PATH.read_text()
    if MARKER in s:
        print("winemetal_unix.c: already patched")
        return 0
    if "madeira-bcd: luminance probe" not in s:
        sys.exit("patch-winemetal-readback-census: run tools/patch-winemetal-lum-probe.py first")
    for old, new in PAIRS:
        if s.count(old) != 1:
            sys.exit(f"patch-winemetal-readback-census: anchor found {s.count(old)} times (want 1) in {PATH}:\n{old}")
        s = s.replace(old, new)
    PATH.write_text(s)
    print("winemetal_unix.c: patched")
    return 0


if __name__ == "__main__":
    sys.exit(main())

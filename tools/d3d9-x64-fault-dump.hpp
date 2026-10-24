// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
//
// [fault-dump]: the portable core of the 64-bit guest fault report (ajan17-ds2, 2026-10-23).
//
// Why. Darksiders II Deathinitive Edition (Steam 388410, 64-bit Direct3D 9) gets through DXMT's Direct3D 9 device and its
// first intro video, then dies on `mov rax,[rcx]` / `call [rax+0x40]` with RCX = [R12] = 0 (build 595, iPad16,5). The session
// log has the registers as numbers and 16 code bytes at the faulting RIP. That does not say WHICH object was NULL, which class
// the code belongs to, or which call produced the value. This report prints, for the first access violations of the process,
// what is needed to read that off a log without the game's executable on the Mac:
//   - the exact RIP with module+RVA, the access, and every register that points at something (a module address; an object
//     whose vtable has MSVC RTTI, with the class name; text; the stack);
//   - the code around the RIP (256 bytes before, 96 after) as hex, so the function can be disassembled from the log;
//   - 48 stack qwords from RSP, each annotated, and the first four return-address candidates with the 96 code bytes before
//     each (the call sites that led here);
//   - the stack memory around the registers that point into the stack (a NULL "local" that a callee was meant to fill).
//
// Log only: it reads through a Reader that never faults and writes nothing. The Windows glue is d3d9-x64-fault-dump.cpp, which
// is compiled into d3d9-dxmt.dll (the ARM64EC DXMT Direct3D 9 build, tools/build-d3d9-x64-dll.sh) and nowhere else, and arms
// itself only when the game's environment has MADEIRA_D3D9_LAST=1. This header has no Windows or DXMT dependency so that
// tests/host/check-d3d9-fault-dump.py can compile it against fake memory.
//
// A Reader R provides
//   bool read(uint64_t addr, void *buf, size_t len)   all of len bytes, or false (never faults)
//   bool image(uint64_t addr, bcdfd::Image &out)      the loaded image that holds addr
#pragma once

#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <initializer_list>

namespace bcdfd {

struct Regs {
  uint64_t rax, rcx, rdx, rbx, rsp, rbp, rsi, rdi, r8, r9, r10, r11, r12, r13, r14, r15, rip;
};

struct Image {
  uint64_t base = 0;
  uint64_t size = 0;
  char name[40] = {};
};

inline bool plausible(uint64_t v) { return v >= 0x10000 && v < 0x800000000000ULL; }

// "module+0xRVA" when v lies inside a loaded image.
template <class R>
bool modRva(R &r, uint64_t v, char *out, size_t n) {
  Image im;
  if (!plausible(v) || !r.image(v, im) || v < im.base || v >= im.base + im.size)
    return false;
  snprintf(out, n, "%s+0x%llx", im.name, (unsigned long long)(v - im.base));
  return true;
}

// ".?AVOuter@Inner@@" -> "Inner::Outer". A name with a template or other decoration ('?') stays as the compiler wrote it,
// without the ".?AV" prefix.
inline void demangleRtti(const char *raw, char *out, size_t n) {
  const char *p = raw + 4;
  char seg[8][48];
  int k = 0;
  bool plain = true;
  for (const char *c = p; *c; c++)
    if (*c == '?')
      plain = false;
  if (plain) {
    while (*p && k < 8) {
      size_t len = 0;
      while (p[len] && p[len] != '@')
        len++;
      if (!len)
        break;
      snprintf(seg[k++], sizeof(seg[0]), "%.*s", (int)len, p);
      p += len;
      if (*p == '@')
        p++;
    }
    out[0] = 0;
    for (int i = k - 1; i >= 0; i--) {
      size_t used = strlen(out);
      snprintf(out + used, n - used, "%s%s", i == k - 1 ? "" : "::", seg[i]);
    }
    if (k)
      return;
  }
  snprintf(out, n, "%.*s", 60, raw + 4);
}

// The class of the object at obj: its first qword is a vtable inside an image, the qword before the vtable points at an MSVC
// x64 RTTI Complete Object Locator in the same image (signature 1, pSelf = its own RVA), whose type descriptor carries the
// decorated name. False when the object has no RTTI (the game was built without /GR) or is not an object.
template <class R>
bool rttiName(R &r, uint64_t obj, char *out, size_t n) {
  Image im;
  uint64_t vt = 0, col = 0;
  uint32_t c[6];
  char raw[96] = {};
  if (!plausible(obj) || (obj & 7) || !r.read(obj, &vt, 8) || !plausible(vt) || (vt & 7) || !r.image(vt, im))
    return false;
  if (vt < im.base + 8 || vt >= im.base + im.size || !r.read(vt - 8, &col, 8))
    return false;
  if (col < im.base || col + 24 > im.base + im.size || (col & 3) || !r.read(col, c, sizeof(c)))
    return false;
  if (c[0] != 1 || (uint64_t)c[5] != col - im.base)
    return false;
  uint64_t td = im.base + c[3];
  if (td < im.base || td + 24 > im.base + im.size)
    return false;
  if (!r.read(td + 16, raw, 64) && !r.read(td + 16, raw, 32) && !r.read(td + 16, raw, 12))
    return false;
  raw[sizeof(raw) - 1] = 0;
  if (strncmp(raw, ".?A", 3) != 0 || (raw[3] != 'V' && raw[3] != 'U' && raw[3] != 'T'))
    return false;
  demangleRtti(raw, out, n);
  return true;
}

// Printable text at addr: ASCII or UTF-16LE of ASCII characters, 4 or more, ended by a NUL (or 12+ characters that run to the
// end of what could be read). At most 40 characters are copied.
template <class R>
bool textAt(R &r, uint64_t addr, char *out, size_t n) {
  unsigned char b[48];
  size_t got = 0;
  for (size_t want : {sizeof(b), (size_t)24, (size_t)8})
    if (r.read(addr, b, want)) {
      got = want;
      break;
    }
  if (got < 8 || n < 48)
    return false;
  size_t len = 0;
  while (len < got && b[len] >= 0x20 && b[len] < 0x7f)
    len++;
  bool wide = false;
  size_t unit = 1;
  if (len < 4) {
    wide = true;
    unit = 2;
    len = 0;
    while (2 * len + 1 < got && b[2 * len] >= 0x20 && b[2 * len] < 0x7f && !b[2 * len + 1])
      len++;
  }
  bool ended = len * unit + unit <= got && !b[len * unit] && (!wide || !b[len * unit + 1]);
  bool window = len * unit + unit > got;
  if (len < 4 || (!ended && !(window && len >= 12)))
    return false;
  size_t i = 0;
  for (; i < len && i < 40; i++)
    out[i] = (char)b[i * unit];
  out[i] = 0;
  return true;
}

// What a value points at, short: "mod+0x..", "rsp+0x..", "obj<Class>", "[v]=mod+0x..", "\"text\"". False when nothing.
template <class R>
bool describe(R &r, uint64_t v, uint64_t rsp, uint64_t stackLo, uint64_t stackHi, char *out, size_t n) {
  char tmp[96], q[96];
  uint64_t first = 0;
  out[0] = 0;
  if (!plausible(v))
    return false;
  if (modRva(r, v, tmp, sizeof(tmp))) {
    snprintf(out, n, "%s", tmp);
    return true;
  }
  if (v >= stackLo && v < stackHi) {
    if (v >= rsp)
      snprintf(out, n, "rsp+0x%llx", (unsigned long long)(v - rsp));
    else
      snprintf(out, n, "rsp-0x%llx", (unsigned long long)(rsp - v));
    return true;
  }
  if (rttiName(r, v, tmp, sizeof(tmp))) {
    snprintf(out, n, "obj<%s>", tmp);
    return true;
  }
  if (r.read(v, &first, 8) && modRva(r, first, tmp, sizeof(tmp))) {
    snprintf(out, n, "[v]=%s", tmp);
    return true;
  }
  if (textAt(r, v, q, sizeof(q))) {
    snprintf(out, n, "\"%s\"", q);
    return true;
  }
  return false;
}

// A call instruction ends right before ra: the shapes of `call rel32`, `call [rip+d32]`, `call r`, `call [r]`, `call [r+d8]`,
// `call [r+d32]` and the same with a SIB byte. A hint, not a proof.
template <class R>
bool isReturnAddress(R &r, uint64_t ra) {
  unsigned char b[16];
  if (ra < 0x10010 || !r.read(ra - 16, b, 16))
    return false;
  if (b[11] == 0xE8)
    return true;
  if (b[10] == 0xFF && b[11] == 0x15)
    return true;
  if (b[14] == 0xFF && ((b[15] & 0xF8) == 0xD0 || ((b[15] & 0xF8) == 0x10 && (b[15] & 7) != 4 && (b[15] & 7) != 5)))
    return true;
  if (b[13] == 0xFF && (b[14] & 0xF8) == 0x50 && (b[14] & 7) != 4) // call [r+d8]
    return true;
  if (b[13] == 0xFF && b[14] == 0x14) // call [sib]
    return true;
  if (b[12] == 0xFF && b[13] == 0x54) // call [sib+d8]
    return true;
  if (b[10] == 0xFF && (b[11] & 0xF8) == 0x90 && (b[11] & 7) != 4) // call [r+d32]
    return true;
  if (b[9] == 0xFF && b[10] == 0x94) // call [sib+d32]
    return true;
  return false;
}

inline void hexRow(char *out, size_t n, const unsigned char *b, size_t len) {
  size_t used = 0;
  out[0] = 0;
  for (size_t i = 0; i < len && used + 4 < n; i++)
    used += snprintf(out + used, n - used, i ? " %02x" : "%02x", b[i]);
}

template <class R, class Emit>
void codeRows(R &r, Emit &emit, const char *what, uint64_t from, unsigned rows) {
  for (unsigned i = 0; i < rows; i++) {
    unsigned char b[32];
    char hex[128], line[256];
    uint64_t a = from + 32ULL * i;
    if (r.read(a, b, sizeof(b))) {
      hexRow(hex, sizeof(hex), b, sizeof(b));
      snprintf(line, sizeof(line), "%s 0x%llx: %s", what, (unsigned long long)a, hex);
    } else {
      snprintf(line, sizeof(line), "%s 0x%llx: unreadable", what, (unsigned long long)a);
    }
    emit(line);
  }
}

// qword rows (4 per row) from `from`, then one line per annotated qword. `label` names the base ("rsp", "r12", ...).
template <class R, class Emit>
void qwordRows(R &r, Emit &emit, const char *label, uint64_t base, uint64_t from, unsigned count, uint64_t rsp, uint64_t lo,
               uint64_t hi, bool markRet) {
  char line[400], note[160];
  for (unsigned i = 0; i < count; i += 4) {
    uint64_t q[4] = {0, 0, 0, 0};
    unsigned have = 0;
    for (unsigned j = 0; j < 4 && i + j < count; j++)
      if (r.read(from + 8ULL * (i + j), &q[j], 8))
        have++;
    long long off = (long long)(from + 8ULL * i) - (long long)base;
    snprintf(line, sizeof(line), "%s%s0x%llx: %016llx %016llx %016llx %016llx%s", label, off < 0 ? "-" : "+",
             (unsigned long long)(off < 0 ? -off : off), (unsigned long long)q[0], (unsigned long long)q[1],
             (unsigned long long)q[2], (unsigned long long)q[3], have == 4 || i + 4 > count ? "" : " (partly unreadable)");
    emit(line);
  }
  for (unsigned i = 0; i < count; i++) {
    uint64_t v = 0;
    long long off = (long long)(from + 8ULL * i) - (long long)base;
    if (!r.read(from + 8ULL * i, &v, 8) || !describe(r, v, rsp, lo, hi, note, sizeof(note)))
      continue;
    bool ret = markRet && isReturnAddress(r, v);
    snprintf(line, sizeof(line), "  %s%s0x%llx = 0x%llx  %s%s", label, off < 0 ? "-" : "+",
             (unsigned long long)(off < 0 ? -off : off), (unsigned long long)v, note, ret ? "  <== return address?" : "");
    emit(line);
  }
}

// The whole report. `code`: ExceptionCode; `access`: ExceptionInformation[0] (0 read, 1 write, 8 execute); `addr`: [1].
// `lo`/`hi`: the thread's stack limit and base. `emit(const char *line)` receives one line without prefix or newline.
template <class R, class Emit>
void dumpFault(R &r, const Regs &g, unsigned seq, unsigned tid, uint32_t code, uint64_t access, uint64_t addr, uint64_t lo,
               uint64_t hi, Emit emit) {
  char line[512], note[160], mod[96];
  const struct {
    const char *name;
    uint64_t v;
  } regs[] = {
      {"rax", g.rax}, {"rcx", g.rcx}, {"rdx", g.rdx}, {"rbx", g.rbx}, {"rbp", g.rbp}, {"rsi", g.rsi}, {"rdi", g.rdi}, {"r8", g.r8},
      {"r9", g.r9},   {"r10", g.r10}, {"r11", g.r11}, {"r12", g.r12}, {"r13", g.r13}, {"r14", g.r14}, {"r15", g.r15},
  };
  const unsigned nregs = sizeof(regs) / sizeof(regs[0]);

  if (!modRva(r, g.rip, mod, sizeof(mod)))
    snprintf(mod, sizeof(mod), "no module");
  snprintf(line, sizeof(line),
           "rev=ajan17-ds2 #%u tid=%04x exception 0x%08x, %s of 0x%llx at rip=0x%llx (%s) rsp=0x%llx stack=[0x%llx,0x%llx)", seq,
           tid, (unsigned)code, access == 0 ? "read" : access == 1 ? "write" : access == 8 ? "execute" : "access",
           (unsigned long long)addr, (unsigned long long)g.rip, mod, (unsigned long long)g.rsp, (unsigned long long)lo,
           (unsigned long long)hi);
  emit(line);

  // Registers: one line each when they point at something, the rest in one line.
  char plain[256] = "";
  size_t pl = 0;
  for (unsigned i = 0; i < nregs; i++) {
    if (describe(r, regs[i].v, g.rsp, lo, hi, note, sizeof(note))) {
      snprintf(line, sizeof(line), "%s=0x%llx  %s", regs[i].name, (unsigned long long)regs[i].v, note);
      emit(line);
    } else if (pl + 28 < sizeof(plain)) {
      pl += snprintf(plain + pl, sizeof(plain) - pl, " %s=0x%llx", regs[i].name, (unsigned long long)regs[i].v);
    }
  }
  snprintf(line, sizeof(line), "plain registers:%s", plain);
  emit(line);

  // The code that faulted, as hex: 256 bytes before the RIP (the rows end exactly at the RIP) and 96 after.
  if (plausible(g.rip)) {
    emit("code before and at the faulting rip (the row that starts at rip is the faulting instruction):");
    codeRows(r, emit, "code", g.rip - 256, 11);
  }

  // The stack from RSP: 48 qwords.
  qwordRows(r, emit, "rsp", g.rsp, g.rsp, 48, g.rsp, lo, hi, true);

  // The call sites behind the first four return-address candidates that lie in an image.
  unsigned sites = 0;
  for (unsigned i = 0; i < 48 && sites < 4; i++) {
    uint64_t ra = 0;
    if (!r.read(g.rsp + 8ULL * i, &ra, 8) || !modRva(r, ra, mod, sizeof(mod)) || !isReturnAddress(r, ra))
      continue;
    snprintf(line, sizeof(line), "call site #%u: return address 0x%llx = %s, from [rsp+0x%x]; 96 bytes before it and 32 after:", sites + 1,
             (unsigned long long)ra, mod, i * 8);
    emit(line);
    codeRows(r, emit, "site", ra - 96, 4);
    sites++;
  }

  // Locals: the stack memory around registers that point into the stack (a NULL out-parameter slot is [r12] here).
  const char *order[] = {"r12", "rbp", "r13", "r14", "r15", "rbx", "rsi", "rdi", "rdx", "rcx"};
  uint64_t done[4] = {0, 0, 0, 0};
  unsigned ndone = 0;
  for (const char *want : order) {
    if (ndone >= 3)
      break;
    for (unsigned i = 0; i < nregs; i++) {
      if (strcmp(regs[i].name, want) != 0)
        continue;
      uint64_t v = regs[i].v;
      if (v < g.rsp || v >= hi || v < lo)
        break;
      bool dup = false;
      for (unsigned k = 0; k < ndone; k++)
        if (done[k] == v)
          dup = true;
      if (dup)
        break;
      done[ndone++] = v;
      uint64_t one = 0;
      bool readable = r.read(v, &one, 8);
      snprintf(line, sizeof(line), "stack around %s=0x%llx (rsp+0x%llx); [%s] = %s0x%llx:", want, (unsigned long long)v,
               (unsigned long long)(v - g.rsp), want, readable ? "" : "unreadable ", (unsigned long long)one);
      emit(line);
      uint64_t from = (v & ~7ULL) - 0x60; // rows of 4 qwords: the slot itself starts the fourth row
      qwordRows(r, emit, want, v, from < g.rsp ? g.rsp : from, 24, g.rsp, lo, hi, true);
      break;
    }
  }

  // Objects: the first eight qwords of up to four registers that are objects with a vtable (the class name above).
  unsigned objs = 0;
  for (unsigned i = 0; i < nregs && objs < 4; i++) {
    if (!rttiName(r, regs[i].v, note, sizeof(note)))
      continue;
    snprintf(line, sizeof(line), "object %s=0x%llx obj<%s>, first 8 qwords:", regs[i].name, (unsigned long long)regs[i].v, note);
    emit(line);
    qwordRows(r, emit, regs[i].name, regs[i].v, regs[i].v, 8, g.rsp, lo, hi, false);
    objs++;
  }
  emit("end");
}

} // namespace bcdfd

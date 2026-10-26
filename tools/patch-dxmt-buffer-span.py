#!/usr/bin/env python3
"""Span refresh for D3D9 NOOVERWRITE buffer locks (opt-in, DXMT_D9_BUFFER_SPAN).

Grand Theft Auto IV on the native D3D9 frontend spends 38% of its running time
in memmove under MTLD3D9VertexBuffer::refreshWholeMirror ([rip-profile],
iPad16,10, 2026-10-26): it appends a few hundred bytes at a time to large
dynamic vertex buffers with D3DLOCK_NOOVERWRITE, 78 locks a frame, and every
draw after such a lock renames the buffer and copies the WHOLE host mirror into
the new generation. The render thread is then bound to one core at full clock,
and 60 FPS falls to 25 as soon as iOS lowers the performance-core clock.

NOOVERWRITE is the application's promise that it does not touch bytes a draw
still in flight reads, which is what lets wined3d and DXVK skip both the rename
and the copy. With DXMT_D9_BUFFER_SPAN set, a refresh whose locks since the
last refresh were ALL NOOVERWRITE skips the rename and stores only the locked
span into the current generation:

  DXMT_D9_BUFFER_SPAN=1  the union of the declared lock ranges;
  DXMT_D9_BUFFER_SPAN=2  from the lowest lock offset to the end of the buffer
                         (covers an application that writes past the range it
                         declared -- refreshWholeMirror's comment measured
                         36.6% of NOOVERWRITE refreshes doing so).

A DISCARD or plain write lock, the creation-time dirty state, and a lock of size
0 keep the existing rename + whole-mirror path. Unset or 0: unchanged. Applies
to vertex and index buffers. [buffer-span] logs the mode once.

Idempotent; fails by name if an anchor moves. Run from the repository root.
"""
import pathlib
import sys

ROOT = pathlib.Path("dxmt/src")
MARKER = "madeira-bcd: NOOVERWRITE span refresh"


def edit(rel, pairs):
    path = ROOT / rel
    s = path.read_text()
    if MARKER in s:
        print(f"{rel}: already patched")
        return
    for old, new, count in pairs:
        if s.count(old) != count:
            sys.exit(f"patch-dxmt-buffer-span: anchor found {s.count(old)} times (want {count}) in {rel}:\n{old}")
        s = s.replace(old, new)
    if MARKER not in s:
        sys.exit(f"patch-dxmt-buffer-span: marker missing after editing {rel}")
    path.write_text(s)
    print(f"{rel}: patched")


# Both buffer classes get the same span state, declared next to m_writeLocked.
FIELDS = """  /* madeira-bcd: NOOVERWRITE span refresh (tools/patch-dxmt-buffer-span.py).
   * The locked bytes since the last refresh, and whether every write lock in
   * that time was NOOVERWRITE with a known extent. */
  UINT m_spanBegin = 0;
  UINT m_spanEnd = 0;
  bool m_spanOnly = true;
  void noteWriteLock(UINT offset, UINT size, DWORD flags);
  bool refreshSpanInPlace();
"""

edit("d3d9/d3d9_buffer.hpp", [
    ("""  bool m_dirty = false;
  bool m_writeLocked = false;
""",
     """  bool m_dirty = false;
  bool m_writeLocked = false;
""" + FIELDS, 1),
    ("""  bool m_writeLocked = false;
  D3D9BufferLockCount m_lockCount;
""",
     """  bool m_writeLocked = false;
""" + FIELDS + """  D3D9BufferLockCount m_lockCount;
""", 1),
])

HELPERS = """
/* madeira-bcd: NOOVERWRITE span refresh (tools/patch-dxmt-buffer-span.py). */
static int
madeira_buffer_span_mode() {
  static const int mode = [] {
    std::string v = env::getEnvVar("DXMT_D9_BUFFER_SPAN");
    int m = v.empty() ? 0 : std::atoi(v.c_str());
    if (m < 0 || m > 2)
      m = 0;
    Logger::info(str::format("[buffer-span] mode ", m,
                             m == 0 ? " (off: every refresh copies the whole mirror)"
                             : m == 1 ? " (NOOVERWRITE locks store their declared span in place)"
                                      : " (NOOVERWRITE locks store from their offset to the buffer end in place)"));
    return m;
  }();
  return mode;
}

/* Record one write lock. `was_dirty` is the buffer's dirty flag before the
 * lock: a buffer that was already dirty for a reason other than a span (its
 * creation, a DISCARD or plain lock) stays on the whole-mirror path. */
static void
madeira_note_write_lock(UINT &begin, UINT &end, bool &span_only, bool was_dirty, UINT buffer_size,
                        UINT offset, UINT size, DWORD flags) {
  const int mode = madeira_buffer_span_mode();
  if (!mode || !(flags & D3DLOCK_NOOVERWRITE) || (flags & D3DLOCK_DISCARD) || size == 0 || offset >= buffer_size) {
    span_only = false;
    return;
  }
  if (was_dirty && begin == end)
    span_only = false; /* dirty with no span recorded: not ours to narrow */
  UINT lo = offset;
  UINT hi = mode == 2 ? buffer_size : (size > buffer_size - offset ? buffer_size : offset + size);
  if (begin == end) {
    begin = lo;
    end = hi;
  } else {
    if (lo < begin)
      begin = lo;
    if (hi > end)
      end = hi;
  }
}
"""

VB_LOCK_OLD = """  if (buffer_lock_updates_dirty(Flags)) {
    m_dirty = true;
    m_writeLocked = true;
  }
  m_lockCount.increment();
  *ppbData = static_cast<char *>(m_hostPtr) + OffsetToLock;
  return D3D_OK;
}"""
VB_LOCK_NEW = """  if (buffer_lock_updates_dirty(Flags)) {
    noteWriteLock(OffsetToLock, SizeToLock, Flags);
    m_dirty = true;
    m_writeLocked = true;
  }
  m_lockCount.increment();
  *ppbData = static_cast<char *>(m_hostPtr) + OffsetToLock;
  return D3D_OK;
}"""

REFRESH_HEAD_VB = """void
MTLD3D9VertexBuffer::refreshWholeMirror() {
  if (!m_dirty)
    return;
"""
REFRESH_HEAD_IB = """void
MTLD3D9IndexBuffer::refreshWholeMirror() {
  if (!m_dirty)
    return;
"""


def span_methods(cls):
    return f"""void
{cls}::noteWriteLock(UINT offset, UINT size, DWORD flags) {{
  madeira_note_write_lock(m_spanBegin, m_spanEnd, m_spanOnly, m_dirty, m_size, offset, size, flags);
}}

/* The NOOVERWRITE case of the refresh: no rename (the application promised not
 * to touch what an unfinished draw reads), and only the locked span goes into
 * the current generation. Returns false when the whole-mirror path must run. */
bool
{cls}::refreshSpanInPlace() {{
  if (!madeira_buffer_span_mode() || !m_spanOnly || m_spanBegin >= m_spanEnd || m_spanEnd > m_size)
    return false;
  m_dynamic->immediateName()->updateContents(
      m_spanBegin, static_cast<char *>(m_hostPtr) + m_spanBegin, m_spanEnd - m_spanBegin,
      m_dynamic->immediateSuballocation()
  );
  m_dirty = m_writeLocked;
  if (!m_dirty) {{
    m_spanBegin = m_spanEnd = 0;
    m_spanOnly = true;
  }}
  return true;
}}

"""


RESET_SPAN = """  m_dirty = m_writeLocked;
  if (!m_dirty) {
    m_spanBegin = m_spanEnd = 0;
    m_spanOnly = true;
  }
}"""

edit("d3d9/d3d9_buffer.cpp", [
    ("""#include "log/log.hpp"
""",
     """#include "log/log.hpp"
#include "util_env.hpp"    /* madeira-bcd: NOOVERWRITE span refresh */
#include "util_string.hpp"
#include <cstdlib>
#include <string>
""", 1),
    # helpers before the first class body
    ("""void
MTLD3D9VertexBuffer::refreshWholeMirror() {""",
     HELPERS + "\n" + span_methods("MTLD3D9VertexBuffer") + """void
MTLD3D9VertexBuffer::refreshWholeMirror() {""", 1),
    (REFRESH_HEAD_VB,
     REFRESH_HEAD_VB + """  if (refreshSpanInPlace())
    return;
""", 1),
    (REFRESH_HEAD_IB.replace("void\nMTLD3D9IndexBuffer::refreshWholeMirror() {", "void\nMTLD3D9IndexBuffer::refreshWholeMirror() {"),
     span_methods("MTLD3D9IndexBuffer") + REFRESH_HEAD_IB + """  if (refreshSpanInPlace())
    return;
""", 1),
    # both whole-mirror refreshes end the same way; clear the span there too
    ("""  m_dirty = m_writeLocked;
}""", RESET_SPAN, 2),
    # both Lock bodies record the span
    (VB_LOCK_OLD, VB_LOCK_NEW, 2),
])

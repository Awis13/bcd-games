#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""Find the function of an [int3-x86] report inside a PE file (the public CEF libcef.dll, or Blizzard's own when the file is at hand).

The report (build/ntdll-unix/ios_int3_x86_diag.h; env.MADEIRA_INT3_X86_DUMP = 1, Battle.net's launcher entry) prints the code bytes
around a 32-bit guest's int3 and before every return address of the EBP chain. This tool reads those rows from a BCD session log and looks
for the same instructions in a PE32 file whose symbols are known. Two builds of the same Chromium source differ in absolute addresses
(base relocations) and relative calls (rel32), so the match is by VOTE: every 8-byte chunk of the window that is fully readable and not
trivial is searched exactly, each hit votes for the file offset where the window would start, and the offset with the most votes wins. The
winner is then scored byte by byte; the bytes that differ are the relocated operands.

    tools/int3x86-match.py SESSION.log --pe libcef.dll [--report 1] [--section .text] [--top 3]
    tools/int3x86-match.py --selftest

For the best offset of each window it prints the RVA in the PE file, the votes, the share of equal bytes, and the nearest function start
before it (the previous 16-byte aligned `55 89 e5` / `55 8b ec` after 0xcc padding: a hint, not a symbol). A symbol (PDB) lookup of that RVA names it.
"""
import argparse
import re
import struct
import sys


def pe_sections(data):
    if data[:2] != b'MZ':
        raise SystemExit('not a PE file (no MZ)')
    nt = struct.unpack_from('<I', data, 0x3c)[0]
    if data[nt:nt + 4] != b'PE\0\0':
        raise SystemExit('not a PE file (no PE signature)')
    nsec, = struct.unpack_from('<H', data, nt + 6)
    optsz, = struct.unpack_from('<H', data, nt + 20)
    image_base, = struct.unpack_from('<I', data, nt + 24 + 28)
    sec = nt + 24 + optsz
    out = []
    for i in range(nsec):
        name, vsize, va, rawsize, rawptr = struct.unpack_from('<8sIIII', data, sec + 40 * i)
        out.append((name.rstrip(b'\0').decode('latin1'), va, vsize, rawptr, rawsize))
    return image_base, out


def rva_of(secs, off):
    for _, va, vsize, rawptr, rawsize in secs:
        if rawptr <= off < rawptr + rawsize:
            return va + off - rawptr
    return None


def parse_windows(text, report):
    """The code windows of report number `report`: {tag: (guest address of the first byte, bytes with None for ??)}"""
    wins = {}
    in_report = False
    head = None
    rows = {}
    for line in text.splitlines():
        if not line.startswith('[int3-x86]'):
            continue
        m = re.match(r'\[int3-x86\] rev=\S+ #(\d+) ', line)
        if m:
            in_report = int(m.group(1)) == report
            continue
        if not in_report:
            continue
        m = re.match(r'\[int3-x86\]   code (\S+): (\d+) bytes before and (\d+) after guest 0x([0-9a-f]+).*rows are 32 bytes, the first at 0x([0-9a-f]+)', line)
        if m:
            head = m.group(1)
            rows[head] = {'first': int(m.group(5), 16), 'at': int(m.group(4), 16), 'before': int(m.group(2)), 'after': int(m.group(3)), 'bytes': {}}
            continue
        m = re.match(r'\[int3-x86\]   code (\S+) ([+-]\d+): ((?:[0-9a-f?]{2} ?)+)$', line)
        if m and m.group(1) in rows:
            r = rows[m.group(1)]
            start = int(m.group(2)) + r['before']
            for i, b in enumerate(m.group(3).split()):
                r['bytes'][start + i] = None if b == '??' else int(b, 16)
    for tag, r in rows.items():
        n = r['before'] + r['after']
        wins[tag] = (r['first'], r['at'], [r['bytes'].get(i) for i in range(n)])
    return wins


def vote(data, lo, hi, window, top, max_hits=4000):
    """Offsets in data[lo:hi] where `window` (list of byte-or-None) best fits, by exact 8-byte anchors."""
    votes = {}
    n = len(window)
    for o in range(0, n - 8 + 1, 4):
        chunk = window[o:o + 8]
        if any(b is None for b in chunk) or len(set(chunk)) < 5:
            continue
        pat = bytes(chunk)
        pos = lo
        hits = 0
        while hits < max_hits:
            p = data.find(pat, pos, hi)
            if p < 0:
                break
            votes[p - o] = votes.get(p - o, 0) + 1
            pos = p + 1
            hits += 1
    best = sorted(votes.items(), key=lambda kv: -kv[1])[:top]
    res = []
    for off, v in best:
        if off < lo:
            continue
        known = [(i, b) for i, b in enumerate(window) if b is not None and off + i < hi]
        same = sum(1 for i, b in known if data[off + i] == b)
        res.append((off, v, same, len(known)))
    return res


def function_hint(data, off, lo, limit=0x40000):
    """A hint for the function start: the nearest 16-byte aligned `55 89 e5` / `55 8b ec` (push ebp; mov ebp, esp; the public CEF 108 x86
    build uses the first) at or before off whose previous byte is 0xcc padding (clang-cl pads functions with int3). Cold CHECK blocks sit
    after the function's ret, inside the same function."""
    a = off & ~0xf
    stop = max(lo, off - limit)
    while a >= stop + 1:
        if data[a:a + 3] in (b'\x55\x8b\xec', b'\x55\x89\xe5') and data[a - 1] == 0xcc:
            return a
        a -= 16
    return None


def selftest():
    import random
    rnd = random.Random(18)
    text = bytearray(rnd.randrange(256) for _ in range(300000))
    # a function: int3 padding, a 16-aligned prologue, a body with operands that differ between the two builds, a CHECK block
    pos = 123456 - (123456 % 16)
    text[pos - 5:pos] = b'\xcc' * 5
    body = b'\x55\x8b\xec\x53\x56' + bytes(rnd.randrange(256) for _ in range(120)) + b'\xcc\x0f\x0b' + bytes(rnd.randrange(256) for _ in range(40))
    text[pos:pos + len(body)] = body
    int3 = pos + 5 + 120
    other = bytearray(text)                                                          # "Blizzard's" build: the same function with other operands
    for i in (30, 31, 32, 33, 70, 71, 72, 73):
        other[pos + 5 + i] ^= 0x5a
    # the report as the engine prints it, from the OTHER build, with an unreadable first row
    before, after = 256, 32
    win = bytes(other[int3 - before:int3 + after])
    log = ['[int3-x86] rev=ajan18-bnet-cef #1 a 32-bit guest executed int3 at guest 0x%08x (libcef.dll+0x%x), window 0x5000000000, 3 modules' % (0x6c7b0000 + 0x1000 + int3, 0x1000 + int3),
           '[int3-x86]   code int3: %d bytes before and %d after guest 0x%08x (libcef.dll+0x%x) (288 of 288 readable); rows are 32 bytes, the first at 0x%08x'
           % (before, after, 0x6c7b0000 + 0x1000 + int3, 0x1000 + int3, 0x6c7b0000 + 0x1000 + int3 - before)]
    for r in range(0, len(win), 32):
        row = ' '.join('%02x' % b if r > 0 else '??' for b in win[r:r + 32])
        log.append('[int3-x86]   code int3 %+d: %s' % (r - before, row))
    log.append('[int3-x86]   end of report #1')
    wins = parse_windows('\n'.join(log), 1)
    assert 'int3' in wins and len(wins['int3'][2]) == 288 and wins['int3'][2][0] is None and wins['int3'][2][40] == win[40], 'parse'
    res = vote(bytes(text), 0, len(text), wins['int3'][2], 3)
    assert res and res[0][0] == int3 - before, 'vote %r (want %d)' % (res[:2], int3 - before)
    assert res[0][2] / res[0][3] > 0.95, 'score %r' % (res[0],)
    h = function_hint(bytes(text), int3, 0)
    assert h == pos, 'hint %r (want %d)' % (h, pos)
    print('selftest ok: window found at +0x%x with %d votes, %d of %d bytes equal, function hint +0x%x' % (res[0][0], res[0][1], res[0][2], res[0][3], h))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('log', nargs='?')
    ap.add_argument('--pe')
    ap.add_argument('--report', type=int, default=1)
    ap.add_argument('--section', default='.text')
    ap.add_argument('--top', type=int, default=3)
    ap.add_argument('--selftest', action='store_true')
    a = ap.parse_args()
    if a.selftest:
        selftest()
        return
    if not a.log or not a.pe:
        ap.error('LOG and --pe are required')
    text = open(a.log, errors='replace').read()
    data = open(a.pe, 'rb').read()
    base, secs = pe_sections(data)
    sec = next((s for s in secs if s[0] == a.section), None)
    if not sec:
        raise SystemExit('no section %s in %s' % (a.section, a.pe))
    lo, hi = sec[3], sec[3] + sec[4]
    wins = parse_windows(text, a.report)
    if not wins:
        raise SystemExit('no code windows of report #%d in the log' % a.report)
    for tag, (first, at, window) in sorted(wins.items()):
        known = sum(1 for b in window if b is not None)
        print('window %s: %d bytes (%d readable) around guest 0x%08x' % (tag, len(window), known, at))
        res = vote(data, lo, hi, window, a.top)
        if not res:
            print('   no anchor chunk matched anywhere in %s' % a.section)
            continue
        for off, v, same, n in res:
            at_off = off + (at - first)
            hint = function_hint(data, at_off, lo)
            diffs = [i for i, b in enumerate(window) if b is not None and off + i < hi and data[off + i] != b]
            print('   file offset 0x%x  RVA of the window start 0x%x  RVA of the address 0x%x  votes %d  equal %d/%d (%.0f%%)  function hint RVA %s'
                  % (off, rva_of(secs, off) or 0, rva_of(secs, at_off) or 0, v, same, n, 100.0 * same / max(n, 1),
                     ('0x%x' % rva_of(secs, hint)) if hint else 'none'))
            if diffs:
                print('      differing bytes at window offsets: %s' % ' '.join('%d' % i for i in diffs[:40]) + (' ...' if len(diffs) > 40 else ''))


if __name__ == '__main__':
    main()

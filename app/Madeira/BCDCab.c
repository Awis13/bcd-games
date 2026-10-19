// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
//
//  BCDCab.c
//
//  Cabinet reader (BCDCab.h), written from Microsoft's Cabinet File Format
//  specification. The layout it reads, all integers little-endian:
//
//    CFHEADER  "MSCF", reserved1, cbCabinet, reserved2, coffFiles, reserved3,
//              versionMinor (3), versionMajor (1), cFolders, cFiles, flags,
//              setID, iCabinet; then, with flags & RESERVE_PRESENT, cbCFHeader
//              (u16), cbCFFolder (u8), cbCFData (u8) and cbCFHeader bytes of
//              reserved data (Authenticode-signed cabinets carry 20 there).
//    CFFOLDER  coffCabStart (its first CFDATA), cCFData, typeCompress, then
//              cbCFFolder reserved bytes; cFolders of them.
//    CFFILE    cbFile, uoffFolderStart, iFolder, date, time, attribs, szName;
//              cFiles of them from coffFiles.
//    CFDATA    csum, cbData, cbUncomp, cbCFData reserved bytes, cbData bytes;
//              cCFData of them per folder, one after another.
//
//  A folder is one stream: its files are ranges of the concatenated output of
//  its blocks. An MSZIP block is "CK" and a raw deflate stream that ends in a
//  final block and inflates to cbUncomp (at most 32 KB) bytes; it may refer
//  back into the previous 32 KB of the folder's output, so each block is
//  inflated with that output as zlib's preset dictionary.

#define ZLIB_CONST
#include "BCDCab.h"

#include <stdlib.h>
#include <string.h>
#include <zlib.h>

enum {
    HEADER_SIZE = 36,              /* CFHEADER up to iCabinet */
    FOLDER_SIZE = 8,               /* CFFOLDER without its reserved bytes */
    FILE_SIZE = 16,                /* CFFILE without its name */
    DATA_SIZE = 8,                 /* CFDATA header without its reserved bytes */
    BLOCK_MAX = 32768,             /* the most a CFDATA block holds uncompressed; MSZIP's window */
    NAME_BYTES = 256,              /* szName with its NUL */
    HEADER_RESERVE_MAX = 60000,    /* cbCFHeader's limit in the specification */
};

enum { FLAG_PREV_CABINET = 0x0001, FLAG_NEXT_CABINET = 0x0002, FLAG_RESERVE_PRESENT = 0x0004 };
enum { COMPRESS_NONE = 0x0000, COMPRESS_MSZIP = 0x0001 };
enum { FOLDER_CONTINUED = 0xFFFD };   /* iFolder from here up: the file continues across cabinets */

typedef struct {
    uint32_t first;                /* offset of its first CFDATA */
    uint32_t blocks;               /* cCFData */
    uint32_t type;                 /* typeCompress */
    uint64_t usize;                /* the sum of its blocks' cbUncomp */
} cab_folder;

typedef struct {
    uint32_t size;                 /* cbFile */
    uint32_t offset;               /* uoffFolderStart */
    uint32_t folder;               /* iFolder */
    const char *name;              /* into the cabinet's bytes; NUL-terminated there */
} cab_file;

struct bcd_cab {
    const uint8_t *data;
    uint32_t size;                 /* cbCabinet */
    uint32_t data_reserve;         /* cbCFData */
    uint32_t nfolders, nfiles;
    cab_folder *folders;
    cab_file *files;

    /* The folder being decoded, block by block. */
    int folder;                    /* -1: none */
    uint32_t next_block;           /* the next block to decode */
    uint32_t next_offset;          /* where its CFDATA starts */
    uint64_t out_start;            /* folder offset of out[0] */
    uint32_t out_len;              /* bytes in out: the last block decoded */
    uint32_t hist_len;             /* bytes in hist: the folder's last output, up to 32 KB */
    int zready;
    z_stream z;
    uint8_t out[BLOCK_MAX];
    uint8_t hist[BLOCK_MAX];
};

static uint16_t get16(const uint8_t *p)
{
    return (uint16_t)(p[0] | p[1] << 8);
}

static uint32_t get32(const uint8_t *p)
{
    return (uint32_t)p[0] | (uint32_t)p[1] << 8 | (uint32_t)p[2] << 16 | (uint32_t)p[3] << 24;
}

/* The specification's CFDATA checksum: the bytes as little-endian 32-bit words
 * XORed together, the one to three bytes left over combined first byte highest. */
static uint32_t checksum(const uint8_t *p, size_t n, uint32_t seed)
{
    uint32_t sum = seed, rest = 0;
    size_t words = n / 4;
    for (size_t i = 0; i < words; i++)
        sum ^= get32(p + 4 * i);
    for (size_t i = 4 * words; i < n; i++)
        rest = rest << 8 | p[i];
    return sum ^ rest;
}

/* The fixed header's own consistency, before anything else is read. */
static int header_plausible(const uint8_t *h, size_t avail)
{
    if (avail < HEADER_SIZE || memcmp(h, "MSCF", 4) != 0)
        return 0;
    if (get32(h + 4) != 0 || get32(h + 12) != 0 || get32(h + 20) != 0)   /* reserved1-3 */
        return 0;
    if (h[24] != 3 || h[25] != 1)                                         /* version 1.3 */
        return 0;
    uint32_t cb = get32(h + 8), files_at = get32(h + 16);
    if (cb < HEADER_SIZE || cb > avail || files_at < HEADER_SIZE || files_at >= cb)
        return 0;
    if (get16(h + 26) == 0 || get16(h + 28) == 0)                         /* no folder or no file */
        return 0;
    if (get16(h + 30) & ~(FLAG_PREV_CABINET | FLAG_NEXT_CABINET | FLAG_RESERVE_PRESENT))
        return 0;
    return 1;
}

int64_t bcd_cab_find(const void *data, size_t size, size_t from)
{
    const uint8_t *d = data;
    if (!d)
        return -1;
    while (from < size && size - from >= HEADER_SIZE) {
        const uint8_t *m = memchr(d + from, 'M', size - from - HEADER_SIZE + 1);
        if (!m)
            return -1;
        size_t at = (size_t)(m - d);
        if (header_plausible(m, size - at))
            return (int64_t)at;
        from = at + 1;
    }
    return -1;
}

/* A folder entry, and its chain of data blocks: each inside the cabinet, each
 * holding 1 to 32 KB uncompressed, a stored one exactly as many as it unpacks to.
 * `blocks_left` is how many more blocks the cabinet's size leaves room for: in a
 * sound cabinet no two blocks overlap, so folders that share theirs to claim more
 * are refused (and the work here stays bounded by the cabinet's size). */
static int read_folder(const bcd_cab *cab, uint64_t at, cab_folder *f, uint64_t *blocks_left)
{
    const uint8_t *p = cab->data + at;
    f->first = get32(p);
    f->blocks = get16(p + 4);
    f->type = get16(p + 6);
    if (f->blocks == 0 || f->first < HEADER_SIZE || f->blocks > *blocks_left)
        return BCD_CAB_ERR_FORMAT;
    *blocks_left -= f->blocks;
    uint64_t off = f->first, usize = 0;
    for (uint32_t k = 0; k < f->blocks; k++) {
        if (off + DATA_SIZE + cab->data_reserve > cab->size)
            return BCD_CAB_ERR_FORMAT;
        const uint8_t *h = cab->data + off;
        uint32_t packed = get16(h + 4), unpacked = get16(h + 6);
        uint64_t body = off + DATA_SIZE + cab->data_reserve;
        if (packed == 0 || body + packed > cab->size)
            return BCD_CAB_ERR_FORMAT;
        if (unpacked == 0 || unpacked > BLOCK_MAX)   /* 0: continued in the next cabinet */
            return BCD_CAB_ERR_FORMAT;
        if (f->type == COMPRESS_NONE && packed != unpacked)
            return BCD_CAB_ERR_FORMAT;
        usize += unpacked;
        off = body + packed;
    }
    f->usize = usize;
    return BCD_CAB_OK;
}

/* A file entry: its name ends inside the cabinet and within 256 bytes, it is in
 * one of this cabinet's folders, and its range lies inside that folder's output. */
static int read_file(const bcd_cab *cab, uint64_t *pos, cab_file *f)
{
    uint64_t at = *pos;
    if (at + FILE_SIZE > cab->size)
        return BCD_CAB_ERR_FORMAT;
    const uint8_t *p = cab->data + at;
    f->size = get32(p);
    f->offset = get32(p + 4);
    f->folder = get16(p + 8);
    const uint8_t *name = p + FILE_SIZE;
    size_t room = (size_t)(cab->size - (at + FILE_SIZE));
    if (room > NAME_BYTES)
        room = NAME_BYTES;
    const uint8_t *nul = room ? memchr(name, 0, room) : NULL;
    if (!nul || nul == name)
        return BCD_CAB_ERR_FORMAT;
    if (f->folder >= FOLDER_CONTINUED)
        return BCD_CAB_ERR_UNSUPPORTED;
    if (f->folder >= cab->nfolders)
        return BCD_CAB_ERR_FORMAT;
    if ((uint64_t)f->offset + f->size > cab->folders[f->folder].usize)
        return BCD_CAB_ERR_FORMAT;
    f->name = (const char *)name;
    *pos = at + FILE_SIZE + (uint64_t)(nul - name) + 1;
    return BCD_CAB_OK;
}

int bcd_cab_open(const void *data, size_t size, bcd_cab **out)
{
    if (!out)
        return BCD_CAB_ERR_ARGUMENT;
    *out = NULL;
    if (!data)
        return BCD_CAB_ERR_ARGUMENT;
    const uint8_t *d = data;
    if (!header_plausible(d, size))
        return BCD_CAB_ERR_FORMAT;

    const uint32_t cb = get32(d + 8), files_at = get32(d + 16);
    const uint32_t nfolders = get16(d + 26), nfiles = get16(d + 28), flags = get16(d + 30);
    if (flags & (FLAG_PREV_CABINET | FLAG_NEXT_CABINET))
        return BCD_CAB_ERR_UNSUPPORTED;

    uint64_t pos = HEADER_SIZE;
    uint32_t folder_reserve = 0, data_reserve = 0;
    if (flags & FLAG_RESERVE_PRESENT) {
        if (pos + 4 > cb)
            return BCD_CAB_ERR_FORMAT;
        uint32_t header_reserve = get16(d + pos);
        folder_reserve = d[pos + 2];
        data_reserve = d[pos + 3];
        pos += 4;
        if (header_reserve > HEADER_RESERVE_MAX || pos + header_reserve > cb)
            return BCD_CAB_ERR_FORMAT;
        pos += header_reserve;
    }
    const uint64_t folder_stride = FOLDER_SIZE + (uint64_t)folder_reserve;
    if (pos + nfolders * folder_stride > files_at)   /* the folders come before the files */
        return BCD_CAB_ERR_FORMAT;

    bcd_cab *cab = calloc(1, sizeof *cab);
    if (!cab)
        return BCD_CAB_ERR_MEMORY;
    cab->data = d;
    cab->size = cb;
    cab->data_reserve = data_reserve;
    cab->nfolders = nfolders;
    cab->nfiles = nfiles;
    cab->folder = -1;
    cab->folders = calloc(nfolders, sizeof *cab->folders);
    cab->files = calloc(nfiles, sizeof *cab->files);
    if (!cab->folders || !cab->files) {
        bcd_cab_close(cab);
        return BCD_CAB_ERR_MEMORY;
    }

    int rc = BCD_CAB_OK;
    uint64_t blocks_left = cb / (DATA_SIZE + data_reserve + 1);   /* each block holds at least one byte */
    for (uint32_t i = 0; i < nfolders && rc == BCD_CAB_OK; i++, pos += folder_stride)
        rc = read_folder(cab, pos, &cab->folders[i], &blocks_left);
    pos = files_at;
    for (uint32_t i = 0; i < nfiles && rc == BCD_CAB_OK; i++)
        rc = read_file(cab, &pos, &cab->files[i]);
    if (rc != BCD_CAB_OK) {
        bcd_cab_close(cab);
        return rc;
    }
    *out = cab;
    return BCD_CAB_OK;
}

void bcd_cab_close(bcd_cab *cab)
{
    if (!cab)
        return;
    if (cab->zready)
        inflateEnd(&cab->z);
    free(cab->folders);
    free(cab->files);
    free(cab);
}

uint32_t bcd_cab_size(const bcd_cab *cab)
{
    return cab ? cab->size : 0;
}

uint32_t bcd_cab_count(const bcd_cab *cab)
{
    return cab ? cab->nfiles : 0;
}

const char *bcd_cab_name(const bcd_cab *cab, uint32_t index)
{
    return cab && index < cab->nfiles ? cab->files[index].name : NULL;
}

uint32_t bcd_cab_file_size(const bcd_cab *cab, uint32_t index)
{
    return cab && index < cab->nfiles ? cab->files[index].size : 0;
}

static void restart(bcd_cab *cab, uint32_t folder)
{
    cab->folder = (int)folder;
    cab->next_block = 0;
    cab->next_offset = cab->folders[folder].first;
    cab->out_start = 0;
    cab->out_len = 0;
    cab->hist_len = 0;
}

/* Keeps the last 32 KB of the folder's output: the next block's dictionary. */
static void remember(bcd_cab *cab, const uint8_t *p, uint32_t n)
{
    if (n >= BLOCK_MAX) {
        memcpy(cab->hist, p + n - BLOCK_MAX, BLOCK_MAX);
        cab->hist_len = BLOCK_MAX;
        return;
    }
    uint32_t keep = cab->hist_len;
    if (keep > BLOCK_MAX - n)
        keep = BLOCK_MAX - n;
    memmove(cab->hist, cab->hist + cab->hist_len - keep, keep);
    memcpy(cab->hist + keep, p, n);
    cab->hist_len = keep + n;
}

/* Decodes the folder's next block into out. Its bounds were checked by open. */
static int next_block(bcd_cab *cab)
{
    const cab_folder *f = &cab->folders[cab->folder];
    if (cab->next_block >= f->blocks)
        return BCD_CAB_ERR_FORMAT;
    const uint8_t *h = cab->data + cab->next_offset;
    const uint32_t sum = get32(h), packed = get16(h + 4), unpacked = get16(h + 6);
    const uint8_t *body = h + DATA_SIZE + cab->data_reserve;
    /* csum covers the data, then cbData and cbUncomp; 0 means none was stored. */
    if (sum != 0 && checksum(h + 4, 4, checksum(body, packed, 0)) != sum)
        return BCD_CAB_ERR_DATA;

    if (f->type == COMPRESS_NONE) {
        memcpy(cab->out, body, unpacked);
    } else {
        if (packed < 2 || body[0] != 'C' || body[1] != 'K')
            return BCD_CAB_ERR_DATA;
        if (!cab->zready) {
            if (inflateInit2(&cab->z, -MAX_WBITS) != Z_OK)
                return BCD_CAB_ERR_MEMORY;
            cab->zready = 1;
        } else if (inflateReset(&cab->z) != Z_OK) {
            return BCD_CAB_ERR_DATA;
        }
        if (cab->hist_len && inflateSetDictionary(&cab->z, cab->hist, cab->hist_len) != Z_OK)
            return BCD_CAB_ERR_DATA;
        cab->z.next_in = body + 2;
        cab->z.avail_in = packed - 2;
        cab->z.next_out = cab->out;
        cab->z.avail_out = BLOCK_MAX;
        int zr = inflate(&cab->z, Z_FINISH);
        if (zr != Z_STREAM_END || BLOCK_MAX - cab->z.avail_out != unpacked)
            return BCD_CAB_ERR_DATA;
        remember(cab, cab->out, unpacked);
    }
    cab->out_start += cab->out_len;
    cab->out_len = unpacked;
    cab->next_block++;
    cab->next_offset = (uint32_t)((body + packed) - cab->data);
    return BCD_CAB_OK;
}

int bcd_cab_extract(bcd_cab *cab, uint32_t index, void *dst, size_t dst_size)
{
    if (!cab || index >= cab->nfiles)
        return BCD_CAB_ERR_ARGUMENT;
    const cab_file *file = &cab->files[index];
    if (dst_size < file->size || (!dst && file->size))
        return BCD_CAB_ERR_ARGUMENT;
    if (file->size == 0)
        return BCD_CAB_OK;
    const cab_folder *f = &cab->folders[file->folder];
    if (f->type != COMPRESS_NONE && f->type != COMPRESS_MSZIP)
        return BCD_CAB_ERR_UNSUPPORTED;
    if (cab->folder != (int)file->folder || file->offset < cab->out_start)
        restart(cab, file->folder);

    uint8_t *w = dst;
    uint64_t pos = file->offset;
    const uint64_t end = pos + file->size;
    while (pos < end) {
        const uint64_t have = cab->out_start + cab->out_len;
        if (pos >= have) {
            int rc = next_block(cab);
            if (rc != BCD_CAB_OK) {
                cab->folder = -1;   /* out may be half written: start over next time */
                return rc;
            }
            continue;
        }
        uint64_t n = have - pos;
        if (n > end - pos)
            n = end - pos;
        memcpy(w, cab->out + (pos - cab->out_start), (size_t)n);
        w += n;
        pos += n;
    }
    return BCD_CAB_OK;
}

const char *bcd_cab_error(int result)
{
    switch (result) {
    case BCD_CAB_OK:              return "no error";
    case BCD_CAB_ERR_ARGUMENT:    return "bad argument";
    case BCD_CAB_ERR_FORMAT:      return "not a cabinet, or a damaged one";
    case BCD_CAB_ERR_UNSUPPORTED: return "LZX or Quantum data, or a multi-cabinet set";
    case BCD_CAB_ERR_DATA:        return "damaged compressed data";
    case BCD_CAB_ERR_MEMORY:      return "out of memory";
    default:                      return "unknown error";
    }
}

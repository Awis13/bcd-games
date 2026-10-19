// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
//
//  BCDCab.h
//
//  A reader for Microsoft cabinet files ("MSCF") whose folders are stored or
//  MSZIP-compressed, working on bytes in memory. Written from Microsoft's
//  published Cabinet File Format specification; VCRuntimeDownload.swift uses it
//  to take the Visual C++ runtime DLLs out of vc_redist.x64.exe on the device.
//
//  The bytes come from a download, so nothing in them is trusted: bcd_cab_open
//  checks every offset, count and length the reader will use later (header,
//  folders, files, the chain of data blocks) and refuses the whole cabinet when
//  anything does not add up. Extraction then checks each block's checksum and
//  that it inflates to exactly its declared size.
//
//  Not supported (refused, never guessed at): LZX and Quantum folders (their
//  file names can still be listed), and sets of cabinets that continue into one
//  another.

#ifndef BCD_CAB_H
#define BCD_CAB_H

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef struct bcd_cab bcd_cab;

/* Results. */
#define BCD_CAB_OK               0
#define BCD_CAB_ERR_ARGUMENT    -1   /* NULL, an index out of range, or a buffer too small */
#define BCD_CAB_ERR_FORMAT      -2   /* not a cabinet, or one whose structure does not add up */
#define BCD_CAB_ERR_UNSUPPORTED -3   /* LZX or Quantum data, or a cabinet of a multi-cabinet set */
#define BCD_CAB_ERR_DATA        -4   /* a data block with a bad checksum or bad compressed data */
#define BCD_CAB_ERR_MEMORY      -5

/* The offset of the first plausible cabinet header at or after `from` in
 * data[0, size): signature, zero reserved fields, version 1.3, and a declared
 * size that fits in what follows. -1 when there is none. A hit is only a
 * candidate; bcd_cab_open decides. */
int64_t bcd_cab_find(const void *data, size_t size, size_t from);

/* Opens the cabinet that starts at data[0]; `size` is how many bytes follow it
 * (the cabinet may be shorter: a signature can follow it). On success *cab is
 * the handle and BCD_CAB_OK is returned; otherwise *cab is NULL. The bytes must
 * stay valid and unchanged until bcd_cab_close. A handle is for one thread. */
int bcd_cab_open(const void *data, size_t size, bcd_cab **cab);
void bcd_cab_close(bcd_cab *cab);

/* The cabinet's own size (cbCabinet): it occupies data[0, size). */
uint32_t bcd_cab_size(const bcd_cab *cab);
/* The number of files. */
uint32_t bcd_cab_count(const bcd_cab *cab);
/* A file's name as stored (NUL-terminated, 1 to 255 bytes, any path separators
 * kept), or NULL for a bad index. Valid while the handle is. */
const char *bcd_cab_name(const bcd_cab *cab, uint32_t index);
/* A file's uncompressed size, or 0 for a bad index. */
uint32_t bcd_cab_file_size(const bcd_cab *cab, uint32_t index);

/* Decompresses file `index` into dst, which must hold at least its size.
 * Files of one folder taken in increasing order are decompressed in a single
 * pass; asking for an earlier one starts that folder over. */
int bcd_cab_extract(bcd_cab *cab, uint32_t index, void *dst, size_t dst_size);

/* A short English description of a result. */
const char *bcd_cab_error(int result);

#ifdef __cplusplus
}
#endif

#endif /* BCD_CAB_H */

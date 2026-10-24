/* SPDX-License-Identifier: GPL-3.0-or-later
 * Copyright 2026 mckenna654 (this program: thebcd69/bcd-games#4, submitted with a Signed-off-by under CONTRIBUTING.md)
 * Copyright 2026 thebcd69 (BCD) (the header only)
 * Modified by BCD (thebcd69), 2026-10-24: ported into the tree behind the public repository (header; the code is the contributor's, unchanged).
 * Not BCD Material: LICENSE-BCD.md section 1 keeps the other author's header and licence on a file that mixes in their text.
 *
 * Pagefile section regression: write x64 code through one view and run it
 * through another. Launch in BCD; no game files or extra DLLs required.
 * The contributor's licence line: GPL-3.0-or-later with the Madeira Converter Exception (LICENSE-EXCEPTION.md).
 *
 * Build: x86_64-w64-mingw32-clang -O2 -Wall -Wextra -Werror shared-section-x64.c -o shared-section-x64.exe (tests/x64/build.sh does it
 * for the other programs here). With env.MADEIRA_SHARED_SECTION_EXEC = 1 and env.MADEIRA_NTDLL_XSEC = 1 in the program's file the
 * expected output is four PASS lines and "shared-section: PASS", exit code 0. Without them the executable view is copied into the JIT
 * pool (as in the Diablo II: Resurrected log) and the first check is expected to end with "FAIL: executable and writable views do
 * not share bytes". Not run on a device yet.
 */
#include <windows.h>
#include <stdio.h>
#include <string.h>

static int check_code(unsigned char *rx, unsigned char *rw, unsigned char value)
{
    const unsigned char code[] = {0xb8, value, 0, 0, 0, 0xc3}; /* mov eax, value; ret */
    int result;

    memcpy(rw, code, sizeof(code));
    if (memcmp(rx, code, sizeof(code)))
    {
        puts("FAIL: executable and writable views do not share bytes");
        return 0;
    }
    /* Flush the execution address: writes through a different alias need not
     * trigger a write watch on the executable view. */
    if (!FlushInstructionCache(GetCurrentProcess(), rx, sizeof(code))) return 0;
    result = ((int (*)(void))rx)();
    if (result != value)
    {
        printf("FAIL: execution returned %d, expected %u\n", result, value);
        return 0;
    }
    return 1;
}

int main(void)
{
    HANDLE section;
    unsigned char *rx = NULL, *rw = NULL;
    DWORD old;
    int ok = 0;
    const SIZE_T size = 0x100000, offset = 0x3ae4;

    setvbuf(stdout, NULL, _IONBF, 0);
    section = CreateFileMappingW(INVALID_HANDLE_VALUE, NULL,
                                PAGE_EXECUTE_READWRITE | SEC_COMMIT, 0, (DWORD)size, NULL);
    if (!section) goto done;
    /* Match the failing log: map the executable view before the writable one. */
    rx = MapViewOfFile(section, FILE_MAP_READ | FILE_MAP_EXECUTE, 0, 0, size);
    rw = MapViewOfFile(section, FILE_MAP_WRITE, 0, 0, size);
    if (!rx || !rw) goto done;
    printf("shared-section: rx=%p rw=%p\n", rx, rw);
    if (!check_code(rx + offset, rw + offset, 42)) goto done;
    puts("PASS: initial alias write and execution");
    if (!check_code(rx + offset, rw + offset, 84)) goto done;
    puts("PASS: alias rewrite and instruction-cache flush");
    if (!VirtualProtect(rx, size, PAGE_READONLY, &old) ||
        !VirtualProtect(rx, size, PAGE_EXECUTE_READ, &old)) goto done;
    if (!check_code(rx + offset, rw + offset, 63)) goto done;
    puts("PASS: protection transition preserves sharing");
    if (!UnmapViewOfFile(rx)) goto done;
    rx = MapViewOfFile(section, FILE_MAP_READ | FILE_MAP_EXECUTE, 0, 0, size);
    if (!rx || !check_code(rx + offset, rw + offset, 21)) goto done;
    puts("PASS: unmap/remap and execution");
    ok = 1;
done:
    if (!ok) printf("FAIL: shared-section test (last error %lu)\n", GetLastError());
    if (rx) UnmapViewOfFile(rx);
    if (rw) UnmapViewOfFile(rw);
    if (section) CloseHandle(section);
    puts(ok ? "shared-section: PASS" : "shared-section: FAIL");
    return !ok;
}

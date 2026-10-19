#!/usr/bin/env python3
"""Mirror lines of a game's own text log into the session log ([guest-log]).

Before the build 222 switch to upstream's pins this fork's wine (125hz
pr/wow64-core, dlls/ntdll/unix/file.c) mirrored error lines a program wrote
to its *.log file; that is how Ghost of Tsushima's "[NxApp] Failed to get GPU
Driver Info" reached the 2026-09-28 20:41 log. willfaust/wine has no such
hook, so since build 222 a game that stops at its own dialog leaves no reason
in the session log. This restores it, applied to the wine checkout before
"Build ntdll-unix" (the submodule is not committed to).

NtWriteFile, after a successful write to a regular file and before the fd is
closed, hands the bytes to ios_guest_log_mirror(): it returns at once for a
spent budget, short writes and binary data. UTF-16 LE/BE writes are decoded
before the control-byte check (a BOM may have been written separately);
it splits the text into lines, keeps the ones that matter and, only
then, asks the fd's path (F_GETPATH) -- only *.log and output_log.txt count.
Guest I/O results are never changed.

MADEIRA_GUEST_LOG (madeira.cfg env.MADEIRA_GUEST_LOG / the game's file):
  unset   lines with error / fail / exception / unsupported / gpu / driver /
          adapter / graphics / monitor / display / nvapi / nvidia, 64 lines
  all     every line, 400 lines
  0       off (MADEIRA_GUEST_LOG_ERRORS=0, the old switch, also turns it off)
MADEIRA_GUEST_LOG_LIMIT=N overrides the line budget (1..4000). The first line
from each new file names it once: "[guest-log] file <unix path>".

Usage: patch-wine-guest-log.py [path/to/wine/dlls/ntdll/unix/file.c]
Idempotent; exits non-zero when an anchor is missing (wine moved).
"""
import sys

path = sys.argv[1] if len(sys.argv) > 1 else "wine/dlls/ntdll/unix/file.c"
src = open(path).read()
marker = "madeira-bcd: [guest-log] mirror"
if marker in src:
    print("patch-wine-guest-log: already patched")
    sys.exit(0)

helper = r'''#ifdef WINE_IOS
/* madeira-bcd: [guest-log] mirror (tools/patch-wine-guest-log.py). */
/* guest-log-test:begin (tests/host/check-guest-log.py compiles this region) */
static int ios_guest_log_fd_path( int fd, char *path, size_t size )
{
#ifdef F_GETPATH
    (void)size;   /* F_GETPATH writes at most MAXPATHLEN (PATH_MAX) bytes */
    return fcntl( fd, F_GETPATH, path ) != -1;
#else
    char link[64];
    ssize_t n;
    snprintf( link, sizeof(link), "/proc/self/fd/%d", fd );
    if ((n = readlink( link, path, size - 1 )) < 0) return 0;
    path[n] = 0;
    return 1;
#endif
}

static int ios_guest_log_keep( const char *line, size_t len )
{
    static const char *const words[] =
    {
        "error", "fail", "exception", "unsupported", "not supported", "gpu", "driver",
        "adapter", "graphics", "monitor", "display", "nvapi", "nvidia",
    };
    char lower[320];
    size_t i;

    if (len >= sizeof(lower)) len = sizeof(lower) - 1;
    for (i = 0; i < len; i++)
        lower[i] = line[i] >= 'A' && line[i] <= 'Z' ? line[i] + ('a' - 'A') : line[i];
    lower[len] = 0;
    for (i = 0; i < sizeof(words) / sizeof(words[0]); i++)
        if (strstr( lower, words[i] )) return 1;
    return 0;
}

/* A BOM can be its own NtWriteFile call. Without one, require several ASCII
 * code units with the correct zero-byte layout before considering UTF-16.
 * Validate the whole bounded input, including text beyond the output limit;
 * do not reinterpret arbitrary NUL-containing writes as text. No fd state is
 * cached, so descriptor reuse and different writers cannot mix encodings. */
static int ios_guest_log_utf16( const unsigned char *src, unsigned int n,
                              int clipped, char *dst, unsigned int capacity )
{
    unsigned int i, start = 0, ascii_le = 0, ascii_be = 0, written = 0;
    int endian = 0, full = 0;

    if (n < 2 || (n & 1)) return -1;
    if (src[0] == 0xff && src[1] == 0xfe) { endian = 1; start = 2; }
    else if (src[0] == 0xfe && src[1] == 0xff) { endian = 2; start = 2; }
    else
    {
        for (i = 0; i < n; i += 2)
        {
            unsigned int a = src[i], b = src[i + 1];
            if (!b && ((a >= 32 && a <= 126) || a == '\n' || a == '\r' || a == '\t')) ascii_le++;
            if (!a && ((b >= 32 && b <= 126) || b == '\n' || b == '\r' || b == '\t')) ascii_be++;
        }
        if (ascii_le >= 4 && ascii_le >= (n / 2) - (n / 2) / 4) endian = 1;
        else if (ascii_be >= 4 && ascii_be >= (n / 2) - (n / 2) / 4) endian = 2;
        else return -1;
    }
    for (i = start; i < n; i += 2)
    {
        unsigned int c = endian == 1 ? src[i] | (src[i + 1] << 8) : (src[i] << 8) | src[i + 1];
        unsigned int bytes;
        if ((c < 32 && c != '\n' && c != '\r' && c != '\t') || (c >= 127 && c <= 159)) return -1;
        if (c >= 0xd800 && c <= 0xdbff)
        {
            unsigned int low;
            if (i + 3 >= n)
            {
                if (clipped) break; /* bounded prefix ended between a surrogate pair */
                return -1;
            }
            low = endian == 1 ? src[i + 2] | (src[i + 3] << 8) : (src[i + 2] << 8) | src[i + 3];
            if (low < 0xdc00 || low > 0xdfff) return -1;
            c = 0x10000 + ((c - 0xd800) << 10) + low - 0xdc00;
            i += 2;
        }
        else if (c >= 0xdc00 && c <= 0xdfff) return -1;
        if (c == 0xfffe || c == 0xffff) return -1;
        bytes = c < 0x80 ? 1 : c < 0x800 ? 2 : c < 0x10000 ? 3 : 4;
        if (full || bytes > capacity - written) { full = 1; continue; }
        if (bytes == 1) dst[written++] = c;
        else
        {
            if (bytes == 2) dst[written++] = 0xc0 | (c >> 6);
            else if (bytes == 3)
            {
                dst[written++] = 0xe0 | (c >> 12);
                dst[written++] = 0x80 | ((c >> 6) & 0x3f);
            }
            else
            {
                dst[written++] = 0xf0 | (c >> 18);
                dst[written++] = 0x80 | ((c >> 12) & 0x3f);
                dst[written++] = 0x80 | ((c >> 6) & 0x3f);
            }
            dst[written++] = 0x80 | (c & 0x3f);
        }
    }
    return written;
}

static void ios_guest_log_mirror( int fd, const void *buffer, unsigned int length )
{
    static int mode = -1;                 /* 0 off, 1 keyword lines, 2 every line */
    static unsigned int limit, emitted, announced;
    static unsigned long long previous_hash, file_hashes[8];
    const unsigned char *text = buffer;
    unsigned int i, n, start, serial;
    int saved_errno = errno, have_path = 0, m = __atomic_load_n( &mode, __ATOMIC_RELAXED );
    char path[PATH_MAX];
    char decoded[4096];

    if (m < 0)
    {
        const char *e = getenv( "MADEIRA_GUEST_LOG" ), *old = getenv( "MADEIRA_GUEST_LOG_ERRORS" );
        const char *l = getenv( "MADEIRA_GUEST_LOG_LIMIT" );
        unsigned int lim;

        if ((e && !strcmp( e, "0" )) || (old && !strcmp( old, "0" ))) m = 0;
        else if (e && !strcasecmp( e, "all" )) m = 2;
        else m = 1;
        lim = l ? (unsigned int)strtoul( l, NULL, 10 ) : 0;
        if (!lim || lim > 4000) lim = m == 2 ? 400 : 64;
        __atomic_store_n( &limit, lim, __ATOMIC_RELAXED );
        __atomic_store_n( &mode, m, __ATOMIC_RELAXED );
        if (!__atomic_exchange_n( &announced, 1, __ATOMIC_RELAXED ))
            dprintf( 2, "[guest-log] madeira-bcd text-log mirror: %s, %u lines (MADEIRA_GUEST_LOG=all|0, "
                     "MADEIRA_GUEST_LOG_LIMIT=N)\n", m == 0 ? "off" : m == 2 ? "every line" : "error/GPU/display lines",
                     lim );
    }
    if (!m || length < 5) goto out;
    if (__atomic_load_n( &emitted, __ATOMIC_RELAXED ) >= __atomic_load_n( &limit, __ATOMIC_RELAXED )) goto out;

    n = length < 4096 ? length : 4096;
    {
        int wide = n >= 2 && ((text[0] == 0xff && text[1] == 0xfe) ||
                              (text[0] == 0xfe && text[1] == 0xff));
        for (i = 0; !wide && i < n; i++) if (!text[i]) wide = 1;
        if (wide)
        {
            int count = ios_guest_log_utf16( text, n, length > n, decoded, sizeof(decoded) );
            if (count < 0) goto out;
            n = count;
            text = (const unsigned char *)decoded;
        }
    }
    for (i = 0; i < n; i++)
        if (text[i] < 32 && text[i] != '\n' && text[i] != '\r' && text[i] != '\t') goto out;   /* binary */

    for (start = 0; start < n; start = i + 1)
    {
        unsigned long long hash = 14695981039346656037ULL;
        unsigned int len, shown, j;

        for (i = start; i < n && text[i] != '\n'; i++) ;
        len = i - start;
        while (len && (text[start + len - 1] == '\r' || text[start + len - 1] == ' ')) len--;
        if (len < 3) continue;
        if (m == 1 && !ios_guest_log_keep( (const char *)text + start, len )) continue;
        if (!have_path)
        {
            const char *name, *ext;
            if (!ios_guest_log_fd_path( fd, path, sizeof(path) )) goto out;
            name = strrchr( path, '/' );
            name = name ? name + 1 : path;
            ext = strrchr( name, '.' );
            /* only *.log and output_log.txt; never arbitrary text files */
            if ((!ext || strcasecmp( ext, ".log" )) && strcasecmp( name, "output_log.txt" )) goto out;
            have_path = 1;
            {
                unsigned long long ph = 14695981039346656037ULL;
                const char *p;
                for (p = path; *p; p++) ph = (ph ^ (unsigned char)*p) * 1099511628211ULL;
                for (j = 0; j < 8; j++)
                {
                    unsigned long long cur = __atomic_load_n( &file_hashes[j], __ATOMIC_RELAXED );
                    if (cur == ph) break;
                    if (!cur && __atomic_compare_exchange_n( &file_hashes[j], &cur, ph, 0,
                                                             __ATOMIC_RELAXED, __ATOMIC_RELAXED ))
                    {
                        dprintf( 2, "[guest-log] file %s\n", path );
                        break;
                    }
                    if (cur == ph) break;
                }
            }
        }
        for (j = 0; j < len; j++) hash = (hash ^ text[start + j]) * 1099511628211ULL;
        if (__atomic_exchange_n( &previous_hash, hash, __ATOMIC_RELAXED ) == hash) continue;
        serial = __atomic_fetch_add( &emitted, 1, __ATOMIC_RELAXED );
        if (serial >= __atomic_load_n( &limit, __ATOMIC_RELAXED )) break;
        shown = len > 300 ? 300 : len;
        /* Keep a truncated UTF-8 line valid, including decoded UTF-16. */
        if (shown < len)
            while (shown && (text[start + shown] & 0xc0) == 0x80) shown--;
        dprintf( 2, "[guest-log] #%u tid=%04x %.*s%s\n", serial + 1, (unsigned int)GetCurrentThreadId(),
                 (int)shown, (const char *)text + start, shown < len ? " [truncated]" : "" );
    }
out:
    errno = saved_errno;
}
/* guest-log-test:end */
#endif

'''

anchor = ("/******************************************************************************\n"
          " *              NtWriteFile   (NTDLL.@)\n"
          " */\n")
if src.count(anchor) != 1:
    sys.exit("patch-wine-guest-log: NtWriteFile header anchor not found (%d)" % src.count(anchor))
src = src.replace(anchor, helper + anchor)

call_anchor = ("done:\n    send_completion = cvalue != 0;\n\nerr:\n"
               "    if (needs_close) close( unix_handle );\n\n    if (type == FD_TYPE_SERIAL")
if src.count(call_anchor) != 1:
    sys.exit("patch-wine-guest-log: NtWriteFile tail anchor not found (%d)" % src.count(call_anchor))
src = src.replace(call_anchor, (
    "done:\n    send_completion = cvalue != 0;\n\nerr:\n"
    "#ifdef WINE_IOS\n"
    "    /* " + marker + ": tools/patch-wine-guest-log.py */\n"
    "    if (status == STATUS_SUCCESS && type == FD_TYPE_FILE && total)\n"
    "        ios_guest_log_mirror( unix_handle, buffer, total );\n"
    "#endif\n"
    "    if (needs_close) close( unix_handle );\n\n    if (type == FD_TYPE_SERIAL"))

for inc in ("#include <limits.h>", "#include <fcntl.h>", "#include <errno.h>"):
    if inc not in src:
        sys.exit("patch-wine-guest-log: %s missing from file.c" % inc)

open(path, "w").write(src)
print("patch-wine-guest-log: [guest-log] mirror added to " + path)

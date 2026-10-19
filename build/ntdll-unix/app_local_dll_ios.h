/* Per-session, opt-in app-local DLL reads. Included after get_nt_and_unix_names.
 * No files are copied or changed. The normal file-id module lookup can reuse
 * a loaded app-local image when a later request names a different directory. */
#ifdef WINE_IOS

static WCHAR madeira_dll_path_char( WCHAR c )
{
    if (c == '/') return '\\';
    if (c >= 'A' && c <= 'Z') return c + ('a' - 'A');
    return c;
}

static unsigned int madeira_dll_dos_start( const UNICODE_STRING *name )
{
    unsigned int start = 0, n;
    const WCHAR *p;

    if (!name || !name->Buffer || name->Length % sizeof(WCHAR)) return ~0u;
    p = name->Buffer;
    n = name->Length / sizeof(WCHAR);
    if (n >= 4 && p[0] == '\\' && p[1] == '?' && p[2] == '?' && p[3] == '\\') start = 4;
    if (n < start + 3 || !((p[start] >= 'A' && p[start] <= 'Z') ||
                           (p[start] >= 'a' && p[start] <= 'z')) ||
        p[start + 1] != ':' || madeira_dll_path_char(p[start + 2]) != '\\') return ~0u;
    for (unsigned int i = start; i < n; i++) if (!p[i]) return ~0u;
    return start;
}

static BOOL madeira_dll_alias_name_valid( const char *name, size_t length )
{
    if (length <= 4 || name[length - 4] != '.' ||
        madeira_dll_path_char(name[length - 3]) != 'd' ||
        madeira_dll_path_char(name[length - 2]) != 'l' ||
        madeira_dll_path_char(name[length - 1]) != 'l') return FALSE;
    for (size_t i = 0; i < length; i++)
    {
        unsigned char c = name[i];
        if (c < 33 || c > 126 || strchr("<>:\"/\\|?*=", c)) return FALSE;
    }
    return TRUE;
}

/* Aliases apply outside the app directory only. Refuse ambiguous directory
 * syntax instead of redirecting an app's own initial dependency load. */
static BOOL madeira_dll_alias_directory_valid( const WCHAR *path, unsigned int start,
                                               unsigned int end )
{
    unsigned int segment = start + 3;
    for (unsigned int i = segment; i < end; i++)
    {
        if (madeira_dll_path_char(path[i]) != '\\') continue;
        if (i == segment || (i - segment == 1 && path[segment] == '.') ||
            (i - segment == 2 && path[segment] == '.' && path[segment + 1] == '.')) return FALSE;
        segment = i + 1;
    }
    return TRUE;
}

static unsigned int madeira_dll_local_path( const char *list, const UNICODE_STRING *request,
                                           const UNICODE_STRING *image, WCHAR *out, size_t capacity )
{
    unsigned int rs, es, rn, en, base, dir, bn, target_length, n, i;
    const char *entry, *target = NULL;
    BOOL selected = FALSE;

    if (!list || !*list) return 0;
    rs = madeira_dll_dos_start( request );
    es = madeira_dll_dos_start( image );
    if (rs == ~0u || es == ~0u) return 0; /* no relative, pipe, Unix, or UNC paths */
    rn = request->Length / sizeof(WCHAR);
    en = image->Length / sizeof(WCHAR);
    for (base = rn; base > rs && madeira_dll_path_char(request->Buffer[base - 1]) != '\\'; base--) {}
    bn = rn - base;
    if (bn <= 4 || request->Buffer[rn - 4] != '.' ||
        madeira_dll_path_char(request->Buffer[rn - 3]) != 'd' ||
        madeira_dll_path_char(request->Buffer[rn - 2]) != 'l' ||
        madeira_dll_path_char(request->Buffer[rn - 1]) != 'l') return 0;
    for (dir = en; dir > es && madeira_dll_path_char(image->Buffer[dir - 1]) != '\\'; dir--) {}
    target_length = bn;
    for (entry = list; *entry; )
    {
        const char *end = strchr( entry, ';' ), *next, *equal, *source_end;
        if (!end) end = entry + strlen(entry);
        next = *end ? end + 1 : end;
        while (entry < end && (*entry == ' ' || *entry == '\t')) entry++;
        while (end > entry && (end[-1] == ' ' || end[-1] == '\t')) end--;
        equal = memchr( entry, '=', end - entry );
        source_end = equal ? equal : end;
        while (source_end > entry && (source_end[-1] == ' ' || source_end[-1] == '\t')) source_end--;
        if ((size_t)(source_end - entry) == bn)
        {
            for (i = 0; i < bn; i++)
            {
                unsigned char c = entry[i];
                if (c < 33 || c > 126 || c == '/' || c == '\\' || c == ':' || c == '*') break;
                if (madeira_dll_path_char(c) != madeira_dll_path_char(request->Buffer[base + i])) break;
            }
            if (i == bn)
            {
                if (equal)
                {
                    const char *alias = equal + 1;
                    while (alias < end && (*alias == ' ' || *alias == '\t')) alias++;
                    if (!madeira_dll_alias_name_valid( entry, source_end - entry ) ||
                        !madeira_dll_alias_name_valid( alias, end - alias ))
                    { entry = next; continue; }
                    if (!madeira_dll_alias_directory_valid( request->Buffer, rs, base ) ||
                        !madeira_dll_alias_directory_valid( image->Buffer, es, dir )) return 0;
                    if (base - rs == dir - es)
                    {
                        for (i = 0; i < dir - es; i++)
                            if (madeira_dll_path_char(request->Buffer[rs + i]) !=
                                madeira_dll_path_char(image->Buffer[es + i])) break;
                        if (i == dir - es) return 0;
                    }
                    target = alias;
                    if ((size_t)(end - alias) >= 0x7fff) return 0;
                    target_length = end - alias;
                }
                selected = TRUE;
                break;
            }
        }
        entry = next;
    }
    if (!selected) return 0;
    n = 4 + dir - es + target_length;
    if (n >= capacity || n >= 0x7fff) return 0;
    out[0] = '\\'; out[1] = '?'; out[2] = '?'; out[3] = '\\';
    memcpy( out + 4, image->Buffer + es, (dir - es) * sizeof(WCHAR) );
    if (target)
        for (i = 0; i < target_length; i++) out[4 + dir - es + i] = (unsigned char)target[i];
    else memcpy( out + 4 + dir - es, request->Buffer + base, bn * sizeof(WCHAR) );
    out[n] = 0;
    /* A request already in the app directory needs no second translation. */
    if (n - 4 == rn - rs)
    {
        for (i = 0; i < n - 4; i++)
            if (madeira_dll_path_char(out[4 + i]) != madeira_dll_path_char(request->Buffer[rs + i])) break;
        if (i == n - 4) return 0;
    }
    return n;
}

static NTSTATUS madeira_dll_read_names( OBJECT_ATTRIBUTES *attr, UNICODE_STRING *nt_name,
                                       char **unix_name, UINT disposition, BOOL open_reparse,
                                       ACCESS_MASK access, ULONG options )
{
    /* MADEIRA_DLL_LOCAL: semicolon-separated DLL filenames (including .dll),
     * optionally source.dll=proxy.dll for requests outside the app directory.
     * Read only, off by default. Explicit path requests use the current guest
     * executable's directory when that selected local file exists. */
    const char *list = getenv( "MADEIRA_DLL_LOCAL" );
    const ACCESS_MASK writes = FILE_WRITE_DATA | FILE_APPEND_DATA | FILE_WRITE_EA |
                              FILE_WRITE_ATTRIBUTES | DELETE | WRITE_DAC | WRITE_OWNER |
                              GENERIC_WRITE | GENERIC_ALL | MAXIMUM_ALLOWED;
    TEB *teb;
    PEB *peb;
    WCHAR path[4096];
    unsigned int n;

    if (list && *list && disposition == FILE_OPEN && !(access & writes) &&
        !(options & (FILE_DELETE_ON_CLOSE | FILE_DIRECTORY_FILE | FILE_OPEN_REPARSE_POINT)) &&
        !attr->RootDirectory && (teb = NtCurrentTeb()) && (peb = teb->Peb) && peb->ProcessParameters &&
        (n = madeira_dll_local_path( list, attr->ObjectName,
                                     &peb->ProcessParameters->ImagePathName, path, ARRAY_SIZE(path) )))
    {
        UNICODE_STRING candidate = {n * sizeof(WCHAR), (n + 1) * sizeof(WCHAR), path};
        OBJECT_ATTRIBUTES local = *attr;
        NTSTATUS status;
        struct stat st;

        local.ObjectName = &candidate;
        status = get_nt_and_unix_names( &local, nt_name, unix_name, FILE_OPEN, open_reparse );
        if (!status && !stat( *unix_name, &st ) && S_ISREG(st.st_mode))
        {
            /* Translation may retain the input name. Give the caller owned
             * storage, never the stack candidate or its UNICODE_STRING. */
            if (!nt_name->Buffer)
            {
                if ((nt_name->Buffer = malloc(candidate.MaximumLength)))
                {
                    memcpy( nt_name->Buffer, path, candidate.MaximumLength );
                    nt_name->Length = candidate.Length;
                    nt_name->MaximumLength = candidate.MaximumLength;
                }
                else status = STATUS_NO_MEMORY;
            }
            if (!status)
            {
                static int logged;
                if (__atomic_add_fetch( &logged, 1, __ATOMIC_RELAXED ) <= 64)
                    dprintf( 2, "[dll-local] %s -> %s (read-only app-local override)\n",
                             debugstr_us(attr->ObjectName), debugstr_us(nt_name) );
                *attr = local;
                attr->ObjectName = nt_name;
                return STATUS_SUCCESS;
            }
        }
        /* Unresolved or non-regular local candidates keep Wine's
         * requested path. Do not leave candidate pointers in the caller. */
        free( nt_name->Buffer );
        free( *unix_name );
    }
    return get_nt_and_unix_names( attr, nt_name, unix_name, disposition, open_reparse );
}

#else
#define madeira_dll_read_names(a,b,c,d,e,f,g) get_nt_and_unix_names(a,b,c,d,e)
#endif

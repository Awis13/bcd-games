#!/usr/bin/env python3
"""Add opt-in, read-only app-local DLL path resolution to Wine's Unix file API.

The PE ntdll and submodule pin stay unchanged. Its existing file-id lookup
can reuse the local image instead of loading a second copy from another path.
"""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
MARKER = "madeira-bcd: opt-in app-local DLL reads"


def patch(source):
    if MARKER in source:
        return source
    helper = (ROOT / "build/ntdll-unix/app_local_dll_ios.h").read_text()
    anchor = "/***********************************************************************\n *           get_full_path\n"
    create = """    else status = get_nt_and_unix_names( &new_attr, &nt_name, &unix_name, disposition,
                                         options & FILE_OPEN_REPARSE_POINT );"""
    query = "    if (!(status = get_nt_and_unix_names( &new_attr, &nt_name, &unix_name, FILE_OPEN, TRUE )))"
    if source.count(anchor) != 1 or source.count(create) != 1 or source.count(query) != 2:
        raise ValueError("app-local DLL patch anchors missing or ambiguous")
    source = source.replace(anchor, "/* " + MARKER + " */\n" + helper + "\n\n" + anchor)
    source = source.replace(create, """    else status = madeira_dll_read_names( &new_attr, &nt_name, &unix_name, disposition,
                                          options & FILE_OPEN_REPARSE_POINT, access, options );""")
    source = source.replace(query, """    if (!(status = madeira_dll_read_names( &new_attr, &nt_name, &unix_name, FILE_OPEN, TRUE,
                                          FILE_READ_ATTRIBUTES, 0 )))""")
    return source


def main():
    path = Path(sys.argv[1] if len(sys.argv) > 1 else "wine/dlls/ntdll/unix/file.c")
    source = path.read_text()
    try:
        result = patch(source)
    except ValueError as error:
        sys.exit(str(error))
    if result == source:
        print("app-local DLL reads already patched")
    else:
        path.write_text(result)
        print("patched app-local DLL reads")


if __name__ == "__main__":
    main()

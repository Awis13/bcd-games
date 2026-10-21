#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""[rpc-np]: the status of a failed RPC named-pipe read or write (madeira-bcd; logging only).

Nine testers' logs (builds 572-574) end an RPC bind with "RpcAssoc_BindConnection receive failed with
error 1726": Valve's SteamService.exe /install (Just Cause 2 JYFG4D, Saints Row: Gat out of Hell REDAH7;
"Failed to open service control manager", so Steam's CEG step fails), RockstarService.exe (GTA V Legacy,
RDR2). The pipe opens; the read of the bind reply fails, and Wine's rpcrt4 does not say how. This logs the
NTSTATUS, the byte count and the pipe's state for the first 16 failures in a process, on the failure path
only: nothing else changes.

Usage: patch-wine-rpc-np-diag.py <wine source dir>. Idempotent; fails by name if an anchor moves.
"""
import pathlib
import sys

wine = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "wine")
PATH = wine / "dlls/rpcrt4/rpc_transport.c"
MARKER = "madeira-bcd: [rpc-np]"

READ_OLD = """    release_np_event(connection, event);
    return status && status != STATUS_BUFFER_OVERFLOW ? -1 : connection->io_status.Information;
}"""
READ_NEW = """    release_np_event(connection, event);
    if (status && status != STATUS_BUFFER_OVERFLOW)
    {
        static LONG told;   /* madeira-bcd: [rpc-np] (tools/patch-wine-rpc-np-diag.py) */
        if (InterlockedIncrement(&told) <= 16)
            ERR("[rpc-np] read failed: status %#lx, %u bytes asked, read_closed %d, io %#lx/%Iu\\n", (long)status,
                count, (int)connection->read_closed, (long)connection->io_status.Status, connection->io_status.Information);
    }
    return status && status != STATUS_BUFFER_OVERFLOW ? -1 : connection->io_status.Information;
}"""


def main():
    text = PATH.read_text()
    if MARKER in text:
        print("rpc named-pipe diagnostics: already applied")
        return
    if text.count(READ_OLD) != 1:
        sys.exit("patch-wine-rpc-np-diag: anchor not found exactly once in " + str(PATH))
    text = text.replace(READ_OLD, READ_NEW)
    PATH.write_text(text)
    print("rpc named-pipe diagnostics: applied")


if __name__ == "__main__":
    main()

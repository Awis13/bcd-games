#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""madeira-bcd: a kernelbase that logs the fiber calls (a diagnostic).

Microsoft Flight Simulator 2024 (build 540) stops in BOOT_INIT with its own
crash report "FiberError": its job system created 57 fibers and converted 10
threads, and the count of free fibers in the report's frame table fell 57 -> 4
over 53 frames, in steps of 1-5, until the game hit int3. Each lost fiber is a
job that never finished. Wine's fiber functions (dlls/kernelbase/thread.c) log
nothing, so the logs cannot say where those fibers wait.

This patch makes Wine's kernelbase log them. Each line starts with
"[fiber-trace] pid:tid":
  - every CreateFiberEx, ConvertThreadToFiberEx, ConvertFiberToThread and
    DeleteFiber (the first 256 of each), with the x64 call site;
  - every 2 s while fibers switch: how many run, are parked, were never run or
    are being left, the switches, and counts of overlapped ReadFile calls
    (done at once, pending, failed), ReadFileEx calls, completion-port entries
    dequeued and GetOverlappedResult(Ex) results;
  - each fiber parked for 3 s or longer, once, with the x64 return addresses
    found on its saved stack and what each call there calls (exe+offset,
    module+offset, [exe+slot]=module+offset for an import), and a line when it
    runs again;
  - a switch into a fiber that runs on another thread, whose switch-out has not
    finished, or that was deleted;
  - a fiber whose start routine returns or raises an unhandled exception;
  - the whole table at the first unhandled exception (UnhandledExceptionFilter);
  - at an unhandled MSVC C++ exception (0xe06d7363), with or without fibers:
    the thrown types (decorated names from its ThrowInfo), what() when one
    is std::exception, the object's vftable and first words, and the x64
    calls on the throwing stack (Ratchet & Clank: Rift Apart, build 550,
    died of a C++ exception its own crash handler calls "Unknown
    exception"; the dispatcher's ml811 line names only the code).
The fiber functions behave as before. Lines stay under 720 bytes (ntdll aborts
on a debug line of 1020).

The patched DLL ships as arm64ec-windows/kernelbase-fibertrace.dll NEXT TO the
untouched kernelbase.dll; only a game whose settings have
env.MADEIRA_FIBER_TRACE = 1 gets it (app/Madeira/WineProcessBridge.m links it
as system32\\kernelbase.dll and sysx64\\kernelbase.dll for that session).

Usage:
  patch-wine-kernelbase-fibertrace.py WINE_SRC              patch the tree
      (idempotent: a second run changes nothing)
  patch-wine-kernelbase-fibertrace.py --verify DLL SHIPPED  check a built DLL:
      it carries the trace and exports exactly the shipped kernelbase's names
tools/build-wine-extra-dlls.sh applies it around its kernelbase build and
restores the tree afterwards (FILES below).
"""
import os
import struct
import sys

MARKER = "fiber-trace-v1 madeira-bcd"
FILES = ("dlls/kernelbase/kernelbase.h", "dlls/kernelbase/thread.c", "dlls/kernelbase/debug.c",
         "dlls/kernelbase/file.c", "dlls/kernelbase/sync.c")

TRACE_C = r'''/* madeira-bcd fiber trace begin (tools/patch-wine-kernelbase-fibertrace.py) */
/* Only in kernelbase-fibertrace.dll, which app/Madeira/WineProcessBridge.m links
 * as kernelbase.dll for a game with env.MADEIRA_FIBER_TRACE = 1. Wine's fiber
 * functions log nothing; Microsoft Flight Simulator 2024 stops with "FiberError"
 * once its job system has run out of free fibers (build 540). The table holds
 * one entry per fiber, found through fiber_data's ft_index; a switch costs two
 * interlocked operations and a tick read. Reports run on the switching thread,
 * at most every FT_REPORT_MS, and read other fibers' stacks under a page-fault
 * guard. Addresses inside the game's exe print as exe+offset. */
#define FT_MAX        1024
#define FT_SWITCHING  0x40000000   /* state: thread id | this while its thread leaves it */
#define FT_DEAD       0x7fffffff   /* state: deleted or converted back */
#define FT_STUCK_MS   3000
#define FT_REPORT_MS  2000
#define FT_LINES      256
#define FT_SCAN_BYTES 0x8000
#define FT_CALLS      8
#define FT_IO         6

struct ft_entry
{
    struct fiber_data *fiber;      /* set last: the entry is complete */
    void              *start;
    void              *param;
    void              *stack_base;
    void              *stack_alloc;
    SIZE_T             commit;
    SIZE_T             reserve;
    DWORD              flags;
    LONG               creator;    /* thread that created or converted it */
    LONG               converted;  /* a thread turned into a fiber */
    LONG volatile      state;      /* 0 parked, thread id running, | FT_SWITCHING being left, FT_DEAD */
    LONG               came_from;  /* index + 1 of the fiber its thread left to enter it */
    LONG               last_tid;
    LONG               stuck;      /* reported parked for FT_STUCK_MS, not run since */
    LONG               in;         /* switches into it */
    ULONGLONG          parked_at;  /* GetTickCount64() when it was last left */
};

static struct ft_entry ft_table[FT_MAX];
static LONG ft_used, ft_banner, ft_dumped, ft_full;
static LONG ft_lines_made, ft_lines_gone, ft_lines_wrong, ft_lines_back;
static LONGLONG ft_next_report;
static LONG ft_in_last;
static ULONGLONG ft_report_last;
static ULONG_PTR ft_exe_base, ft_exe_end;
/* overlapped reads done at once, pending, failed; ReadFileEx; completion-port
 * entries dequeued; GetOverlappedResult(Ex) results */
static LONG ft_io[FT_IO], ft_io_last[FT_IO];

void madeira_fibertrace_dump( const EXCEPTION_RECORD *rec, const CONTEXT *ctx );
void madeira_fibertrace_io( int what, LONG count );

static LONG ft_tid(void)
{
    return HandleToLong( NtCurrentTeb()->ClientId.UniqueThread );
}

static LONG ft_count(void)
{
    LONG n = ft_used;
    return n > FT_MAX ? FT_MAX : n;
}

/* append text, always terminated, never past size */
static void ft_cat( char *buf, size_t size, size_t *pos, const char *text )
{
    while (*text && *pos + 1 < size) buf[(*pos)++] = *text++;
    buf[*pos] = 0;
}

static void ft_out( const char *text )
{
    char line[720], head[40];
    size_t pos = 0;

    snprintf( head, sizeof(head), "[fiber-trace] %04x:%04x ",
              (unsigned int)HandleToULong( NtCurrentTeb()->ClientId.UniqueProcess ), (unsigned int)ft_tid() );
    head[sizeof(head) - 1] = 0;
    ft_cat( line, sizeof(line) - 1, &pos, head );
    ft_cat( line, sizeof(line) - 1, &pos, text );
    line[pos++] = '\n';
    line[pos] = 0;
    MESSAGE( "%s", line );
}

static void ft_narrow( char *out, size_t size, const WCHAR *str, size_t len, BOOL base_only )
{
    size_t i, start = 0, n = 0;

    if (!str) len = 0;
    if (base_only) for (i = 0; i < len; i++) if (str[i] == '\\' || str[i] == '/') start = i + 1;
    for (i = start; i < len && n + 1 < size; i++) out[n++] = (str[i] >= 0x20 && str[i] < 0x7f) ? (char)str[i] : '?';
    out[n] = 0;
}

/* exe+0x... inside the game's image, module+0x... inside another loaded one,
 * the address otherwise. The loader's list is walked without the loader lock
 * (a report must not wait for it), so callers hold a page-fault guard. */
static void ft_where( char *out, size_t size, ULONG_PTR addr )
{
    const LIST_ENTRY *head, *e;
    int n = 0;

    if (addr >= ft_exe_base && addr < ft_exe_end)
    {
        snprintf( out, size, "exe+0x%x", (unsigned int)(addr - ft_exe_base) );
        out[size - 1] = 0;
        return;
    }
    head = &NtCurrentTeb()->Peb->LdrData->InMemoryOrderModuleList;
    for (e = head->Flink; e && e != head && n < 4096; e = e->Flink, n++)
    {
        const LDR_DATA_TABLE_ENTRY *mod = CONTAINING_RECORD( e, LDR_DATA_TABLE_ENTRY, InMemoryOrderLinks );
        ULONG_PTR base = (ULONG_PTR)mod->DllBase;
        char name[40];

        if (addr < base || addr >= base + mod->SizeOfImage) continue;
        ft_narrow( name, sizeof(name), mod->BaseDllName.Buffer, mod->BaseDllName.Length / sizeof(WCHAR), FALSE );
        snprintf( out, size, "%s+0x%x", name, (unsigned int)(addr - base) );
        out[size - 1] = 0;
        return;
    }
    snprintf( out, size, "%p", (void *)addr );
    out[size - 1] = 0;
}

/* when an x64 call instruction ends at ret (a return address into the exe),
 * what it calls */
static BOOL ft_call( char *out, size_t size, ULONG_PTR ret )
{
    const BYTE *p = (const BYTE *)ret;
    char where[64];

    if (ret < ft_exe_base + 0x1000 || ret >= ft_exe_end) return FALSE;
    out[0] = 0;
    if (p[-6] == 0xff && p[-5] == 0x15)   /* call [rip+disp32]: an import or a pointer */
    {
        ULONG_PTR slot = ret + *(const INT *)(p - 4);

        if (slot >= ft_exe_base && slot + sizeof(ULONG_PTR) <= ft_exe_end)
            ft_where( where, sizeof(where), *(const ULONG_PTR *)slot );
        else
            snprintf( where, sizeof(where), "?" );
        where[sizeof(where) - 1] = 0;
        snprintf( out, size, "[exe+0x%x]=%s", (unsigned int)(slot - ft_exe_base), where );
    }
    else if (p[-5] == 0xe8)   /* call rel32 */
        ft_where( out, size, ret + *(const INT *)(p - 4) );
    else if (p[-6] == 0xff && (p[-5] & 0xf8) == 0x90 && (p[-5] & 7) != 4)   /* call [reg+disp32] */
        snprintf( out, size, "[r+0x%x]", (unsigned int)*(const INT *)(p - 4) );
    else if (p[-7] == 0xff && p[-6] == 0x94)   /* call [base+index*scale+disp32] */
        snprintf( out, size, "[r+r*s+0x%x]", (unsigned int)*(const INT *)(p - 4) );
    else if (p[-3] == 0xff && (p[-2] & 0xf8) == 0x50 && (p[-2] & 7) != 4)   /* call [reg+disp8] */
        snprintf( out, size, "[r+0x%x]", p[-1] );
    else if (p[-4] == 0xff && p[-3] == 0x54)   /* call [base+index*scale+disp8] */
        snprintf( out, size, "[r+r*s+0x%x]", p[-1] );
    else if (p[-2] == 0xff && (p[-1] & 0xf8) == 0xd0)   /* call reg */
        snprintf( out, size, "%s", "reg" );
    else if (p[-2] == 0xff && (p[-1] & 0xf8) == 0x10 && (p[-1] & 7) != 4 && (p[-1] & 7) != 5)   /* call [reg] */
        snprintf( out, size, "%s", "[reg]" );
    else
        return FALSE;
    out[size - 1] = 0;
    return TRUE;
}

/* up to max return addresses into the exe between sp and top that follow an x64
 * call, nearest first: " exe+0x...>callee" each */
static void ft_scan( char *out, size_t size, ULONG_PTR sp, ULONG_PTR top, int max )
{
    const ULONG_PTR *p;
    size_t pos = 0;
    int n = 0;

    out[0] = 0;
    if (!sp || top <= sp) return;
    if (top - sp > FT_SCAN_BYTES) top = sp + FT_SCAN_BYTES;
    for (p = (const ULONG_PTR *)((sp + 7) & ~(ULONG_PTR)7); (ULONG_PTR)(p + 1) <= top && n < max; p++)
    {
        char call[96], item[128];

        if (!ft_call( call, sizeof(call), *p )) continue;
        snprintf( item, sizeof(item), " exe+0x%x>%s", (unsigned int)(*p - ft_exe_base), call );
        item[sizeof(item) - 1] = 0;
        ft_cat( out, size, &pos, item );
        n++;
    }
}

static void ft_init(void)
{
    char name[64], line[240];

    if (!ft_exe_base || !ft_exe_end)
    {
        const IMAGE_DOS_HEADER *dos = (const IMAGE_DOS_HEADER *)NtCurrentTeb()->Peb->ImageBaseAddress;
        const IMAGE_NT_HEADERS *nt = (const IMAGE_NT_HEADERS *)((const char *)dos + dos->e_lfanew);

        ft_exe_base = (ULONG_PTR)dos;
        ft_exe_end = (ULONG_PTR)dos + nt->OptionalHeader.SizeOfImage;
    }
    if (InterlockedCompareExchange( &ft_banner, 1, 0 )) return;
    ft_narrow( name, sizeof(name), NtCurrentTeb()->Peb->ProcessParameters->ImagePathName.Buffer,
               NtCurrentTeb()->Peb->ProcessParameters->ImagePathName.Length / sizeof(WCHAR), TRUE );
    snprintf( line, sizeof(line), "%s on in %s: exe %p-%p; fibers parked %u ms or more are listed, a summary every %u ms",
              "fiber-trace-v1 madeira-bcd", name, (void *)ft_exe_base, (void *)ft_exe_end, FT_STUCK_MS, FT_REPORT_MS );
    line[sizeof(line) - 1] = 0;
    ft_out( line );
}

static struct ft_entry *ft_entry_of( const struct fiber_data *fiber )
{
    LONG i = fiber ? fiber->ft_index : 0;

    if (i < 1 || i > ft_count() || ft_table[i - 1].fiber != fiber) return NULL;
    return &ft_table[i - 1];
}

/* a new entry, NULL when the table is full; its fiber is set by the caller, last */
static struct ft_entry *ft_new( struct fiber_data *fiber )
{
    LONG i = InterlockedIncrement( &ft_used );

    if (i > FT_MAX)
    {
        fiber->ft_index = 0;
        if (!InterlockedExchange( &ft_full, 1 )) ft_out( "the table is full: later fibers are not traced" );
        return NULL;
    }
    fiber->ft_index = i;
    return &ft_table[i - 1];
}

/* the x64 call site of the fiber API called, from the current stack */
static void ft_caller( char *out, size_t size, const void *frame )
{
    __TRY
    {
        ft_scan( out, size, (ULONG_PTR)frame, (ULONG_PTR)NtCurrentTeb()->Tib.StackBase, 2 );
    }
    __EXCEPT_PAGE_FAULT
    {
        out[0] = 0;
    }
    __ENDTRY
}

static void ft_created( struct fiber_data *fiber, SIZE_T commit, SIZE_T reserve )
{
    struct ft_entry *e;
    char line[600], start[64], from[300];

    ft_init();
    if (!(e = ft_new( fiber ))) return;
    e->start = fiber->start;
    e->param = fiber->param;
    e->stack_base = fiber->stack_base;
    e->stack_alloc = fiber->stack_allocation;
    e->commit = commit;
    e->reserve = reserve;
    e->flags = fiber->flags;
    e->creator = ft_tid();
    InterlockedExchangePointer( (void **)&e->fiber, fiber );
    if (InterlockedIncrement( &ft_lines_made ) > FT_LINES) return;
    __TRY
    {
        ft_where( start, sizeof(start), (ULONG_PTR)fiber->start );
    }
    __EXCEPT_PAGE_FAULT
    {
        snprintf( start, sizeof(start), "?" );
    }
    __ENDTRY
    ft_caller( from, sizeof(from), __builtin_frame_address(0) );
    snprintf( line, sizeof(line), "#%d CreateFiberEx(commit 0x%x, reserve 0x%x, flags %#x, start %s, param %p) = %p, stack %p-%p; from%s",
              (int)fiber->ft_index, (unsigned int)commit, (unsigned int)reserve, (unsigned int)fiber->flags, start,
              fiber->param, fiber, fiber->stack_allocation, fiber->stack_base, from[0] ? from : " ?" );
    line[sizeof(line) - 1] = 0;
    ft_out( line );
}

static void ft_converted( struct fiber_data *fiber )
{
    struct ft_entry *e;
    char line[600], from[300];

    ft_init();
    if (!(e = ft_new( fiber ))) return;
    e->param = fiber->param;
    e->stack_base = fiber->stack_base;
    e->stack_alloc = fiber->stack_allocation;
    e->flags = fiber->flags;
    e->creator = e->last_tid = e->state = ft_tid();
    e->converted = 1;
    e->in = 1;
    InterlockedExchangePointer( (void **)&e->fiber, fiber );
    if (InterlockedIncrement( &ft_lines_made ) > FT_LINES) return;
    ft_caller( from, sizeof(from), __builtin_frame_address(0) );
    snprintf( line, sizeof(line), "#%d ConvertThreadToFiberEx(param %p, flags %#x) = %p, this thread, stack %p-%p; from%s",
              (int)fiber->ft_index, fiber->param, (unsigned int)fiber->flags, fiber, fiber->stack_allocation,
              fiber->stack_base, from[0] ? from : " ?" );
    line[sizeof(line) - 1] = 0;
    ft_out( line );
}

static void ft_wrong( const char *text )
{
    if (InterlockedIncrement( &ft_lines_wrong ) <= FT_LINES) ft_out( text );
}

/* DeleteFiber (deleted) or ConvertFiberToThread */
static void ft_gone( struct fiber_data *fiber, BOOL deleted )
{
    struct ft_entry *e = ft_entry_of( fiber );
    LONG prev, tid = ft_tid();
    BOOL self = fiber == NtCurrentTeb()->Tib.FiberData;
    char line[200];

    if (!e) return;
    prev = InterlockedExchange( &e->state, FT_DEAD );
    if (prev == FT_DEAD)
        snprintf( line, sizeof(line), "WRONG: %s of #%d, which is already gone", deleted ? "DeleteFiber" : "ConvertFiberToThread",
                  (int)fiber->ft_index );
    else if (!self && prev)
        snprintf( line, sizeof(line), "WRONG: DeleteFiber of #%d while thread %04x %s it", (int)fiber->ft_index,
                  (unsigned int)(prev & ~FT_SWITCHING), (prev & FT_SWITCHING) ? "is still leaving" : "runs" );
    else
        line[0] = 0;
    if (line[0])
    {
        line[sizeof(line) - 1] = 0;
        ft_wrong( line );
    }
    if (InterlockedIncrement( &ft_lines_gone ) > FT_LINES) return;
    snprintf( line, sizeof(line), "#%d %s, run %d times, last on %04x", (int)fiber->ft_index,
              !deleted ? "ConvertFiberToThread" : self ? "DeleteFiber of the running fiber: the thread exits" : "DeleteFiber",
              (int)e->in, (unsigned int)(self ? tid : e->last_tid) );
    line[sizeof(line) - 1] = 0;
    ft_out( line );
}

/* one fiber's line: state, start, how deep its stack is, and for a parked fiber
 * the x64 calls on its saved stack */
static void ft_entry_line( struct ft_entry *e, LONG index, ULONGLONG now, BOOL scan )
{
    char line[720], state[48], start[64], stack[600];
    LONG s = e->state;
    BOOL parked = !s && (e->in || e->converted);

    if (s == FT_DEAD) snprintf( state, sizeof(state), "gone" );
    else if (s & FT_SWITCHING) snprintf( state, sizeof(state), "being left by %04x", (unsigned int)(s & ~FT_SWITCHING) );
    else if (s) snprintf( state, sizeof(state), "running on %04x", (unsigned int)s );
    else if (!parked) snprintf( state, sizeof(state), "never run" );
    else snprintf( state, sizeof(state), "parked %u ms, last on %04x", (unsigned int)(now - e->parked_at),
                   (unsigned int)e->last_tid );
    state[sizeof(state) - 1] = 0;
    start[0] = stack[0] = 0;
    __TRY
    {
        if (e->converted) snprintf( start, sizeof(start), "a converted thread" );
        else
        {
            char where[64];
            ft_where( where, sizeof(where), (ULONG_PTR)e->start );
            snprintf( start, sizeof(start), "start %s", where );
        }
        start[sizeof(start) - 1] = 0;
#if defined(__x86_64__) || defined(__arm64ec__)
        if (scan && parked)
        {
            ULONG_PTR sp = e->fiber->context.Rsp, base = (ULONG_PTR)e->stack_base;
            char calls[520];

            ft_scan( calls, sizeof(calls), sp, base, FT_CALLS );
            snprintf( stack, sizeof(stack), "; stack used 0x%x of 0x%x; calls%s", (unsigned int)(base - sp),
                      (unsigned int)(base - (ULONG_PTR)e->stack_alloc), calls[0] ? calls : " none found" );
            stack[sizeof(stack) - 1] = 0;
        }
#endif
    }
    __EXCEPT_PAGE_FAULT
    {
        snprintf( stack, sizeof(stack), "; stack not readable" );
    }
    __ENDTRY
    snprintf( line, sizeof(line), "#%d %p %s, run %d times, %s%s", (int)(index + 1), e->fiber, state, (int)e->in, start, stack );
    line[sizeof(line) - 1] = 0;
    ft_out( line );
}

/* the summary, then the fibers newly parked for FT_STUCK_MS (all: every fiber) */
static void ft_report( ULONGLONG now, BOOL all )
{
    LONG i, n = ft_count(), running = 0, parked = 0, stuck = 0, idle = 0, leaving = 0, gone = 0, in = 0;
    LONG io[FT_IO];
    char line[600];

    for (i = 0; i < n; i++)
    {
        struct ft_entry *e = &ft_table[i];
        LONG s = e->state;

        if (!e->fiber) continue;
        in += e->in;
        if (s == FT_DEAD) gone++;
        else if (s & FT_SWITCHING) leaving++;
        else if (s) running++;
        else if (!e->in && !e->converted) idle++;
        else
        {
            parked++;
            if (now - e->parked_at >= FT_STUCK_MS) stuck++;
        }
    }
    for (i = 0; i < FT_IO; i++)
    {
        LONG v = ft_io[i];
        io[i] = v - ft_io_last[i];
        ft_io_last[i] = v;
    }
    snprintf( line, sizeof(line), "%s%d fibers: %d running, %d parked (%d for %u ms or more), %d never run, %d being left, %d gone;"
              " %d switches in %u ms; overlapped ReadFile %d done at once, %d pending, %d failed; ReadFileEx %d;"
              " completion-port entries %d; GetOverlappedResult %d",
              all ? "at the exception: " : "", (int)n, (int)running, (int)parked, (int)stuck, FT_STUCK_MS, (int)idle,
              (int)leaving, (int)gone, (int)(in - ft_in_last), (unsigned int)(now - ft_report_last), (int)io[0], (int)io[1],
              (int)io[2], (int)io[3], (int)io[4], (int)io[5] );
    line[sizeof(line) - 1] = 0;
    ft_out( line );
    ft_in_last = in;
    ft_report_last = now;
    for (i = 0; i < n; i++)
    {
        struct ft_entry *e = &ft_table[i];

        if (!e->fiber) continue;
        if (all)
        {
            if (e->state != FT_DEAD) ft_entry_line( e, i, now, TRUE );
        }
        else if (!e->state && !e->stuck && (e->in || e->converted) && now - e->parked_at >= FT_STUCK_MS)
        {
            e->stuck = 1;
            ft_entry_line( e, i, now, TRUE );
        }
    }
}

static void ft_switch( struct fiber_data *cur, struct fiber_data *new )
{
    struct ft_entry *c = ft_entry_of( cur ), *n = ft_entry_of( new );
    LONG tid = ft_tid(), prev;
    ULONGLONG now = GetTickCount64();
    LONGLONG next = ft_next_report;
    char line[200];

    if (!ft_banner) ft_init();
    if ((ULONGLONG)next <= now && InterlockedCompareExchange64( &ft_next_report, now + FT_REPORT_MS, next ) == next)
    {
        if (!next) ft_report_last = now;
        else ft_report( now, FALSE );
    }
    if (!cur)
    {
        snprintf( line, sizeof(line), "WRONG: SwitchToFiber(%p) on a thread that is not a fiber", new );
        ft_wrong( line );
        return;
    }
    if (cur == new)
    {
        if (n) n->in++;
        return;
    }
    if (!n)
    {
        snprintf( line, sizeof(line), "WRONG: SwitchToFiber(%p): not a fiber this process has (deleted, or never created)", new );
        ft_wrong( line );
    }
    else if ((prev = InterlockedCompareExchange( &n->state, tid, 0 )) == FT_DEAD)
    {
        snprintf( line, sizeof(line), "WRONG: SwitchToFiber(#%d), a deleted fiber", (int)new->ft_index );
        ft_wrong( line );
    }
    else
    {
        if (prev)
        {
            if (prev & FT_SWITCHING)
                snprintf( line, sizeof(line), "WRONG: SwitchToFiber(#%d) while thread %04x is still leaving it (its registers are not saved yet)",
                          (int)new->ft_index, (unsigned int)(prev & ~FT_SWITCHING) );
            else
                snprintf( line, sizeof(line), "WRONG: SwitchToFiber(#%d), which runs on thread %04x", (int)new->ft_index,
                          (unsigned int)prev );
            line[sizeof(line) - 1] = 0;
            ft_wrong( line );
            InterlockedExchange( &n->state, tid );
        }
        n->came_from = c ? (LONG)(c - ft_table) + 1 : 0;
        n->in++;
        n->last_tid = tid;
        if (n->stuck)
        {
            n->stuck = 0;
            if (InterlockedIncrement( &ft_lines_back ) <= FT_LINES)
            {
                snprintf( line, sizeof(line), "#%d runs again after %u ms parked", (int)new->ft_index,
                          (unsigned int)(now - n->parked_at) );
                ft_out( line );
            }
        }
    }
    if (c)
    {
        c->parked_at = now;
        InterlockedExchange( &c->state, tid | FT_SWITCHING );
    }
}

/* in the fiber just entered: the fiber its thread left is parked now */
static void ft_after_switch( struct fiber_data *me )
{
    struct ft_entry *e = ft_entry_of( me );
    LONG i;

    if (!e || !(i = e->came_from)) return;
    e->came_from = 0;
    if (i < 1 || i > ft_count()) return;
    InterlockedCompareExchange( &ft_table[i - 1].state, 0, ft_tid() | FT_SWITCHING );
}

static void ft_start_end( struct fiber_data *fiber, DWORD code, BOOL exception )
{
    char line[160];

    if (exception)
        snprintf( line, sizeof(line), "#%d raised %08x and nothing handled it: the thread is terminated", (int)fiber->ft_index,
                  (unsigned int)code );
    else
        snprintf( line, sizeof(line), "#%d start routine returned: the thread exits", (int)fiber->ft_index );
    ft_wrong( line );
}

/* An MSVC C++ exception (0xe06d7363) that reached UnhandledExceptionFilter,
 * fibers or not. Its parameters are { magic, object, ThrowInfo, image base };
 * ThrowInfo, its catchable type array, each catchable type and each type
 * descriptor are found through RVAs from that image. Logged: the thrown types
 * (decorated names, most derived first), what() when one of them is
 * std::exception (vftable, then __std_exception_data's text pointer, at that
 * type's this-displacement), the object's vftable and first words, and the x64
 * calls on the throwing stack. Ratchet & Clank: Rift Apart (build 550) died of
 * one its own crash handler reports as "Unknown exception". Every read is
 * under a page-fault guard; at most four exceptions are described. */
static void ft_cxx( const EXCEPTION_RECORD *rec, const CONTEXT *ctx )
{
    static LONG cxx_seen;
    char line[700], name[100], text[200], where[64], calls[600];
    size_t pos = 0;
    ULONG_PTR obj, base, info;
    volatile INT what_disp = -1;

    if (rec->ExceptionCode != 0xe06d7363 || rec->NumberParameters < 4) return;
    if (InterlockedIncrement( &cxx_seen ) > 4) return;
    ft_init();
    obj = rec->ExceptionInformation[1];
    info = rec->ExceptionInformation[2];
    base = rec->ExceptionInformation[3];
    snprintf( line, sizeof(line), "unhandled C++ exception: object %p, ThrowInfo %p, image %p, types:",
              (void *)obj, (void *)info, (void *)base );
    line[sizeof(line) - 1] = 0;
    pos = strlen( line );
    __TRY
    {
        /* ThrowInfo: attributes, unwind, forward compat, catchable type array */
        const INT *types = (const INT *)(base + ((const INT *)info)[3]);
        INT i, n = types[0];

        for (i = 0; i < n && i < 8; i++)
        {
            /* CatchableType: properties, type descriptor, mdisp, pdisp, vdisp, size, copy;
             * TypeDescriptor: vftable, spare, then the decorated name */
            const INT *ct = (const INT *)(base + types[1 + i]);
            const char *tn = (const char *)(base + ct[1] + 2 * sizeof(void *));
            size_t k;

            for (k = 0; k + 1 < sizeof(name) && tn[k]; k++) name[k] = (tn[k] >= 0x20 && tn[k] < 0x7f) ? tn[k] : '?';
            name[k] = 0;
            ft_cat( line, sizeof(line), &pos, " " );
            ft_cat( line, sizeof(line), &pos, name );
            if (!strcmp( name, ".?AVexception@std@@" )) what_disp = ct[2];
        }
    }
    __EXCEPT_PAGE_FAULT
    {
        ft_cat( line, sizeof(line), &pos, " (unreadable)" );
    }
    __ENDTRY
    ft_out( line );

    if (what_disp >= 0)
    {
        text[0] = 0;
        __TRY
        {
            const char *w = *(const char *const *)(obj + what_disp + sizeof(void *));
            size_t k;

            for (k = 0; w && k + 1 < sizeof(text) && w[k]; k++) text[k] = (w[k] >= 0x20 && w[k] < 0x7f) ? w[k] : '?';
            text[k] = 0;
        }
        __EXCEPT_PAGE_FAULT
        {
            snprintf( text, sizeof(text), "(unreadable)" );
        }
        __ENDTRY
        snprintf( line, sizeof(line), "what(): \"%s\"", text );
        line[sizeof(line) - 1] = 0;
        ft_out( line );
    }

    __TRY
    {
        const ULONG_PTR *q = (const ULONG_PTR *)obj;

        ft_where( where, sizeof(where), q[0] );
        snprintf( line, sizeof(line), "object: vftable %s, then %p %p %p %p %p", where,
                  (void *)q[1], (void *)q[2], (void *)q[3], (void *)q[4], (void *)q[5] );
    }
    __EXCEPT_PAGE_FAULT
    {
        snprintf( line, sizeof(line), "object %p unreadable", (void *)obj );
    }
    __ENDTRY
    line[sizeof(line) - 1] = 0;
    ft_out( line );

    if (!ctx) return;
    __TRY
    {
        ft_scan( calls, sizeof(calls), ctx->Rsp, (ULONG_PTR)NtCurrentTeb()->Tib.StackBase, 8 );
    }
    __EXCEPT_PAGE_FAULT
    {
        calls[0] = 0;
    }
    __ENDTRY
    snprintf( line, sizeof(line), "x64 calls on the throwing stack:%s", calls[0] ? calls : " none found" );
    line[sizeof(line) - 1] = 0;
    ft_out( line );
}

void madeira_fibertrace_dump( const EXCEPTION_RECORD *rec, const CONTEXT *ctx )
{
    struct ft_entry *e;
    char line[200], where[64];

    ft_cxx( rec, ctx );
    if (!ft_count() || InterlockedCompareExchange( &ft_dumped, 1, 0 )) return;
    __TRY
    {
        ft_where( where, sizeof(where), (ULONG_PTR)rec->ExceptionAddress );
    }
    __EXCEPT_PAGE_FAULT
    {
        snprintf( where, sizeof(where), "%p", rec->ExceptionAddress );
    }
    __ENDTRY
    e = ft_entry_of( NtCurrentTeb()->Tib.FiberData );
    snprintf( line, sizeof(line), "unhandled exception %08x at %s on %s%d; every fiber follows", (unsigned int)rec->ExceptionCode,
              where, e ? "fiber #" : "no traced fiber ", e ? (int)(e - ft_table) + 1 : 0 );
    line[sizeof(line) - 1] = 0;
    ft_out( line );
    ft_report( GetTickCount64(), TRUE );
}

void madeira_fibertrace_io( int what, LONG count )
{
    if (what >= 0 && what < FT_IO) InterlockedExchangeAdd( &ft_io[what], count );
}
/* madeira-bcd fiber trace end */

'''

# (file, anchor, replacement): every anchor must occur exactly once.
EDITS = (
    ("dlls/kernelbase/kernelbase.h",
     "extern BOOL is_console_handle( HANDLE );\n",
     "extern BOOL is_console_handle( HANDLE );\n"
     "/* madeira-bcd fiber trace (thread.c; tools/patch-wine-kernelbase-fibertrace.py) */\n"
     "extern void madeira_fibertrace_dump( const EXCEPTION_RECORD *rec, const CONTEXT *ctx );\n"
     "extern void madeira_fibertrace_io( int what, LONG count );\n"),
    ("dlls/kernelbase/thread.c",
     "    struct fiber_actctx   actctx;            /*       activation context state */\n};\n",
     "    struct fiber_actctx   actctx;            /*       activation context state */\n"
     "    LONG                  ft_index;          /*       madeira-bcd fiber trace: table index + 1 */\n};\n"),
    ("dlls/kernelbase/thread.c",
     "/* call the fiber initial function once we have switched stack */\nstatic void CDECL start_fiber(void)\n",
     TRACE_C +
     "/* call the fiber initial function once we have switched stack */\nstatic void CDECL start_fiber(void)\n"),
    ("dlls/kernelbase/thread.c",
     "    LPFIBER_START_ROUTINE start = fiber->start;\n\n    __TRY\n    {\n        start( fiber->param );\n"
     "        RtlExitUserThread( 1 );\n    }\n    __EXCEPT(UnhandledExceptionFilter)\n    {\n"
     "        TerminateThread( GetCurrentThread(), GetExceptionCode() );\n",
     "    LPFIBER_START_ROUTINE start = fiber->start;\n\n    ft_after_switch( fiber );\n    __TRY\n    {\n"
     "        start( fiber->param );\n        ft_start_end( fiber, 0, FALSE );\n        RtlExitUserThread( 1 );\n    }\n"
     "    __EXCEPT(UnhandledExceptionFilter)\n    {\n        ft_start_end( fiber, GetExceptionCode(), TRUE );\n"
     "        TerminateThread( GetCurrentThread(), GetExceptionCode() );\n"),
    ("dlls/kernelbase/thread.c",
     "    init_fiber_context( fiber );\n    return fiber;\n",
     "    init_fiber_context( fiber );\n    ft_created( fiber, stack_commit, stack_reserve );\n    return fiber;\n"),
    ("dlls/kernelbase/thread.c",
     "    if (fiber)\n    {\n        relocate_thread_actctx_stack( &NtCurrentTeb()->ActivationContextStack );\n"
     "        NtCurrentTeb()->Tib.FiberData = NULL;\n",
     "    if (fiber)\n    {\n        ft_gone( fiber, FALSE );\n"
     "        relocate_thread_actctx_stack( &NtCurrentTeb()->ActivationContextStack );\n"
     "        NtCurrentTeb()->Tib.FiberData = NULL;\n"),
    ("dlls/kernelbase/thread.c",
     "    NtCurrentTeb()->Tib.FiberData = fiber;\n    return fiber;\n",
     "    NtCurrentTeb()->Tib.FiberData = fiber;\n    ft_converted( fiber );\n    return fiber;\n"),
    ("dlls/kernelbase/thread.c",
     "    if (!fiber) return;\n    if (fiber == NtCurrentTeb()->Tib.FiberData)\n",
     "    if (!fiber) return;\n    ft_gone( fiber, TRUE );\n    if (fiber == NtCurrentTeb()->Tib.FiberData)\n"),
    ("dlls/kernelbase/thread.c",
     "    struct fiber_data *current_fiber = NtCurrentTeb()->Tib.FiberData;\n\n"
     "    current_fiber->except      = NtCurrentTeb()->Tib.ExceptionList;\n",
     "    struct fiber_data *current_fiber = NtCurrentTeb()->Tib.FiberData;\n\n"
     "    ft_switch( current_fiber, new_fiber );\n"
     "    current_fiber->except      = NtCurrentTeb()->Tib.ExceptionList;\n"),
    ("dlls/kernelbase/thread.c",
     "    switch_fiber( &current_fiber->context, &new_fiber->context );\n}\n",
     "    switch_fiber( &current_fiber->context, &new_fiber->context );\n    ft_after_switch( current_fiber );\n}\n"),
    ("dlls/kernelbase/debug.c",
     "    const EXCEPTION_RECORD *rec = epointers->ExceptionRecord;\n    BOOL nested;\n\n",
     "    const EXCEPTION_RECORD *rec = epointers->ExceptionRecord;\n    BOOL nested;\n\n"
     "    madeira_fibertrace_dump( rec, epointers->ContextRecord );   /* madeira-bcd fiber trace */\n"),
    ("dlls/kernelbase/file.c",
     "    status = NtReadFile( file, event, NULL, cvalue, io_status, buffer, count, poffset, NULL);\n",
     "    status = NtReadFile( file, event, NULL, cvalue, io_status, buffer, count, poffset, NULL);\n"
     "    if (overlapped)   /* madeira-bcd fiber trace */\n"
     "        madeira_fibertrace_io( status == STATUS_PENDING ? 1 : (status && status != STATUS_END_OF_FILE) ? 2 : 0, 1 );\n"),
    ("dlls/kernelbase/file.c",
     "    status = NtReadFile( file, NULL, read_write_apc, completion, io, buffer, count, &offset, NULL);\n",
     "    status = NtReadFile( file, NULL, read_write_apc, completion, io, buffer, count, &offset, NULL);\n"
     "    madeira_fibertrace_io( 3, 1 );   /* madeira-bcd fiber trace */\n"),
    ("dlls/kernelbase/file.c",
     "    }\n\n    *result = overlapped->InternalHigh;\n",
     "    }\n\n    madeira_fibertrace_io( 5, 1 );   /* madeira-bcd fiber trace */\n    *result = overlapped->InternalHigh;\n"),
    ("dlls/kernelbase/file.c",
     "        SetLastError( ERROR_IO_INCOMPLETE );\n        return FALSE;\n    }\n    *result = overlapped->InternalHigh;\n",
     "        SetLastError( ERROR_IO_INCOMPLETE );\n        return FALSE;\n    }\n"
     "    madeira_fibertrace_io( 5, 1 );   /* madeira-bcd fiber trace */\n    *result = overlapped->InternalHigh;\n"),
    ("dlls/kernelbase/sync.c",
     "    if (status == STATUS_SUCCESS)\n    {\n        *count = iosb.Information;\n",
     "    if (status == STATUS_SUCCESS)\n    {\n        madeira_fibertrace_io( 4, 1 );   /* madeira-bcd fiber trace */\n"
     "        *count = iosb.Information;\n"),
    ("dlls/kernelbase/sync.c",
     "                                  written, get_nt_timeout( &time, timeout ), alertable );\n"
     "    if (ret == STATUS_SUCCESS) return TRUE;\n",
     "                                  written, get_nt_timeout( &time, timeout ), alertable );\n"
     "    if (ret == STATUS_SUCCESS)   /* madeira-bcd fiber trace */\n"
     "    {\n        madeira_fibertrace_io( 4, written ? (LONG)*written : 0 );\n        return TRUE;\n    }\n"),
)


def patch(wine):
    texts = {}
    for name in FILES:
        path = os.path.join(wine, name)
        if not os.path.isfile(path):
            sys.exit("patch-wine-kernelbase-fibertrace: no " + path)
        texts[name] = open(path).read()
    if "madeira-bcd fiber trace" in texts["dlls/kernelbase/thread.c"]:
        print("already patched")
        return
    for name, anchor, repl in EDITS:
        if texts[name].count(anchor) != 1:
            sys.exit("patch-wine-kernelbase-fibertrace: anchor found %d times in %s:\n%s"
                     % (texts[name].count(anchor), name, anchor))
        texts[name] = texts[name].replace(anchor, repl)
    for name in FILES:
        open(os.path.join(wine, name), "w").write(texts[name])
    print("patched %s (%d edits in %d files)" % (os.path.join(wine, "dlls/kernelbase"), len(EDITS), len(FILES)))


def pe_exports(path):
    d = open(path, "rb").read()
    if d[:2] != b"MZ":
        sys.exit("verify: %s is not a PE image" % path)
    pe = struct.unpack_from("<I", d, 0x3c)[0]
    machine, nsec = struct.unpack_from("<HH", d, pe + 4)
    optsz = struct.unpack_from("<H", d, pe + 20)[0]
    opt = pe + 24
    secs = []
    for i in range(nsec):
        s = opt + optsz + 40 * i
        vsz, va, rsz, raw = struct.unpack_from("<IIII", d, s + 8)
        secs.append((va, max(vsz, rsz), raw))

    def off(rva):
        for va, size, raw in secs:
            if va <= rva < va + size:
                return rva - va + raw
        raise ValueError("rva %#x outside the sections" % rva)

    exp_rva = struct.unpack_from("<I", d, opt + 112)[0]
    e = off(exp_rva)
    nnames, anames = struct.unpack_from("<I", d, e + 24)[0], struct.unpack_from("<I", d, e + 32)[0]
    names = set()
    for i in range(nnames):
        no = off(struct.unpack_from("<I", d, off(anames) + 4 * i)[0])
        names.add(d[no:d.index(b"\0", no)].decode("latin-1"))
    return d, machine, names


def verify(path, shipped):
    d, machine, names = pe_exports(path)
    _, smachine, snames = pe_exports(shipped)
    bad = []
    if MARKER.encode() not in d:
        bad.append("no '%s' string: not built from the patched tree" % MARKER)
    if machine != smachine:
        bad.append("machine %#x, the shipped kernelbase.dll's is %#x" % (machine, smachine))
    if names != snames:
        bad.append("exports differ from the shipped kernelbase.dll: only here %s, only there %s"
                   % (sorted(names - snames)[:8], sorted(snames - names)[:8]))
    if bad:
        sys.exit("verify %s:\n  %s" % (path, "\n  ".join(bad)))
    print("verify %s: fiber trace present, the %d exports of %s" % (path, len(names), shipped))


if __name__ == "__main__":
    if len(sys.argv) == 4 and sys.argv[1] == "--verify":
        verify(sys.argv[2], sys.argv[3])
    elif len(sys.argv) == 2:
        patch(sys.argv[1])
    else:
        sys.exit(__doc__)

#!/usr/bin/env python3
"""env.MADEIRA_DXMT_FAULT_INFO = 1: which DXMT encoder a GPU hang or fault came from.

GTA V Legacy (DirectX 11, build 546, log GTA5.exe 2026-10-15 12:35:46) entered
Story Mode's loading and the GPU stopped: "Caused GPU Timeout Error" on
two command buffers, the others "victim of GPU error/recovery", and the phone
froze with it. DXMT logs only "Device error at frame N"; Metal names the
encoder at fault only for a command buffer created with
MTLCommandBufferErrorOptionEncoderExecutionStatus, and DXMT labels only its
clear/resolve/blit passes.

With the switch on (the game's own file; off by default, nothing changes
without it):
  * winemetal (unix): MTLCommandQueue commandBuffer creates its buffers with
    encoder execution status, as madeira_ctl op 8 does for madeira_d3d12;
  * DXMT: every render, compute and blit encoder gets a label, "rp#<seq>
    <w>x<h> cmds=<n> gs=<0|1> ts=<0|1> gs_tasks=<n> ts_tasks=<n>",
    "cs#<seq> cmds=<n>", "blit#<seq> cmds=<n>", where seq is the encoding
    sequence number (the frame's chunk) and cmds the commands it encodes;
  * DXMT: after "Device error at frame N" a "[fault-enc]" line lists the
    FAULTED, affected and unknown encoders of that command buffer, with the
    completed and pending counts (madeira_ctl op 9, 64-bit only; the wow64
    entry does not forward it).

Encoder execution status costs some GPU bookkeeping, so it is a diagnostic
only. Idempotent; fails by name if an anchor moves. Run from the repository
root.
"""
import pathlib
import sys

MARKER = "madeira-bcd: DXMT fault info"

EDITS = {
    "dxmt/src/winemetal/unix/winemetal_unix.c": [(
        """  params->ret = (obj_handle_t)[(id<MTLCommandQueue>)params->handle commandBuffer];
  return STATUS_SUCCESS;
}
""",
        """  {   /* madeira-bcd: DXMT fault info -- env.MADEIRA_DXMT_FAULT_INFO = 1 creates
       * command buffers with encoder execution status, so a GPU fault or hang
       * names its encoder (tools/patch-dxmt-d3d11-fault-info.py). */
    const char *fi = getenv("MADEIRA_DXMT_FAULT_INFO");
    if (fi && fi[0] == '1') {
      MTLCommandBufferDescriptor *desc = [MTLCommandBufferDescriptor new];
      desc.errorOptions = MTLCommandBufferErrorOptionEncoderExecutionStatus;
      params->ret = (obj_handle_t)[(id<MTLCommandQueue>)params->handle commandBufferWithDescriptor:desc];
      [desc release];
      return STATUS_SUCCESS;
    }
  }
  params->ret = (obj_handle_t)[(id<MTLCommandQueue>)params->handle commandBuffer];
  return STATUS_SUCCESS;
}
""",
    )],
    "dxmt/src/dxmt/dxmt_command_queue.cpp": [(
        """      ERR("Device error at frame ", chunk.frame_, ": ", chunk.attached_cmdbuf.error().description().getUTF8String());
      MarkDeviceError();
""",
        """      ERR("Device error at frame ", chunk.frame_, ": ", chunk.attached_cmdbuf.error().description().getUTF8String());
#ifndef __i386__
      /* madeira-bcd: DXMT fault info -- the encoders of this command buffer
       * that faulted, were affected or are unknown (madeira_ctl op 9). */
      static const bool madeira_fi = env::getEnvVar("MADEIRA_DXMT_FAULT_INFO").starts_with("1");
      if (madeira_fi) {
        char fi_text[2048];
        struct { uint64_t cb, buf, size; } fi_req = {
            (uint64_t)chunk.attached_cmdbuf.handle, (uint64_t)(uintptr_t)fi_text, sizeof fi_text};
        struct madeira_ctl_args fi_args = {};
        fi_text[0] = 0;
        fi_args.op = 9;
        fi_args.ptr = (uint64_t)(uintptr_t)&fi_req;
        MadeiraCtl(&fi_args);
        if (fi_args.ret)
          ERR("[fault-enc] madeira-bcd frame ", chunk.frame_, ": ", fi_text);
      }
#endif
      MarkDeviceError();
""",
    )],
    "dxmt/src/dxmt/dxmt_context.cpp": [
        ("""      auto encoder = cmdbuf.renderCommandEncoder(render_pass_info);
      encoder.setVertexBuffer(gpu_buffer_, 0, 16);
""",
         """      auto encoder = cmdbuf.renderCommandEncoder(render_pass_info);
      if (madeira_fault_info_on()) {   /* madeira-bcd: DXMT fault info */
        char label[160];
        snprintf(label, sizeof label, "rp#%llu %ux%u cmds=%u gs=%d ts=%d gs_tasks=%u ts_tasks=%u",
                 (unsigned long long)seq_id_, data->render_target_width, data->render_target_height,
                 madeira_cmd_count(&data->cmd_head), (int)data->use_geometry, (int)data->use_tessellation,
                 (unsigned)data->gs_arg_marshal_tasks.size(), (unsigned)data->ts_arg_marshal_tasks.size());
        encoder.setLabel(WMT::String::string(label, WMTUTF8StringEncoding));
      }
      encoder.setVertexBuffer(gpu_buffer_, 0, 16);
"""),
        ("""      auto encoder = cmdbuf.computeCommandEncoder(false);
      struct wmtcmd_compute_setbuffer setcmd;
""",
         """      auto encoder = cmdbuf.computeCommandEncoder(false);
      if (madeira_fault_info_on()) {   /* madeira-bcd: DXMT fault info */
        char label[96];
        snprintf(label, sizeof label, "cs#%llu cmds=%u", (unsigned long long)seq_id_,
                 madeira_cmd_count(&data->cmd_head));
        encoder.setLabel(WMT::String::string(label, WMTUTF8StringEncoding));
      }
      struct wmtcmd_compute_setbuffer setcmd;
"""),
        ("""      auto encoder = cmdbuf.blitCommandEncoder();
      encoder.encodeCommands(&data->cmd_head);
""",
         """      auto encoder = cmdbuf.blitCommandEncoder();
      if (madeira_fault_info_on()) {   /* madeira-bcd: DXMT fault info */
        char label[96];
        snprintf(label, sizeof label, "blit#%llu cmds=%u", (unsigned long long)seq_id_,
                 madeira_cmd_count(&data->cmd_head));
        encoder.setLabel(WMT::String::string(label, WMTUTF8StringEncoding));
      }
      encoder.encodeCommands(&data->cmd_head);
"""),
        ('#include "dxmt_mem_census.hpp"\n',
         '#include "dxmt_mem_census.hpp"\n'
         '#include "util_env.hpp"\n'
         '#include <cstdio>\n'
         '\n'
         '/* madeira-bcd: DXMT fault info -- env.MADEIRA_DXMT_FAULT_INFO = 1 labels every\n'
         ' * encoder (tools/patch-dxmt-d3d11-fault-info.py); off, nothing is labelled. */\n'
         'static bool\n'
         'madeira_fault_info_on() {\n'
         '  static const bool on = dxmt::env::getEnvVar("MADEIRA_DXMT_FAULT_INFO").starts_with("1");\n'
         '  return on;\n'
         '}\n'
         '\n'
         'template <typename Head>\n'
         'static unsigned\n'
         'madeira_cmd_count(Head *head) {\n'
         '  unsigned n = 0;\n'
         '  for (auto *c = (wmtcmd_base *)head->next.get_accessible_or_null(); c && n < 1000000;\n'
         '       c = (wmtcmd_base *)c->next.get_accessible_or_null())\n'
         '    n++;\n'
         '  return n;\n'
         '}\n'),
    ],
}


def main():
    for rel, pairs in EDITS.items():
        path = pathlib.Path(rel)
        s = path.read_text()
        if MARKER in s:
            print(f"{path.name}: DXMT fault info already patched")
            continue
        for old, new in pairs:
            if s.count(old) != 1:
                print(f"::error::{rel}: DXMT fault info anchor not found once -- dxmt moved, review this patch:\n{old}")
                return 1
            s = s.replace(old, new)
        if MARKER not in s:
            print(f"::error::{rel}: marker missing after the edit")
            return 1
        path.write_text(s)
        print(f"{path.name}: DXMT fault info (env.MADEIRA_DXMT_FAULT_INFO = 1) patched")
    return 0


if __name__ == "__main__":
    sys.exit(main())

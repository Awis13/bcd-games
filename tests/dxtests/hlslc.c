// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
//
// hlslc.c: a command line HLSL compiler on top of Wine's bundled vkd3d-shader library, for the
// Mac that builds BCD. tools/gen-dx-test-shaders.sh builds it with the library's sources from the
// wine submodule and runs it to turn tests/dxtests/cube.hlsl into Shader Model 4.0 and 5.0 bytecode.
//
//   hlslc <input.hlsl> <entry point> <profile, such as vs_4_0> <output.dxbc>

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "vkd3d_shader.h"

int main(int argc, char **argv)
{
    FILE *f;
    long n;
    char *src, *messages = NULL;
    struct vkd3d_shader_code out;
    int rc;

    if (argc != 5) {
        fprintf(stderr, "usage: hlslc input.hlsl entry profile output.dxbc\n");
        return 1;
    }
    f = fopen(argv[1], "rb");
    if (!f) { perror(argv[1]); return 1; }
    fseek(f, 0, SEEK_END);
    n = ftell(f);
    fseek(f, 0, SEEK_SET);
    src = malloc((size_t)n + 1);
    if (!src || fread(src, 1, (size_t)n, f) != (size_t)n) { fprintf(stderr, "cannot read %s\n", argv[1]); return 1; }
    fclose(f);
    src[n] = 0;

    {
        struct vkd3d_shader_hlsl_source_info hlsl;
        struct vkd3d_shader_compile_info info;
        memset(&hlsl, 0, sizeof hlsl);
        hlsl.type = VKD3D_SHADER_STRUCTURE_TYPE_HLSL_SOURCE_INFO;
        hlsl.entry_point = argv[2];
        hlsl.profile = argv[3];
        memset(&info, 0, sizeof info);
        info.type = VKD3D_SHADER_STRUCTURE_TYPE_COMPILE_INFO;
        info.next = &hlsl;
        info.source.code = src;
        info.source.size = (size_t)n;
        info.source_type = VKD3D_SHADER_SOURCE_HLSL;
        info.target_type = VKD3D_SHADER_TARGET_DXBC_TPF;
        info.log_level = VKD3D_SHADER_LOG_WARNING;
        info.source_name = argv[1];
        memset(&out, 0, sizeof out);
        rc = vkd3d_shader_compile(&info, &out, &messages);
    }
    if (messages && *messages) fprintf(stderr, "%s", messages);
    if (rc < 0) { fprintf(stderr, "hlslc: compile failed (%d)\n", rc); return 2; }
    f = fopen(argv[4], "wb");
    if (!f || fwrite(out.code, 1, out.size, f) != out.size) { fprintf(stderr, "cannot write %s\n", argv[4]); return 1; }
    fclose(f);
    fprintf(stderr, "%s %s: %zu bytes\n", argv[2], argv[3], out.size);
    return 0;
}

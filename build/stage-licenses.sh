#!/bin/bash
# Refresh the generated licence copies the bundle carries (app/Madeira/licenses is
# a bundled folder reference). The Xcode build phase fails when these are stale.
set -eu
R="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cp "$R/COPYING" "$R/app/Madeira/licenses/LICENSE-MADEIRA-GPL-3.0.txt"
cp "$R/LICENSE-EXCEPTION.md" "$R/app/Madeira/licenses/LICENSE-MADEIRA-EXCEPTION.txt"
# BCD's additional terms (GPL-3.0 section 7) travel with every package (legal audit, 2026-10-18).
cp "$R/LICENSE-BCD.md" "$R/app/Madeira/licenses/LICENSE-BCD.txt"
echo "licence copies refreshed in app/Madeira/licenses"

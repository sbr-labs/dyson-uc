#!/usr/bin/env bash
# Phase 0 diagnostic — verify each command actually changes fan state.
set -euo pipefail
cd "$(dirname "$0")"
# shellcheck disable=SC1091
source .venv/bin/activate
exec python3 phase0_diag.py

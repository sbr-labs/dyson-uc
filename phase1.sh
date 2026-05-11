#!/usr/bin/env bash
# Phase 1 local end-to-end test: drives the driver.py internals against the
# real TP09. No UCR3 needed for this stage.
set -euo pipefail
cd "$(dirname "$0")"
# shellcheck disable=SC1091
source .venv/bin/activate
exec python3 phase1_local.py

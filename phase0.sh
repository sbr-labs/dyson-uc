#!/usr/bin/env bash
# Phase 0 — one-command run for Termius/iPhone.
# Activates venv, installs deps if missing, runs the interactive walkthrough.
set -euo pipefail

cd "$(dirname "$0")"

if [ ! -d .venv ]; then
  echo "Creating venv..."
  python3 -m venv .venv
fi

# shellcheck disable=SC1091
source .venv/bin/activate

if ! python3 -c "import libdyson" 2>/dev/null; then
  echo "Installing libdyson-neon..."
  pip install --quiet --upgrade pip
  pip install --quiet libdyson-neon
fi

exec python3 phase0.py

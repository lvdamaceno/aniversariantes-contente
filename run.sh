#!/usr/bin/env bash
# Execução local (no Render roda via Dockerfile).
set -euo pipefail
cd "$(dirname "$0")"
[ "$(uname)" = "Darwin" ] && export DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib
exec .venv/bin/python -m aniversariantes "$@"

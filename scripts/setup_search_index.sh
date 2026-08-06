#!/bin/sh
# setup_search_index.sh — Wrapper that invokes setup_search_index.py via the repo venv.
# azd runs hooks from the project root, so resolve paths from this script's location.

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# load_python_env.sh provisions the venv at app/backend/.venv
. "$SCRIPT_DIR/load_python_env.sh"

VENV_PYTHON="$REPO_ROOT/app/backend/.venv/bin/python"
if [ ! -f "$VENV_PYTHON" ]; then
    VENV_PYTHON="$REPO_ROOT/.venv/bin/python"
fi

echo "Running setup_search_index.py..."
"$VENV_PYTHON" "$REPO_ROOT/app/backend/setup_search_index.py"

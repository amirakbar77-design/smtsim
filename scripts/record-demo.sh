#!/usr/bin/env bash
# Record demo/demo.gif from demo/demo.tape.
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root"

if ! command -v vhs >/dev/null 2>&1; then
    echo "vhs is not installed. Install it with:" >&2
    echo "    brew install vhs        # macOS" >&2
    echo "    go install github.com/charmbracelet/vhs@latest" >&2
    exit 1
fi

if [ ! -x .venv/bin/smtsim ]; then
    echo "Project virtualenv not found. Run 'uv sync' first." >&2
    exit 1
fi

export PATH="$root/.venv/bin:$PATH"
mkdir -p runs
vhs demo/demo.tape
echo "Wrote demo/demo.gif ($(du -h demo/demo.gif | cut -f1))"

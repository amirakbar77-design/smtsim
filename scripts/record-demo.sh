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

export PATH="$root/.venv/bin:/opt/homebrew/bin:$PATH"
mkdir -p runs

# `record-demo.sh api` records only the API tape, which needs Docker running.
if [ "${1:-}" = "api" ]; then
    tapes=(demo/api.tape)
else
    tapes=(demo/demo.tape demo/compare.tape)
fi

for tape in "${tapes[@]}"; do
    vhs "$tape"
done

for tape in "${tapes[@]}"; do
    gif="${tape%.tape}.gif"
    echo "Wrote $gif ($(du -h "$gif" | cut -f1))"
done

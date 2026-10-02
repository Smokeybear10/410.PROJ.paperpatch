#!/usr/bin/env bash
set -euo pipefail

cd -- "$(dirname -- "${BASH_SOURCE[0]}")"

if [[ "${1:-}" == "--help" ]]; then
  echo 'Usage: ./run-paperpatch.sh [--skip-ocr]'
  echo 'Start the downloaded PaperPatch build on http://127.0.0.1:7171.'
  echo '--skip-ocr skips handwriting model setup for prepared examples and manual review.'
  exit 0
fi
if [[ $# -gt 1 || ( $# -eq 1 && "$1" != "--skip-ocr" ) ]]; then
  echo 'Usage: ./run-paperpatch.sh [--skip-ocr]' >&2
  exit 1
fi
if ! command -v uv >/dev/null 2>&1; then
  echo 'Install uv from https://docs.astral.sh/uv/getting-started/installation/ and run this command again.' >&2
  exit 1
fi
if [[ ! -f dist/index.html ]]; then
  echo 'This is the source checkout. Run npm ci && npm run build first, or download the packaged test build.' >&2
  exit 1
fi

echo 'Preparing the locked Python environment. The first run downloads dependencies.'
uv sync --frozen --python 3.12
if [[ "${1:-}" != "--skip-ocr" ]]; then
  echo 'Checking the handwriting models. The first run downloads their pinned versions.'
  uv run --frozen python -m backend.document_ocr --setup
fi
echo 'Open http://127.0.0.1:7171 in your browser. Press Ctrl+C to stop.'
exec uv run --frozen uvicorn backend.main:app --host 127.0.0.1 --port 7171

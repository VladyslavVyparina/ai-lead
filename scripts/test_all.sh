#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "== Backend / API / UI tests =="
"$ROOT/.venv/bin/python" -m pytest "$ROOT/tests" -q

echo "== Preview-site clean install =="
cd "$ROOT/site_template"
npm ci

echo "== Preview-site production build =="
npm run build

echo "== Production dependency audit =="
npm audit --omit=dev

echo "All checks passed."

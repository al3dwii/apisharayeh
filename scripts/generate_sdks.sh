#!/usr/bin/env bash
set -euo pipefail

OPENAPI_URL=${OPENAPI_URL:-http://localhost:8080/openapi.json}
OUT_TS=${OUT_TS:-sdks/typescript}
OUT_PY=${OUT_PY:-sdks/python}

mkdir -p "$OUT_TS" "$OUT_PY"

# TypeScript (requires Node)
npx --yes openapi-typescript "$OPENAPI_URL" -o "$OUT_TS/index.ts"

# Python
python -m pip install --upgrade openapi-python-client >/dev/null
rm -rf "$OUT_PY"
openapi-python-client generate --url "$OPENAPI_URL" --meta none --config scripts/openapi-python-client-config.json
# Move generated package under sdks/python
GEN_DIR=$(ls -d openapi_python_client* | head -n1 || true)
if [ -n "$GEN_DIR" ]; then
  mkdir -p "$OUT_PY"
  mv "$GEN_DIR"/* "$OUT_PY"/
  rm -rf "$GEN_DIR"
fi

echo "SDKs written to $OUT_TS and $OUT_PY"

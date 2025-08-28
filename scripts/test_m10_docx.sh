#!/usr/bin/env bash
set -euo pipefail

API="${API_URL:-http://localhost:8081}"
LANG="${LANG:-en_US.UTF-8}"
COUNT="${COUNT:-8}"

PROJECT_ID="prj_$(date +%s)"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "[env] API=$API  LANG=$LANG  COUNT=$COUNT"

TMPDIR="$(mktemp -d)"
DOCX="$TMPDIR/in.docx"

# ensure python-docx
if ! python - <<'PY' >/dev/null 2>&1
import sys
try:
    import docx  # noqa
except Exception:
    sys.exit(1)
PY
then
  echo "[deps] installing python-docx..."
  python -m pip install --quiet python-docx
fi

# write a small docx
DOCX_OUT="$DOCX" python - <<'PY'
from docx import Document
from pathlib import Path
import os
out = Path(os.environ["DOCX_OUT"])
doc = Document()
doc.add_heading('M10 Demo: Upload DOCX', 0)
p = doc.add_paragraph('This is a quick demo file for M10. It has ')
p.add_run('bold').bold = True
p.add_run(' and ')
p.add_run('italic').italic = True
doc.add_heading('Topics', level=1)
for b in ['Background','Approach','Results','Next steps']:
    doc.add_paragraph(b, style='List Bullet')
doc.save(out)
print(out)
PY

ls -lh "$DOCX" | awk '{print "[docx]", $9, $5}'

# upload (optional), still use file:// for DEV-safe fetch
UP_JSON="$(curl -fsS -F "file=@$DOCX" "$API/v1/uploads/doc" || true)"
UPLOAD_URL="$(printf '%s' "$UP_JSON" | jq -r '.url // .doc_url // empty' 2>/dev/null || true)"
UPLOAD_PATH="$(printf '%s' "$UP_JSON" | jq -r '.path // .fs_path // empty' 2>/dev/null || true)"
# pretty-ish log without failing if jq dislikes something
echo -n "[upload] "
printf '%s' "$UP_JSON" | jq -c . 2>/dev/null || printf '%s\n' "$UP_JSON"

# resolve an absolute path we can serve with file://
if [[ -n "${UPLOAD_PATH:-}" ]]; then
  if [[ "$UPLOAD_PATH" = /* ]]; then
    ABS_PATH="$UPLOAD_PATH"
  else
    ABS_PATH="$REPO_ROOT/$UPLOAD_PATH"
  fi
else
  ABS_PATH="$(cd "$(dirname "$DOCX")" && pwd)/$(basename "$DOCX")"
fi
DOCX_URL="file://$ABS_PATH"
echo "[docx_url] $DOCX_URL"

# submit slides.generate (docx -> pptx + pdf + html)
BODY="$(jq -nc --arg pid "$PROJECT_ID" --arg url "$DOCX_URL" --arg lang "$LANG" --argjson count "$COUNT" '
{
  service_id: "slides.generate",
  inputs: {
    project_id: $pid,
    source: "docx",
    docx_url: $url,
    language: $lang,
    slides_count: $count,
    export: ["pdf","html"]
  }
}')"

SUBMIT="$(curl -fsS -H 'Content-Type: application/json' -d "$BODY" "$API/v1/jobs")"
JOB_ID="$(printf '%s' "$SUBMIT" | jq -r '.id // .job_id')"
echo "[job] $JOB_ID  project=$PROJECT_ID"

# poll
for _ in $(seq 1 180); do
  ST="$(curl -fsS "$API/v1/jobs/$JOB_ID" | jq -r '.status // .state')"
  echo "  status=$ST"
  [[ "$ST" =~ ^(succeeded|completed)$ ]] && break
  if [[ "$ST" =~ ^(failed|error)$ ]]; then
    curl -fsS "$API/v1/jobs/$JOB_ID" | jq .
    echo "[fail] job failed"
    exit 1
  fi
  sleep 1
done

# extract artifact URLs
JOB_JSON="$(curl -fsS "$API/v1/jobs/$JOB_ID")"
PPTX_URL="$(printf '%s' "$JOB_JSON" | jq -r '.output.pptx_url // .outputs.pptx_url // .result.pptx_url // "/artifacts/'"$PROJECT_ID"'/export/presentation.pptx"')"
PDF_URL="$(printf '%s' "$JOB_JSON" | jq -r '.output.pdf_url  // .outputs.pdf_url  // .result.pdf_url  // "/artifacts/'"$PROJECT_ID"'/presentation.pdf"')"
HTML_ZIP_URL="$(printf '%s' "$JOB_JSON" | jq -r '.output.html_zip_url // .outputs.html_zip_url // .result.html_zip_url // "/artifacts/'"$PROJECT_ID"'/html.zip"')"
INDEX_URL="/artifacts/$PROJECT_ID/index.html"
SLIDES_INDEX_URL="/artifacts/$PROJECT_ID/slides/index.html"

echo "[verify] HEAD artifacts:"
fail=0
for U in "$PPTX_URL" "$PDF_URL" "$HTML_ZIP_URL" "$INDEX_URL" "$SLIDES_INDEX_URL"; do
  CODE="$(curl -s -o /dev/null -w '%{http_code}' -I "$API$U" || echo 000)"
  printf "  %s -> %s\n" "$API$U" "$CODE"
  [[ "$CODE" != "200" ]] && fail=1
done

if [[ $fail -eq 0 ]]; then
  echo "[index] $API$INDEX_URL"
  echo "[pass] M10 DOCX OK"
else
  echo "[warn] some artifacts not 200 yet — open the slides page to inspect:"
  echo "       $API$SLIDES_INDEX_URL"
  exit 2
fi

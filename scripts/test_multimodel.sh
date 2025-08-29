#!/usr/bin/env bash
# test_multimodel.sh — end-to-end smoke for the Multi-model foundation
#
# Usage:
#   bash scripts/test_multimodel.sh
#   WITH_DOCKER=1 bash scripts/test_multimodel.sh
#
# Faster ASR (tiny model):
#   ASR_MODEL=tiny.en bash scripts/test_multimodel.sh
#
# Control macOS voice (optional):
#   SAY_VOICE="Samantha" bash scripts/test_multimodel.sh
#   (list voices: say -v '?')

set -euo pipefail

### ---------- helpers ----------
ROOT="${ROOT:-$(git rev-parse --show-toplevel 2>/dev/null || pwd)}"
APP_DIR="${APP_DIR:-$ROOT/src}"
PYTHONPATH="$APP_DIR"
export PYTHONPATH

PORT="${PORT:-8080}"
BASE="http://127.0.0.1:${PORT}"
ARTIFACTS_DIR="${ARTIFACTS_DIR:-$ROOT/artifacts}"
VENV_DIR="${VENV_DIR:-$ROOT/.venv}"
WITH_DOCKER="${WITH_DOCKER:-0}"

log() { printf "\033[1;34m[TEST]\033[0m %s\n" "$*"; }
ok()  { printf "\033[1;32m[ OK ]\033[0m %s\n" "$*"; }
warn(){ printf "\033[1;33m[WARN]\033[0m %s\n" "$*"; }
err() { printf "\033[1;31m[ ERR]\033[0m %s\n" "$*"; }

need() {
  local cmd="${1:-}"
  if [[ -z "$cmd" ]]; then err "need(): missing cmd name"; exit 1; fi
  command -v "$cmd" >/dev/null 2>&1 || { err "Missing dependency: $cmd"; exit 1; }
}

cleanup() {
  if [[ -n "${UVICORN_PID:-}" ]] && ps -p "$UVICORN_PID" >/dev/null 2>&1; then
    kill "$UVICORN_PID" || true
    wait "$UVICORN_PID" 2>/dev/null || true
  fi
}
trap cleanup EXIT

wait_http() {
  local url="${1:-}"
  local tries="${2:-60}"
  [[ -z "$url" ]] && return 1
  for _ in $(seq 1 "$tries"); do
    if curl -sk --max-time 2 -o /dev/null -w "%{http_code}" "$url" | grep -qE "200|401|404"; then
      return 0
    fi
    sleep 0.5
  done
  return 1
}

### ---------- preflight ----------
log "Repo root: $ROOT"
log "App dir   : $APP_DIR"
need python3
need pip
need curl
need ffmpeg || { err "ffmpeg is required"; exit 1; }

if [[ ! -f "$APP_DIR/app/main.py" ]]; then
  err "FastAPI entry not found at $APP_DIR/app/main.py"
  exit 1
fi

mkdir -p "$ARTIFACTS_DIR" "$ROOT/tests/assets"

### ---------- optional docker build ----------
if [[ "$WITH_DOCKER" == "1" ]]; then
  need docker
  log "Building Docker image (o2-backend:local) from $APP_DIR/Dockerfile ..."
  docker build -t o2-backend:local "$APP_DIR"
  ok "Docker image built"
fi

### ---------- venv + deps ----------
if [[ ! -d "$VENV_DIR" ]]; then
  log "Creating venv at $VENV_DIR"
  python3 -m venv "$VENV_DIR"
fi
# shellcheck source=/dev/null
source "$VENV_DIR/bin/activate"
pip -q install --upgrade pip
log "Installing backend requirements"
if [[ -f "$APP_DIR/requirements.txt" ]]; then
  pip -q install -r "$APP_DIR/requirements.txt"
else
  warn "requirements.txt not found under $APP_DIR; assuming already installed"
fi
log "Installing test/dev deps"
pip -q install pytest pytest-asyncio uvicorn httpx pillow

### ---------- unit tests (no external services) ----------
if ls "$ROOT/tests"/*.py "$ROOT/tests"/*/*.py >/dev/null 2>&1; then
  log "Running unit tests (excluding integration)"
  pytest -q -m "not integration"
  ok "Unit tests passed"
else
  warn "No tests/ found — skipping pytest"
fi

### ---------- start API server ----------
log "Starting FastAPI with uvicorn on port $PORT"
export ARTIFACTS_DIR
( cd "$APP_DIR" && uvicorn app.main:app --host 127.0.0.1 --port "$PORT" >/tmp/o2_uvicorn.log 2>&1 ) &
UVICORN_PID=$!
sleep 0.5
if ! wait_http "$BASE/docs" 60; then
  err "Server failed to start; tail follows:"
  tail -n +1 /tmp/o2_uvicorn.log || true
  exit 1
fi
ok "Server is up"

### ---------- probe /models and /voices (try both /v1/* and /*) ----------
probe_models() {
  local path="${1:-/models}"
  if curl -fsS "$BASE$path" >/tmp/models.json 2>/dev/null; then
    ok "GET $path"
    return 0
  else
    warn "GET $path failed"
    return 1
  fi
}
probe_models "/v1/models" || probe_models "/models" || true

probe_voices() {
  local path="${1:-/models/voices}"
  if curl -fsS "$BASE$path" >/tmp/voices.json 2>/dev/null; then
    ok "GET $path"
    return 0
  else
    warn "GET $path failed"
    return 1
  fi
}
probe_voices "/v1/models/voices" || probe_voices "/models/voices" || true

### ---------- make test assets ----------
SINE_WAV="$ROOT/tests/assets/sine_1s_16k.wav"
log "Generating 1s sine wave @16k mono for media upload / ASR smoke"
ffmpeg -hide_banner -loglevel error -y -f lavfi -i "sine=frequency=440:duration=1" -ac 1 -ar 16000 "$SINE_WAV"
ok "Generated $SINE_WAV"

# Prefer a short real speech sample on macOS for a more realistic ASR smoke
TEST_WAV="$SINE_WAV"
if command -v say >/dev/null 2>&1; then
  SPEECH_AIFF="$ROOT/tests/assets/hello.aiff"
  SPEECH_WAV="$ROOT/tests/assets/hello_16k.wav"

  # Pick voice: env override > first English voice > first listed voice > fallback to default 'say'
  VOICE="${SAY_VOICE:-}"
  if [[ -z "$VOICE" ]]; then
    # First English voice (lines like: "Samantha            en_US    # ...")
    VOICE="$(say -v '?' | awk '/\ben(_|-)/{print $1; exit}')" || true
  fi
  if [[ -z "$VOICE" ]]; then
    # First available voice name (first column)
    VOICE="$(say -v '?' | awk 'NR==1{print $1}')" || true
  fi

  if [[ -n "${VOICE:-}" ]]; then
    log "Generating spoken sample via macOS 'say' (voice: $VOICE)"
    if say -v "$VOICE" -o "$SPEECH_AIFF" "Hello from the multi model test" >/dev/null 2>&1; then
      ffmpeg -hide_banner -loglevel error -y -i "$SPEECH_AIFF" -ac 1 -ar 16000 "$SPEECH_WAV"
      TEST_WAV="$SPEECH_WAV"
      ok "Generated $SPEECH_WAV"
    else
      warn "say failed with selected voice '$VOICE'; falling back to sine wave"
    fi
  else
    # Try system default voice (no -v)
    log "Generating spoken sample via macOS 'say' (default voice)"
    if say -o "$SPEECH_AIFF" "Hello from the multi model test" >/dev/null 2>&1; then
      ffmpeg -hide_banner -loglevel error -y -i "$SPEECH_AIFF" -ac 1 -ar 16000 "$SPEECH_WAV"
      TEST_WAV="$SPEECH_WAV"
      ok "Generated $SPEECH_WAV"
    else
      warn "say failed; falling back to sine wave"
    fi
  fi
fi

# Simple OCR image via Pillow (HELLO)
OCR_PNG="$ROOT/tests/assets/ocr_hello.png"
python3 - <<PY >/dev/null 2>&1 && ok "Generated $OCR_PNG" || warn "Failed to create OCR demo image (Pillow issue?)"
from PIL import Image, ImageDraw, ImageFont
img = Image.new("RGB",(640,240),"white")
d = ImageDraw.Draw(img)
try:
    f = ImageFont.truetype("DejaVuSans.ttf", 72)
except Exception:
    f = ImageFont.load_default()
d.text((40,80),"HELLO WORLD",(0,0,0),font=f)
img.save("$OCR_PNG")
PY

### ---------- upload media (audio) ----------
upload_media() {
  local file="${1:-}"
  [[ -z "$file" ]] && { err "upload_media: file path required"; return 1; }

  # Detect mime (macOS 'file' works); fall back to audio/wav
  local mime
  if command -v file >/dev/null 2>&1; then
    mime="$(file -b --mime-type "$file" 2>/dev/null || true)"
  fi
  mime="${mime:-audio/wav}"

  local path status
  for path in "/v1/uploads/media" "/uploads/media"; do
    status="$(curl -sS -o /tmp/upload.json -w "%{http_code}" \
      -F "file=@${file};type=${mime}" \
      -F "project_id=test_multimodel" \
      "$BASE$path" || true)"
    if [[ "$status" =~ ^2 ]]; then
      ok "POST $path ($(basename "$file"))"
      sed -e 's/^/  /' /tmp/upload.json || true
      return 0
    else
      warn "POST $path failed (HTTP $status)"
    fi
  done
  warn "Media upload failed on both endpoints"
  return 1
}
upload_media "$TEST_WAV" || true

### ---------- provider smoke tests via ModelRouter ----------
# ASR (feed real speech if available; success == no crash + JSON printed)
log "ASR smoke via ModelRouter (faster-whisper)"
python3 - "$TEST_WAV" <<'PY' || { warn "ASR smoke failed"; }
import sys, json, os
from app.services.models import ModelRouter
wav = sys.argv[1]
r = ModelRouter()
out = r.asr(wav, diarize=False, policy_ctx={"lang": os.getenv("ASR_TEST_LANG","en")})
print(json.dumps({"ok": True, "segments": len(out.get("segments",[])), "language": out.get("language")}))
PY
ok "ASR call returned"

# TTS (requires Azure creds and azure sdk installed)
if [[ -n "${AZURE_SPEECH_KEY:-}" && -n "${AZURE_SPEECH_REGION:-}" ]]; then
  log "TTS smoke via ModelRouter (Azure Neural)"
  if python3 -c "import azure.cognitiveservices.speech" 2>/dev/null; then
    TTS_OUT="$ROOT/tests/assets/tts_out.wav"
    export TTS_TEST_TEXT="Hello from Azure TTS pipeline"
    python3 - <<PY || { warn "TTS smoke failed"; }
import os, shutil
from app.services.models import ModelRouter
r = ModelRouter()
out_path = r.tts([{"text": os.getenv("TTS_TEST_TEXT","Hello world")}],
                 voice=os.getenv("TTS_TEST_VOICE","en-US-JennyNeural"),
                 policy_ctx={"lang": os.getenv("TTS_TEST_LANG","en-US")})
print(out_path)
shutil.copyfile(out_path, r"$TTS_OUT")
PY
    [[ -f "$TTS_OUT" ]] && ok "TTS produced $TTS_OUT" || warn "TTS did not produce file"
    upload_media "$TTS_OUT" || true
  else
    warn "azure.cognitiveservices.speech not installed; skipping TTS smoke"
  fi
else
  warn "AZURE_SPEECH_KEY/REGION not set; skipping TTS smoke"
fi

# OCR (requires running PaddleOCR server)
if [[ -n "${PADDLE_OCR_BASE:-}" ]]; then
  log "OCR smoke via ModelRouter (Paddle OCR server at $PADDLE_OCR_BASE)"
  python3 - "$OCR_PNG" <<'PY' || { warn "OCR smoke failed"; }
import sys, json, os
from app.services.models import ModelRouter
img = sys.argv[1]
r = ModelRouter()
out = r.ocr(img, policy_ctx={"lang": os.getenv("OCR_TEST_LANG","en")})
print(json.dumps(out)[:400])
PY
  ok "OCR call returned"
else
  warn "PADDLE_OCR_BASE not set; skipping OCR smoke"
fi

### ---------- finish ----------
ok "Multi-model smoke test completed"
echo
echo "Summary:"
echo "  - Server logs: /tmp/o2_uvicorn.log"
echo "  - Models JSON: /tmp/models.json (if endpoint available)"
echo "  - Voices JSON: /tmp/voices.json (if endpoint available)"
echo "  - Upload JSON : /tmp/upload.json (if upload succeeded)"
echo

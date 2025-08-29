# app/kernel/ops/media.py
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from app.core.config import settings
from app.services.ffmpeg import (
    probe,
    ensure_wav_mono_16k,
    extract_audio as ff_extract_audio,
    mux_replace_audio,
    write_srt,
)
from app.services.models import ModelRouter

ARTIFACTS = Path(settings.ARTIFACTS_DIR).resolve()

def _project_dir(project_id: str) -> Path:
    d = ARTIFACTS / project_id
    d.mkdir(parents=True, exist_ok=True)
    return d

def _save_json(project_id: str, obj: Any, name: str) -> str:
    out = _project_dir(project_id) / name
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(out)

# ---- Ops ----

def op_extract_audio(project_id: str, src_video: str, sample_rate: int = 16000) -> Dict[str, Any]:
    """Extract mono WAV from a video/audio file."""
    out_dir = _project_dir(project_id) / "media"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_wav = out_dir / (Path(src_video).stem + f".{sample_rate//1000}k.wav")
    ff_extract_audio(src_video, str(out_wav), sample_rate=sample_rate)
    return {"audio": f"/artifacts/{project_id}/media/{out_wav.name}"}

def op_asr(project_id: str, src_audio: str, lang: Optional[str] = None, diarize: bool = False) -> Dict[str, Any]:
    """Run ASR and emit segments JSON."""
    router = ModelRouter()
    # normalize to local path
    if src_audio.startswith("/artifacts/"):
        src_audio = str(ARTIFACTS / src_audio.split("/artifacts/", 1)[1])
    wav = ensure_wav_mono_16k(src_audio)
    t0 = time.time()
    out = router.asr(wav, diarize=diarize, policy_ctx={"lang": lang} if lang else None)
    out["elapsed_seconds"] = round(time.time() - t0, 3)
    # persist transcript
    path = _save_json(project_id, out, f"media/{Path(src_audio).stem}.asr.json")
    out["path"] = f"/artifacts/{project_id}/media/{Path(path).name}"
    return out

def op_translate_segments(project_id: str, segments: List[Dict[str, Any]], target_lang: str, source_lang: Optional[str] = None) -> Dict[str, Any]:
    """Translate ASR segments' text into target_lang."""
    router = ModelRouter()
    tr_segs: List[Dict[str, Any]] = []
    for s in segments:
        txt = s.get("text", "")
        if not txt.strip():
            tr_segs.append({**s, "text": ""})
            continue
        t = router.translate(txt, source=source_lang, target=target_lang)
        tr_segs.append({**s, "text": t})
    out = {"segments": tr_segs, "target_lang": target_lang, "source_lang": source_lang}
    path = _save_json(project_id, out, f"media/segments.{target_lang}.json")
    out["path"] = f"/artifacts/{project_id}/media/{Path(path).name}"
    return out

def op_tts(project_id: str, segments: List[Dict[str, Any]], voice: str, lang: Optional[str] = None, sample_rate: int = 24000) -> Dict[str, Any]:
    """Synthesize translated segments to a single WAV using router.tts."""
    router = ModelRouter()
    t0 = time.time()
    wav_path = router.tts(segments, voice=voice, policy_ctx={"lang": lang} if lang else None)
    out_dir = _project_dir(project_id) / "media"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_wav = out_dir / (f"tts_{Path(wav_path).stem}.wav")
    Path(wav_path).replace(out_wav)
    return {
        "audio": f"/artifacts/{project_id}/media/{out_wav.name}",
        "elapsed_seconds": round(time.time() - t0, 3),
        "voice": voice,
        "lang": lang,
    }

def op_mux(project_id: str, src_video: str, new_audio: str) -> Dict[str, Any]:
    """Replace video audio track with synthesized audio."""
    if new_audio.startswith("/artifacts/"):
        new_audio = str(ARTIFACTS / new_audio.split("/artifacts/", 1)[1])
    out_dir = _project_dir(project_id) / "media"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_mp4 = out_dir / (Path(src_video).stem + ".dub.mp4")
    mux_replace_audio(src_video, new_audio, str(out_mp4))
    return {"video": f"/artifacts/{project_id}/media/{out_mp4.name}"}

def op_subtitles(project_id: str, segments: List[Dict[str, Any]], kind: str = "srt") -> Dict[str, Any]:
    """Write SRT (or VTT in future) from segments."""
    out_dir = _project_dir(project_id) / "media"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_srt = out_dir / "subtitles.srt"
    write_srt(segments, str(out_srt))
    return {"srt": f"/artifacts/{project_id}/media/{out_srt.name}"}

# ---- Registration hook ----

def register(toolrouter) -> None:
    """Called from toolrouter bootstrap to expose ops to the DSL."""
    toolrouter.register("media.extract_audio", op_extract_audio)
    toolrouter.register("media.asr",           op_asr)
    toolrouter.register("media.translate",     op_translate_segments)
    toolrouter.register("media.tts",           op_tts)
    toolrouter.register("media.mux",           op_mux)
    toolrouter.register("media.subtitles",     op_subtitles)

# app/services/providers/asr/whisperx_asr.py
import os
from typing import Any, Dict, Optional, List

from app.services.providers.base import ASRProvider
from app.services.ffmpeg import ensure_wav_mono_16k

def _env_bool(name: str, default: bool) -> bool:
    v = os.getenv(name)
    if v is None:
        return default
    return v.lower() in ("1", "true", "yes", "on")

class FasterWhisperASR(ASRProvider):
    """
    Minimal ASR provider using faster-whisper.
    - Honors env overrides for quick smoke tests:
      * ASR_MODEL          -> e.g. 'tiny.en', 'base.en', 'large-v3' (default)
      * ASR_DEVICE         -> 'auto' (default), 'cpu', or 'cuda'
      * ASR_COMPUTE_TYPE   -> e.g. 'auto', 'int8', 'int8_float16', 'float16', 'float32'
      * ASR_VAD_FILTER     -> 'true' (default) | 'false'
    """
    name = "whisperx:large-v3"  # matches config/models.yaml default

    def __init__(self, model: Optional[str] = None):
        from faster_whisper import WhisperModel

        # Allow small-model override for fast local testing
        model_name = os.getenv("ASR_MODEL") or model or "large-v3"
        device = os.getenv("ASR_DEVICE", "auto")  # "auto" | "cpu" | "cuda"
        compute_type = os.getenv("ASR_COMPUTE_TYPE")  # optional
        kwargs = {"device": device}
        if compute_type:
            kwargs["compute_type"] = compute_type

        self.model_name = model_name
        self.model = WhisperModel(self.model_name, **kwargs)
        self.vad_filter = _env_bool("ASR_VAD_FILTER", True)

    def transcribe(
        self,
        wav_path: str,
        diarize: bool = True,
        lang: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Transcribe an audio file. 'diarize' is accepted for interface compatibility,
        but this MVP provider does not perform diarization (speaker stays None).
        """
        wav = ensure_wav_mono_16k(wav_path)

        segments_iter, info = self.model.transcribe(
            wav,
            language=lang,            # None => auto-detect
            vad_filter=self.vad_filter
        )

        segments: List[Dict[str, Any]] = []
        for s in segments_iter:
            segments.append({
                "start": float(getattr(s, "start", 0.0)),
                "end": float(getattr(s, "end", 0.0)),
                "text": (getattr(s, "text", "") or "").strip(),
                "speaker": None  # diarization not supported in MVP
            })

        return {
            "provider": self.name,
            "model": self.model_name,
            "language": getattr(info, "language", lang),
            "duration": float(getattr(info, "duration", 0.0)),
            "segments": segments
        }

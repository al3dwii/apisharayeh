# app/services/providers/tts/system_tts.py
from __future__ import annotations
import os, subprocess, tempfile, shutil
from typing import Any, Dict, List, Optional

from app.services.providers.base import TTSProvider


class SystemTTS(TTSProvider):
    """
    Minimal, zero-download TTS using:
      - macOS: `say` -> AIFF -> ffmpeg -> WAV
      - Linux: `espeak` -> WAV

    Configure voice name via `voice` (string) or env `SYSTEM_TTS_VOICE`.
    """
    name = "system:tts"

    def synthesize(
        self,
        segments: List[Dict[str, Any]],
        voice: Dict[str, Any] | str | None = None,
        lang: Optional[str] = None,
        sample_rate: int = 24000,
    ) -> str:
        text = " ".join((s.get("text", "") or "").strip() for s in segments if (s.get("text") or "").strip()) or "(silence)"
        voice_name = None
        if isinstance(voice, str):
            voice_name = voice
        elif isinstance(voice, dict):
            voice_name = voice.get("name") or voice.get("id")
        if not voice_name:
            voice_name = os.getenv("SYSTEM_TTS_VOICE") or ""

        # Paths
        out_wav = tempfile.NamedTemporaryFile(delete=False, suffix=".wav").name

        # macOS path (say -> aiff -> wav)
        if shutil.which("say"):
            aiff = tempfile.NamedTemporaryFile(delete=False, suffix=".aiff").name
            cmd = ["say", "-o", aiff, text]
            if voice_name:
                cmd = ["say", "-v", voice_name, "-o", aiff, text]
            subprocess.run(cmd, check=True)
            subprocess.run(
                ["ffmpeg", "-y", "-i", aiff, "-ac", "1", "-ar", str(sample_rate), out_wav],
                check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
            return out_wav

        # Linux path (espeak -> wav)
        if shutil.which("espeak"):
            cmd = ["espeak", "-w", out_wav, text]
            if voice_name:
                cmd = ["espeak", "-v", voice_name, "-w", out_wav, text]
            subprocess.run(cmd, check=True)
            # resample to requested rate via ffmpeg if needed
            subprocess.run(
                ["ffmpeg", "-y", "-i", out_wav, "-ac", "1", "-ar", str(sample_rate), out_wav],
                check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
            return out_wav

        raise RuntimeError("No system TTS available (need 'say' on macOS or 'espeak' on Linux).")

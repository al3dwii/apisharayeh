# app/services/models.py
from __future__ import annotations

import importlib
import os
from typing import Any, Dict, Optional, List
import yaml

def _first_existing(paths: List[str]) -> Optional[str]:
    for p in paths:
        if p and os.path.isfile(p):
            return p
    return None

class ModelRouter:
    """
    Config-driven router for LLM / MT / ASR / TTS / OCR.
    - Honors MODELS_CONFIG env var; else tries ./config/models.yaml then ./src/config/models.yaml.
    - OCR: if config selects paddle server but PADDLE_OCR_BASE is missing, auto-falls back to tesseract:cpu.
    """

    def __init__(self, cfg_path: str | None = None):
        env_path = cfg_path or os.getenv("MODELS_CONFIG")
        if env_path is None:
            env_path = _first_existing([
                os.path.join(os.getcwd(), "config", "models.yaml"),
                os.path.join(os.getcwd(), "src", "config", "models.yaml"),
            ])
        self.cfg_path = env_path
        try:
            with open(self.cfg_path, "r", encoding="utf-8") as f:
                self.cfg = yaml.safe_load(f) or {}
        except Exception:
            self.cfg = {}

    # ---------- selection ----------
    def _use(self, family: str, meta: Dict[str, Any]) -> Optional[str]:
        fam = self.cfg.get(family, {}) if isinstance(self.cfg, dict) else {}
        if not isinstance(fam, dict):
            return None
        rules = fam.get("rules") or []
        if not isinstance(rules, list):
            rules = []
        for r in rules:
            when = (r or {}).get("when", {}) or {}
            if all(meta.get(k) == v for k, v in when.items()):
                return (r or {}).get("use")
        return fam.get("default")

    def effective_use(self, family: str, meta: Dict[str, Any]) -> Optional[str]:
        """Final provider key after env-aware fallbacks."""
        use = self._use(family, meta) or None
        if family == "ocr":
            # If paddle server is selected but base URL is not set, fall back to tesseract.
            if use == "paddleocr:server" and not os.getenv("PADDLE_OCR_BASE"):
                return "tesseract:cpu"
        return use

    def _load_provider(self, key: str):
        mapping = {
            # ASR
            "whisperx:large-v3": "app.services.providers.asr.whisperx_asr:FasterWhisperASR",
            "whisper:tiny.en":   "app.services.providers.asr.whisperx_asr:FasterWhisperASR",
            # TTS
            "system:tts":        "app.services.providers.tts.system_tts:SystemTTS",
            "azure:neural":      "app.services.providers.tts.azure_tts:AzureNeuralTTS",
            # OCR
            "tesseract:cpu":     "app.services.providers.ocr.tesseract_ocr:TesseractOCR",
            "paddleocr:server":  "app.services.providers.ocr.paddle_client:PaddleOCRServer",
            # MT
            "argos:en-ar":       "app.services.providers.mt.argos:ArgosMT",
            "argos:ar-en":       "app.services.providers.mt.argos:ArgosMT",
        }
        if key not in mapping:
            raise RuntimeError(f"No provider mapping for '{key}'")
        mod, cls = mapping[key].split(":")
        return getattr(importlib.import_module(mod), cls)

    # ---------- LLM (fallback) ----------
    def _llm_translate(self, text: str, source: Optional[str], target: str) -> str:
        from app.services import llm_router
        return llm_router.translate(text, source=source, target=target)

    # ---------- Public APIs ----------
    def chat(self, messages: list[dict], **kwargs) -> Any:
        from app.services import llm_router
        return llm_router.chat(messages, **kwargs)

    def translate(self, text: str, source: Optional[str] = None, target: str = "en") -> str:
        use = self.effective_use("mt", {"source_lang": source, "target_lang": target})
        if use:
            Prov = self._load_provider(use)
            return Prov().translate(text, source=source, target=target)
        return self._llm_translate(text, source, target)

    def asr(self, wav_path: str, diarize: bool = True, policy_ctx: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        use = self.effective_use("asr", policy_ctx or {}) or "whisper:tiny.en"
        Prov = self._load_provider(use)
        return Prov().transcribe(wav_path, diarize=diarize, lang=(policy_ctx or {}).get("lang"))

    def tts(self, segments: list[dict], voice: dict | str | None, policy_ctx: Optional[Dict[str, Any]] = None) -> str:
        use = self.effective_use("tts", policy_ctx or {}) or "system:tts"
        Prov = self._load_provider(use)
        return Prov().synthesize(segments, voice=voice, lang=(policy_ctx or {}).get("lang"))

    def ocr(self, file_path: str, policy_ctx: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        use = self.effective_use("ocr", policy_ctx or {}) or "tesseract:cpu"
        Prov = self._load_provider(use)
        return Prov().extract(file_path, lang=(policy_ctx or {}).get("lang"))

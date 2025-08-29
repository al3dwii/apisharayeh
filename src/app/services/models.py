# app/services/models.py
import importlib, os, yaml
from typing import Any, Dict, Optional


class ModelRouter:
    def __init__(self, cfg_path=os.getenv("MODELS_CONFIG", "config/models.yaml")):
        # Optional: load model config (not required for the fallback flow)
        try:
            self.cfg = yaml.safe_load(open(cfg_path, "r", encoding="utf-8"))
        except Exception:
            self.cfg = {}

    def chat(self, messages: list[dict], policy_ctx: Dict[str, Any] | None = None) -> dict:
        """
        Minimal glue that routes chat to our LLM helper.
        If the prompt looks like a slides-outline request, we *explicitly* ask for JSON
        with the schema expected by outline_from_prompt().
        """
        from app.services.llm_router import run_chat

        # Concatenate non-system contents (the system role is stripped upstream)
        content = "\n".join([m.get("content", "") for m in messages if m.get("content")])

        # Heuristic: outline requests include "Slides:" or the word "outline"
        wants_outline = ("Slides:" in content) or ("outline" in content.lower())
        if wants_outline:
            content = (
                content.strip()
                + "\n\nReturn JSON array of slides with this exact schema:\n"
                + '[{"title":"","bullets":["",""],"notes":"","layout_hint":"auto"}]'
            )

        txt = run_chat(content)
        return {"text": txt}

    def translate(self, text: str, source: str | None, target: str | None, policy_ctx=None) -> str:
        from app.services.llm_router import run_translate
        return run_translate(text, source, target)

    def asr(self, wav_path: str, diarize=True, policy_ctx=None) -> Dict[str, Any]:
        raise NotImplementedError

    def tts(self, segments: list[dict], voice: dict | str, policy_ctx=None) -> str:
        raise NotImplementedError

    def ocr(self, file_path: str, policy_ctx=None) -> dict:
        raise NotImplementedError


    def _use(self, family: str, meta: Dict[str, Any]) -> str:
        # Apply simple rules like: target_lang == ar -> hf:nllb-200-3.3B
        fam = self.cfg.get(family, {}) if isinstance(self.cfg, dict) else {}
        rules = (fam or {}).get("rules", []) or []
        for r in rules:
            when = r.get("when", {})
            if all(meta.get(k) == v for k,v in when.items()):
                return r["use"]
        return (fam or {}).get("default")

    def _load_provider(self, key: str):
        # map config key → provider class path
        # asr: whisperx:large-v3 → app.services.providers.asr.whisperx_asr:FasterWhisperASR
        m = {
            "whisperx:large-v3": "app.services.providers.asr.whisperx_asr:FasterWhisperASR",
            "azure:neural":      "app.services.providers.tts.azure_tts:AzureNeuralTTS",
            "paddleocr:server":  "app.services.providers.ocr.paddle_client:PaddleOCRServer",
        }
        if key not in m: raise RuntimeError(f"No provider mapping for '{key}'")
        mod, cls = m[key].split(":")
        return getattr(importlib.import_module(mod), cls)

    # ---- ASR ----
    def asr(self, wav_path: str, diarize: bool = True, policy_ctx: Optional[Dict[str,Any]] = None) -> Dict[str,Any]:
        use = self._use("asr", policy_ctx or {})
        Prov = self._load_provider(use)
        try:
            return Prov().transcribe(wav_path, diarize=diarize, lang=(policy_ctx or {}).get("lang"))
        except Exception as e:
            # simple fallback (none configured today)
            raise

    # ---- TTS ----
    def tts(self, segments: list[dict], voice: dict|str, policy_ctx: Optional[Dict[str,Any]]=None) -> str:
        use = self._use("tts", policy_ctx or {})
        Prov = self._load_provider(use)
        return Prov().synthesize(segments, voice=voice, lang=(policy_ctx or {}).get("lang"))

    # ---- OCR ----
    def ocr(self, file_path: str, policy_ctx: Optional[Dict[str,Any]]=None) -> dict:
        use = self._use("ocr", policy_ctx or {})
        Prov = self._load_provider(use)
        return Prov().extract(file_path, lang=(policy_ctx or {}).get("lang"))

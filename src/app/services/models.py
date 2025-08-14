import os, yaml
from typing import Any, Dict

class ModelRouter:
    def __init__(self, cfg_path=os.getenv("MODELS_CONFIG", "config/models.yaml")):
        self.cfg = yaml.safe_load(open(cfg_path, "r", encoding="utf-8"))

    # Stubs — call into your existing router(s)
    def chat(self, messages: list[dict], policy_ctx: Dict[str,Any]|None=None) -> dict:
        from app.services.llm_router import run_chat  # your helper
        content = "\n".join([m.get("content","") for m in messages if m.get("role")!="system"])
        return {"text": run_chat(content)}  # minimal glue

    def translate(self, text: str, source: str|None, target: str|None, policy_ctx=None) -> str:
        from app.services.llm_router import run_translate
        return run_translate(text, source, target)

    def asr(self, wav_path: str, diarize=True, policy_ctx=None) -> Dict[str,Any]:
        raise NotImplementedError

    def tts(self, segments: list[dict], voice: dict|str, policy_ctx=None) -> str:
        raise NotImplementedError

    def ocr(self, file_path: str, policy_ctx=None) -> dict:
        raise NotImplementedError

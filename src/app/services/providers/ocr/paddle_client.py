# app/services/providers/ocr/paddle_client.py
import os, httpx
from typing import Any, Dict, Optional
from app.services.providers.base import OCRProvider

class PaddleOCRServer(OCRProvider):
    name = "paddleocr:server"
    def __init__(self, base_url: Optional[str]=None, timeout: int=120):
        self.base = (base_url or os.getenv("PADDLE_OCR_BASE","http://paddle-ocr:9292")).rstrip("/")
        self.timeout = timeout
    def extract(self, file_path: str, lang: Optional[str]=None) -> Dict[str, Any]:
        with open(file_path, "rb") as f:
            files = {"file": (os.path.basename(file_path), f, "application/octet-stream")}
            with httpx.Client(timeout=self.timeout) as c:
                r = c.post(f"{self.base}/ocr", files=files, data={"lang": lang or ""})
                r.raise_for_status()
                return r.json()

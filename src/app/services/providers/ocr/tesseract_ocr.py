# app/services/providers/ocr/tesseract_ocr.py
from __future__ import annotations
import os
from typing import Any, Dict, Optional, List

from PIL import Image
import pytesseract

from app.services.providers.base import OCRProvider


class TesseractOCR(OCRProvider):
    name = "tesseract:cpu"

    def __init__(self):
        cmd = os.getenv("TESSERACT_CMD")
        if cmd:
            pytesseract.pytesseract.tesseract_cmd = cmd

    def extract(self, file_path: str, lang: Optional[str] = None) -> Dict[str, Any]:
        lang = (lang or os.getenv("OCR_LANG") or "eng").split(",")[0]
        img = Image.open(file_path)

        # Better spacing and line handling:
        #  - psm 6: Assume a single uniform block of text
        #  - preserve_interword_spaces: keep spaces
        config = "--psm 6 -c preserve_interword_spaces=1"

        text = pytesseract.image_to_string(img, lang=lang, config=config)

        data = pytesseract.image_to_data(img, lang=lang, config=config,
                                         output_type=pytesseract.Output.DICT)
        words: List[Dict[str, Any]] = []
        n = len(data.get("text", []))
        for i in range(n):
            w = (data["text"][i] or "").strip()
            if not w:
                continue
            conf = data.get("conf", ["-1"])[i]
            try:
                conf_val = float(conf)
            except Exception:
                conf_val = -1.0
            words.append({
                "text": w,
                "conf": conf_val,
                "left": int(data.get("left", [0])[i]),
                "top": int(data.get("top", [0])[i]),
                "width": int(data.get("width", [0])[i]),
                "height": int(data.get("height", [0])[i]),
                "page": int(data.get("page_num", [1])[i]),
            })

        return {"provider": self.name, "lang": lang, "text": text, "words": words}

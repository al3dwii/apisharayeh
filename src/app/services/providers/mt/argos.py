# app/services/providers/mt/argos.py
from __future__ import annotations
from typing import Optional

from app.services.providers.base import MTProvider

try:
    import argostranslate.translate as ATranslate
except Exception:  # pragma: no cover
    ATranslate = None


class ArgosMT(MTProvider):
    """
    Argos Translate provider (offline).
    - No 'auto' source in Argos; we pick source explicitly:
      * if 'source' provided -> use it
      * else -> guess from text (Arabic range => 'ar', else 'en')
    - Requires language packages installed for the pair (en<->ar in your tiny mode).
    """

    name = "argos:mt"

    def __init__(self):
        if ATranslate is None:
            raise RuntimeError("argostranslate is not installed: pip install argostranslate")
        self._langs = ATranslate.get_installed_languages()

    @staticmethod
    def _guess_code(text: str) -> str:
        # crude heuristic: Arabic Unicode block
        for ch in text:
            if "\u0600" <= ch <= "\u06FF":
                return "ar"
        return "en"

    def _get_lang(self, code: str):
        code = (code or "").split("-")[0].lower()
        for lang in self._langs:
            if getattr(lang, "code", "").lower() == code:
                return lang
        return None

    def _find_translation(self, source: str, target: str):
        """Return an Argos translation object from installed langs, or None."""
        from_lang = self._get_lang(source)
        to_lang = self._get_lang(target)
        if not (from_lang and to_lang):
            return None
        try:
            tr = from_lang.get_translation(to_lang)  # returns None if not installed for pair
        except Exception:
            tr = None
        return tr

    def translate(self, text: str, source: Optional[str], target: str) -> str:
        if not text:
            return ""
        if ATranslate is None:
            raise RuntimeError("argostranslate is not installed.")

        tgt = (target or "en").split("-")[0].lower()
        src = (source or "").split("-")[0].lower() or self._guess_code(text)

        # 1) Try requested (or guessed) source → target
        tr = self._find_translation(src, tgt)

        # 2) If not available, try any installed source that has a translation to target
        if tr is None:
            for lang in self._langs:
                maybe = self._find_translation(getattr(lang, "code", ""), tgt)
                if maybe is not None:
                    tr = maybe
                    break

        if tr is None:
            # Help the user with a clear message
            installed = [(getattr(l, "code", ""), getattr(l, "name", "")) for l in self._langs]
            raise RuntimeError(
                f"Argos MT pair not installed for {src}->{tgt}. "
                f"Installed languages: {installed}. "
                "Install the pair with argostranslate.package (en↔ar in tiny mode)."
            )

        return tr.translate(text)

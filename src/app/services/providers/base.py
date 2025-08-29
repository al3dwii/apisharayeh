# app/services/providers/base.py
from typing import Any, Dict, Iterable, List, Optional, Protocol, runtime_checkable

@runtime_checkable
class ASRProvider(Protocol):
    name: str
    def transcribe(self, wav_path: str, diarize: bool = True, lang: Optional[str]=None) -> Dict[str, Any]: ...

@runtime_checkable
class TTSProvider(Protocol):
    name: str
    def synthesize(self, segments: List[Dict[str, Any]], voice: Dict[str, Any] | str,
                   lang: Optional[str]=None, sample_rate: int = 22050) -> str: ...

@runtime_checkable
class OCRProvider(Protocol):
    name: str
    def extract(self, file_path: str, lang: Optional[str]=None) -> Dict[str, Any]: ...

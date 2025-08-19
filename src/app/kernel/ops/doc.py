from __future__ import annotations
from pathlib import Path
from typing import Dict, Any
from zipfile import ZipFile
import xml.etree.ElementTree as ET
from ..errors import ProblemDetails


def detect_type(ctx, path: str) -> Dict[str, Any]:
    p = Path(path)
    if not p.exists():
        raise ProblemDetails(title="Input not found", detail=str(p), code="E_NOT_FOUND", status=400)
    ext = p.suffix.lower().strip(".")
    kind = ext or "txt"
    ctx.emit("tool_used", {"name": "doc.detect_type", "args": {"kind": kind}})
    return {"kind": kind}


def parse_txt(ctx, path: str) -> Dict[str, Any]:
    try:
        text = Path(path).read_text(encoding="utf-8", errors="ignore")
    except Exception as e:
        raise ProblemDetails(title="Read error", detail=str(e), code="E_READ", status=400)
    ctx.emit("tool_used", {"name": "doc.parse_txt", "args": {"bytes": len(text.encode('utf-8'))}})
    return {"text": text}


def parse_docx(ctx, path: str) -> Dict[str, Any]:
    """
    Minimal DOCX reader (no external deps):
    - unzip
    - read word/document.xml
    - extract text from <w:t> nodes
    """
    p = Path(path)
    if not p.exists():
        raise ProblemDetails(title="Input not found", detail=str(p), code="E_NOT_FOUND", status=400)

    try:
        with ZipFile(p, "r") as z:
            xml_bytes = z.read("word/document.xml")
    except KeyError:
        raise ProblemDetails(title="DOCX invalid", detail="word/document.xml not found", code="E_DOCX_INVALID", status=400)
    except Exception as e:
        raise ProblemDetails(title="DOCX read error", detail=str(e), code="E_DOCX_LIB", status=400)

    try:
        ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
        root = ET.fromstring(xml_bytes)
        texts = []
        for t in root.findall(".//w:t", ns):
            texts.append(t.text or "")
        # Join with line breaks at paragraph boundaries
        # paragraphs are <w:p>
        paras = []
        for pnode in root.findall(".//w:p", ns):
            buf = []
            for t in pnode.findall(".//w:t", ns):
                buf.append(t.text or "")
            para_text = "".join(buf).strip()
            if para_text:
                paras.append(para_text)
        content = "\n".join(paras) if paras else "\n".join(texts)
    except Exception as e:
        raise ProblemDetails(title="DOCX parse error", detail=str(e), code="E_DOCX_PARSE", status=400)

    ctx.emit("tool_used", {"name": "doc.parse_docx", "args": {"chars": len(content)}})
    return {"text": content}

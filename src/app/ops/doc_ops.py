from typing import List, Dict, Any

def parse_docx_outline(path: str, max_slides: int = 12) -> Dict[str, Any]:
    """
    Parse a DOCX into a simple outline structure.

    Be robust to templated strings (e.g., "8") coming from the DSL by
    force-casting max_slides to int.
    """
    # Import inside to avoid module import issues at import time
    from app.tools.office_io import docx_to_outline

    try:
        max_slides = int(max_slides) if max_slides is not None else 12
    except (ValueError, TypeError):
        max_slides = 12

    outline = docx_to_outline(path, max_slides=max_slides)
    return {"outline": outline}

def parse_pdf_text(path: str) -> Dict[str, List[str]]:
    # Placeholder; implement later
    return {"pages": []}

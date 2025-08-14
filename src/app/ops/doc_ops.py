from typing import List, Dict, Any

def parse_docx_outline(path: str, max_slides: int = 12) -> Dict[str, Any]:
    # Import inside to avoid module import errors
    from app.tools.office_io import docx_to_outline
    outline = docx_to_outline(path, max_slides=max_slides)
    return {"outline": outline}

def parse_pdf_text(path: str) -> Dict[str, List[str]]:
    # Placeholder; implement later
    return {"pages": []}

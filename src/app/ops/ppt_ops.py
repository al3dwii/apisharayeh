from app.tools.office_io import outline_to_pptx   # reuse your code
from app.services.artifacts import save_file

def build_from_outline(outline: list, title: str):
    out_path = outline_to_pptx(outline, title=title)
    saved = save_file(out_path, ".pptx")
    return {"url": saved["url"], "path": saved["path"]}

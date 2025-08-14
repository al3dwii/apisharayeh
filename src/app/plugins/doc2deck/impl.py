from app.services.models import ModelRouter
from app.services.tools import ToolRouter
from app.ops import doc_ops, ppt_ops, io_ops

def run(inputs, ctx):
    tmp = io_ops.fetch(url=inputs["source_url"])
    pages = doc_ops.parse_pdf_text(path=tmp["file"])["pages"]
    text = "\n\n".join(pages[:30])
    outline_text = ctx.models.chat([
        {"role":"system","content":"Condense into slide outline"},
        {"role":"user","content": text}
    ])["text"]
    outline = [{"title": f"Slide {i+1}", "bullets": [line.strip()]} for i, line in enumerate(outline_text.split("\n")) if line.strip()][:inputs.get("max_slides",6)]
    ppt = ppt_ops.build_from_outline(outline=outline, title=inputs["title"])
    return {"pptx_url": ppt["url"]}

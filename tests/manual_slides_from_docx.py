# Run: DEV_OFFLINE=true python -m tests.manual_slides_from_docx
from __future__ import annotations
import os, uuid, io, zipfile
from pathlib import Path

os.environ.setdefault("DEV_OFFLINE", "true")
os.environ.setdefault("ARTIFACTS_DIR", "./artifacts")
os.environ.setdefault("PUBLIC_BASE_URL", "")

from app.kernel.context import ExecutionContext
from app.kernel.events import EventEmitter
from app.kernel.bootstrap_toolrouter import build_toolrouter
from app.kernel.storage import project_root

def make_min_docx(path: Path, lines):
    # Create a tiny DOCX with given text lines as paragraphs
    document_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        + "".join(f'<w:p><w:r><w:t>{line}</w:t></w:r></w:p>' for line in lines)
        + '</w:document>'
    ).encode("utf-8")
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml",
                   '<?xml version="1.0" encoding="UTF-8"?>'
                   '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                   '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                   '<Default Extension="xml" ContentType="application/xml"/>'
                   '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
                   '</Types>')
        z.writestr("_rels/.rels",
                   '<?xml version="1.0" encoding="UTF-8"?>'
                   '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
                   '</Relationships>')
        z.writestr("word/_rels/document.xml.rels",
                   '<?xml version="1.0" encoding="UTF-8"?>'
                   '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"/>')
        z.writestr("word/document.xml", document_xml)

def print_event(event_type, payload):
    print(f"[{event_type}] {payload}")

def main():
    job_id = f"job_{uuid.uuid4().hex[:8]}"
    project_id = f"prj_{uuid.uuid4().hex[:8]}"
    emitter = EventEmitter(on_emit=print_event)

    ctx = ExecutionContext(
        job_id=job_id,
        project_id=project_id,
        permissions={"fs_write", "fs_read"},
        env={"DEV_OFFLINE": True},
        emitter=emitter,
    )
    tr = build_toolrouter()

    # Prepare a tiny DOCX locally
    tmp_dir = Path("./_tmp"); tmp_dir.mkdir(exist_ok=True)
    docx_path = tmp_dir / "sample.docx"
    make_min_docx(docx_path, [
        "التعليم الإلكتروني",
        "التعريف",
        "الأنواع",
        "المنصات والأدوات",
        "الفوائد",
        "التحديات",
        "الذكاء الاصطناعي",
        "الخاتمة",
    ])

    # 1) Copy the DOCX into project artifacts via io.fetch
    f = tr.call("io.fetch", ctx, url=f"file://{docx_path.resolve()}", to_dir="input")
    in_path = f["path"]

    # 2) Detect + parse
    kind = tr.call("doc.detect_type", ctx, path=in_path)["kind"]
    if kind != "docx":
        raise RuntimeError(f"Expected docx, got {kind}")
    text = tr.call("doc.parse_docx", ctx, path=in_path)["text"]

    # 3) Outline from doc
    outline = tr.call("slides.outline.from_doc", ctx, text=text, language="ar", count=12)["outline"]

    # 4) Image pack (fixtures/placeholders)
    imgs = tr.call("vision.images.from_fixtures", ctx, queries=["موضوع","أدوات","فوائد","تحديات"], project_id=project_id)["images"]

    # 5) Render
    slides = tr.call("slides.html.render", ctx, project_id=project_id, outline=outline, theme="academic-ar", images=imgs)["slides_html"]

    # 6) Export PDF
    pdf = tr.call("slides.export.pdf", ctx, project_id=project_id, slides_html=slides)["pdf_url"]

    print("\nArtifacts root:", project_root(project_id))
    print("PDF:", pdf)

if __name__ == "__main__":
    main()

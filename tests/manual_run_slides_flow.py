# Run:
#   export DEV_OFFLINE=true
#   python -m tests.manual_run_slides_flow prompt
#   python -m tests.manual_run_slides_flow docx
from __future__ import annotations
import os
import sys
from pathlib import Path

os.environ.setdefault("DEV_OFFLINE", "true")
os.environ.setdefault("ARTIFACTS_DIR", "./artifacts")
os.environ.setdefault("PUBLIC_BASE_URL", "")

from app.kernel.service_runner import ServiceRunner

PLUGINS_ROOT = Path("./plugins")

def on_event(event_type, payload):
    print(f"[{event_type}] {payload}")

def ensure_minimal_plugin():
    """
    Ensures plugins/slides.generate exists with manifest/flow (from Step 2).
    If you already created them, this does nothing.
    """
    man = PLUGINS_ROOT / "slides.generate" / "manifest.yaml"
    flow = PLUGINS_ROOT / "slides.generate" / "flow.yaml"
    if man.exists() and flow.exists():
        return
    # Write a minimal version compatible with our DSL runner
    (PLUGINS_ROOT / "slides.generate").mkdir(parents=True, exist_ok=True)
    man.write_text("""\
id: slides.generate
name: "Slides: Generate Presentation"
version: "0.3.0"
category: slides
runtime: dsl
entrypoint: ./flow.yaml
permissions:
  fs_read: true
  fs_write: true
inputs:
  type: object
  required: ["source","language"]
  properties: {}
events:
  todos:
    - "تجهيز المحتوى"
    - "تخطيط الهيكل"
    - "إنشاء الشرائح"
    - "مراجعة وتصدير"
""", encoding="utf-8")
    flow.write_text("""\
version: 1
vars:
  project_id: "{{ inputs.project_id | default(random_id('prj_')) }}"
  slides_count: "{{ inputs.slides_count | default(12) }}"
  queries: "{{ inputs.images_query_pack | default(['موضوع','أدوات','فوائد','تحديات']) }}"
nodes:
  - id: source_branch
    op: switch
    when:
      "{{ inputs.source == 'prompt' }}":
        op: slides.outline.from_prompt_stub
        in:
          topic: "{{ inputs.topic }}"
          language: "{{ inputs.language }}"
          count: "{{ vars.slides_count }}"
        out:
          outline: "@outline"
      "{{ inputs.source == 'docx' }}":
        op: sequence
        do:
          - op: io.fetch
            in:
              url: "{{ inputs.docx_url }}"
              to_dir: "{{ vars.project_id }}/input"
            out:
              path: "@docx_path"
          - op: doc.detect_type
            in: { path: "@docx_path" }
            out: { kind: "@kind" }
          - op: doc.parse_docx
            in: { path: "@docx_path" }
            out: { text: "@text" }
          - op: slides.outline.from_doc
            in:
              text: "@text"
              language: "{{ inputs.language }}"
              count: "{{ vars.slides_count }}"
            out:
              outline: "@outline"
  - id: images
    op: vision.images.from_fixtures
    in:
      queries: "{{ vars.queries }}"
      project_id: "{{ vars.project_id }}"
    out:
      images: "@images"
  - id: render
    op: slides.html.render
    needs: [source_branch, images]
    in:
      project_id: "{{ vars.project_id }}"
      outline: "@outline"
      theme: "{{ inputs.theme | default('academic-ar') }}"
      images: "@images"
    out:
      slides_html: "@slides_html"
  - id: export_pdf
    op: slides.export.pdf
    needs: [render]
    in:
      project_id: "{{ vars.project_id }}"
      slides_html: "@slides_html"
    out:
      pdf_url: "@pdf_url"
outputs:
  outline: "@outline"
  slides_html: "@slides_html"
  pdf_url: "@pdf_url"
""", encoding="utf-8")

def main():
    ensure_minimal_plugin()
    mode = (sys.argv[1] if len(sys.argv) > 1 else "prompt").strip()

    runner = ServiceRunner(PLUGINS_ROOT)

    if mode == "prompt":
        inputs = {
            "source": "prompt",
            "topic": "التعليم الإلكتروني",
            "language": "ar",
            "slides_count": 12,
        }
    elif mode == "docx":
        # create a tiny docx on the fly
        import zipfile, uuid
        tmp = Path("./_tmp"); tmp.mkdir(exist_ok=True)
        docx_path = tmp / f"sample_{uuid.uuid4().hex[:6]}.docx"
        document_xml = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            '<w:p><w:r><w:t>التعليم الإلكتروني</w:t></w:r></w:p>'
            '<w:p><w:r><w:t>التعريف</w:t></w:r></w:p>'
            '<w:p><w:r><w:t>الأنواع</w:t></w:r></w:p>'
            '<w:p><w:r><w:t>المنصات والأدوات</w:t></w:r></w:p>'
            '</w:document>'
        ).encode("utf-8")
        with zipfile.ZipFile(docx_path, "w", zipfile.ZIP_DEFLATED) as z:
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
        inputs = {
            "source": "docx",
            "docx_url": f"file://{docx_path.resolve()}",
            "language": "ar",
            "slides_count": 10,
        }
    else:
        print("Usage: python -m tests.manual_run_slides_flow [prompt|docx]")
        return

    outputs = runner.run("slides.generate", inputs, on_event=on_event)
    print("\nFinal outputs:", outputs)

if __name__ == "__main__":
    main()

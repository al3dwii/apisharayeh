# Run: DEV_OFFLINE=true python -m tests.manual_slides_from_prompt
from __future__ import annotations
import os, uuid
os.environ.setdefault("DEV_OFFLINE", "true")
os.environ.setdefault("ARTIFACTS_DIR", "./artifacts")
os.environ.setdefault("PUBLIC_BASE_URL", "")

from app.kernel.context import ExecutionContext
from app.kernel.events import EventEmitter
from app.kernel.bootstrap_toolrouter import build_toolrouter
from app.kernel.storage import project_root

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

    # 1) Outline from prompt (Arabic)
    o = tr.call("slides.outline.from_prompt_stub", ctx, topic="التعليم الإلكتروني", language="ar", count=12)
    outline = o["outline"]

    # 2) Image pack (fixtures or placeholders)
    imgs = tr.call("vision.images.from_fixtures", ctx, queries=["online learning","LMS","AI","stats"], project_id=project_id)["images"]

    # 3) Render HTML slides
    slides = tr.call("slides.html.render", ctx, project_id=project_id, outline=outline, theme="academic-ar", images=imgs)["slides_html"]

    # 4) Export stub PDF
    pdf = tr.call("slides.export.pdf", ctx, project_id=project_id, slides_html=slides)["pdf_url"]

    print("\nArtifacts root:", project_root(project_id))
    print("PDF:", pdf)

if __name__ == "__main__":
    main()
